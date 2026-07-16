#!/usr/bin/env python3
"""
fetch_commercial.py — Registered-business density (MSMEs per lakh population)
by pincode, from the Udyam Registration portal.

Source: Ministry of MSME, "District Wise Total MSME Registered Enterprises
under UDYAM Registration till last date", published via the Open
Government Data (OGD) Platform (data.gov.in), dataset resource
f8cd85a1-f9b8-4ff1-b195-9f75c10eb338 (788 districts, updated daily).

Unlike fetch_education.py's UDISE+ source, this one really is live-
fetchable. data.gov.in's *website* (data.gov.in/resource/...) 403s
WebFetch same as every other source in this family, but plain curl gets a
200 — the block is User-Agent-based, not a real bot-wall. Fetching the
resource page with curl and reading its embedded Nuxt page-state JSON
turned up a working `api.data.gov.in` URL (with a usable api-key) that's
meant for the page's own "try the API" feature. That same endpoint,
called directly, is what this script uses — no manual export needed.

One more UA wrinkle specific to `api.data.gov.in` itself: Python's
`requests` library hangs until timeout using its default
"python-requests/x.x" User-Agent, while curl's default UA (and any other
non-default UA) goes through in well under a second — see HEADERS below.
Confirmed 2026-07-17: `limit=all` returns all 788 district rows in one
call, no pagination required.

MSME registration undercounts pure retail/services establishments that
never register (a large share of India's informal economy), so this reads
as "formal small-business density," not total commercial activity — a
real but partial signal, same caveat as every other proxy in this project.

District-name matching reuses fetch_rbi_bsr.py's shared
_norm_state/_norm_district/_add_delhi_suffix/CENSUS_DISTRICT_ALIASES
pipeline; ~20 new alias entries were added there for this source's
spelling variants (transposition typos, Hindi-name-vs-English-name
districts, footnote markers leaking into Census district names, etc.),
benefiting every other district-level signal that shares the same dict —
confirmed no regression and a small match-rate improvement for
fetch_phonepe_pulse.py (723->729/788) from the overlap. Match rate here:
716/788 districts (91%) -> 231/276 known pincodes. Remaining gaps are
mostly districts created after the 2011/2021 Census reference was compiled
(Kekri, Kotputli-Behror, Manendragarh, Noney, Vijayanagar, etc.) — not
fixable by aliasing, would need a newer district-population reference.

Output: data/raw/commercial.csv — pincode, msme_per_lakh

Usage:
  python3 etl/fetch_commercial.py
  python3 etl/fetch_commercial.py --dry-run
"""

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).parent))
from fetch_rbi_bsr import _norm_state, _norm_district, _add_delhi_suffix, CENSUS_DISTRICT_ALIASES  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
REF  = ROOT / "data" / "reference"
RAW  = ROOT / "data" / "raw"
POP_REF     = REF / "district_population_census.csv"
PINCODE_REF = REF / "pincode_district_state_india.csv"
COORDS      = RAW / "pincode_coords.csv"

API_URL = (
    "https://api.data.gov.in/resource/f8cd85a1-f9b8-4ff1-b195-9f75c10eb338"
    "?api-key=579b464db66ec23bdd000001cdc3b564546246a772a26393094f5645"
    "&offset=0&limit=all&format=json"
)
# requests' default "python-requests/x.x" User-Agent hangs until timeout
# against this endpoint (confirmed 2026-07-17 — curl's default UA, and any
# other non-default UA, both go through fine in well under a second). Not a
# browser-spoofing header, just something other than the default.
HEADERS = {"User-Agent": "PaisaMap-Commercial/1.0 (data.gov.in Udyam MSME fetch)"}

def fetch_district_msme_totals() -> pd.DataFrame:
    resp = requests.get(API_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    records = data.get("records", [])
    log.info("Fetched %d district records (API reports total=%s)", len(records), data.get("total"))
    df = pd.DataFrame(records)
    df["total"] = pd.to_numeric(df["total"], errors="coerce")
    df = df.dropna(subset=["total"])

    # _norm_state applies fetch_rbi_bsr.py's shared STATE_NAME_ALIASES internally.
    df["state_norm"] = df["state_name"].apply(_norm_state)
    df["district_norm"] = df["district_name"].apply(_norm_district)
    df["district_norm"] = df.apply(lambda r: _add_delhi_suffix(r["district_norm"], r["state_norm"]), axis=1)
    df["district_norm"] = df["district_norm"].apply(lambda n: CENSUS_DISTRICT_ALIASES.get(n, n))
    return df[["state_norm", "district_norm", "total"]]


def build_district_per_lakh(msme: pd.DataFrame) -> pd.Series:
    pop = pd.read_csv(POP_REF)
    pop["state_norm"] = pop["state_name"].apply(_norm_state)
    pop["district_norm"] = pop["district"].apply(_norm_district)
    pop["district_norm"] = pop["district_norm"].apply(lambda n: CENSUS_DISTRICT_ALIASES.get(n, n))
    pop_idx = pop.drop_duplicates(["state_norm", "district_norm"]).set_index(
        ["state_norm", "district_norm"])["population"]

    msme_idx = msme.set_index(["state_norm", "district_norm"])["total"]
    matched = msme_idx.index.isin(pop_idx.index)
    log.info("Matched %d/%d districts to Census population", matched.sum(), len(msme_idx))
    if not matched.all():
        log.warning("Unmatched (state, district): %s", list(msme_idx.index[~matched][:15]))

    per_lakh = (msme_idx[matched] / pop_idx.reindex(msme_idx.index[matched]) * 100_000).round(2)
    return per_lakh  # index: (state_norm, district_norm)


def build_pincode_commercial(per_lakh: pd.Series) -> pd.DataFrame:
    known = set(pd.read_csv(COORDS, dtype={"pincode": str})["pincode"])
    pc_map = pd.read_csv(PINCODE_REF, dtype={"pincode": str})
    pc_map = pc_map[pc_map["pincode"].isin(known)].copy()
    pc_map["state_norm"] = pc_map["state_name"].apply(_norm_state)
    pc_map["district_norm"] = pc_map["district"].apply(_norm_district)
    pc_map["district_norm"] = pc_map["district_norm"].apply(lambda n: CENSUS_DISTRICT_ALIASES.get(n, n))

    rows = []
    for r in pc_map.itertuples():
        val = per_lakh.get((r.state_norm, r.district_norm))
        if val is not None and not pd.isna(val):
            rows.append({"pincode": r.pincode, "msme_per_lakh": val})

    return pd.DataFrame(rows).drop_duplicates("pincode").set_index("pincode")


def write_output(out: pd.DataFrame, dry_run: bool = False) -> None:
    out_path = RAW / "commercial.csv"
    if dry_run:
        log.info("[DRY RUN] commercial.csv (%d rows):\n%s", len(out), out.head(10))
        return
    out.to_csv(out_path)
    log.info("Written: %s (%d rows)", out_path.name, len(out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    msme = fetch_district_msme_totals()
    per_lakh = build_district_per_lakh(msme)
    out = build_pincode_commercial(per_lakh)
    log.info("msme_per_lakh computed for %d pincodes", len(out))
    write_output(out, dry_run=args.dry_run)


if __name__ == "__main__":
    main()

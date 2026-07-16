"""
fetch_phonepe_pulse.py — District-level UPI transaction activity from PhonePe Pulse.

Source: https://github.com/PhonePe/pulse (CDLA-Permissive-2.0, openly
licensed, no auth/signup). Confirmed 2026-07-16 by direct inspection: the
"hover" map endpoint is exhaustive district-level quarterly transaction
count + value for all 36 states/UTs (31/31 real Karnataka districts
checked, not a top-N sample) — unlike the "top" endpoint (also in this
repo), which is only a top-10 leaderboard per state and not used here. The
repo appears frozen at 2024 Q4 (no later quarters found); this fetches
that most recent quarter.

This is a distinct signal from bank_deposits.csv's balance-sheet-based
proxies (deposits_per_capita, credit_deposit_ratio): those reflect static
holdings, this reflects observed transaction *flow* — a direct read on
local economic/commerce activity.

Feature computed: upi_txn_value_per_capita — district's 2024 Q4 total UPI
transaction value (RBI/PhonePe report this in rupees, not crore — see
raw JSON) divided by Census district population, mapped onto every
currently-known pincode via its district (data/reference/
pincode_district_state_india.csv). District matching reuses
fetch_rbi_bsr.py's _norm_state/_norm_district/CENSUS_DISTRICT_ALIASES so
a district that already matches Census population for bank_branches_per_lakh
matches here too.

Output: data/raw/upi_activity.csv — pincode, upi_txn_value_per_capita

Usage:
  python3 etl/fetch_phonepe_pulse.py
  python3 etl/fetch_phonepe_pulse.py --dry-run
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

PULSE_STATE_LIST_URL = (
    "https://api.github.com/repos/PhonePe/pulse/contents/"
    "data/map/transaction/hover/country/india/state"
)
PULSE_RAW_TMPL = (
    "https://raw.githubusercontent.com/PhonePe/pulse/master/"
    "data/map/transaction/hover/country/india/state/{slug}/{year}/{quarter}.json"
)
YEAR, QUARTER = 2024, 4  # most recent quarter available in the repo as of 2026-07-16
HEADERS = {"User-Agent": "PaisaMap-PulseUPI/1.0 (one-time reference fetch)"}


def fetch_state_slugs() -> list[str]:
    resp = requests.get(PULSE_STATE_LIST_URL, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return [item["name"] for item in resp.json() if item["type"] == "dir"]


def fetch_district_amounts(slug: str) -> list[dict]:
    """One state's district list for YEAR/QUARTER: [{district, amount}, ...]."""
    url = PULSE_RAW_TMPL.format(slug=slug, year=YEAR, quarter=QUARTER)
    resp = requests.get(url, headers=HEADERS, timeout=20)
    if resp.status_code != 200:
        log.warning("  %s: HTTP %d, skipped", slug, resp.status_code)
        return []
    hover = resp.json().get("data", {}).get("hoverDataList", [])
    rows = []
    for entry in hover:
        amount = 0.0
        for m in entry.get("metric", []):
            if m.get("type") == "TOTAL":
                amount = m.get("amount", 0.0)
        rows.append({"district_raw": entry["name"], "amount": amount})
    return rows


def _norm_pulse_district(name: str, state_norm: str = "") -> str:
    # Hover endpoint district names are "<name> district" — strip that suffix
    # before applying the shared _norm_district/CENSUS_DISTRICT_ALIASES
    # pipeline, same normalized space bank_branches_per_lakh matches against.
    n = _norm_district(name)
    if n.endswith(" DISTRICT"):
        n = n[: -len(" DISTRICT")]
    n = _add_delhi_suffix(n, state_norm)
    return CENSUS_DISTRICT_ALIASES.get(n, n)


def build_district_per_capita() -> pd.Series:
    """(state_norm, district_norm) -> upi_txn_value_per_capita for 2024 Q4."""
    slugs = fetch_state_slugs()
    log.info("Fetched %d state directories from PhonePe Pulse", len(slugs))

    all_rows = []
    for slug in slugs:
        state_name = slug.replace("-", " ").replace("&", "and")
        state_norm = _norm_state(state_name)
        for r in fetch_district_amounts(slug):
            all_rows.append({
                "state_norm": state_norm,
                "district_norm": _norm_pulse_district(r["district_raw"], state_norm),
                "amount": r["amount"],
            })
    txn = pd.DataFrame(all_rows)
    log.info("PhonePe Pulse %d-Q%d: %d (state, district) rows across %d states",
              YEAR, QUARTER, len(txn), txn["state_norm"].nunique())

    pop = pd.read_csv(POP_REF)
    pop["state_norm"] = pop["state_name"].apply(_norm_state)
    pop["district_norm"] = pop["district"].apply(_norm_district)
    pop["district_norm"] = pop["district_norm"].apply(lambda n: CENSUS_DISTRICT_ALIASES.get(n, n))
    pop_idx = pop.drop_duplicates(["state_norm", "district_norm"]).set_index(
        ["state_norm", "district_norm"])["population"]

    txn = txn.set_index(["state_norm", "district_norm"])
    matched = txn.index.isin(pop_idx.index)
    log.info("Matched %d/%d districts to Census population (state+district key)",
              matched.sum(), len(txn))

    per_capita = (txn.loc[matched, "amount"] / pop_idx.reindex(txn.index[matched])).round(2)
    return per_capita  # index: (state_norm, district_norm)


def build_pincode_upi_activity(per_capita: pd.Series) -> pd.DataFrame:
    if not (PINCODE_REF.exists() and COORDS.exists()):
        log.info("Pincode UPI backfill skipped — missing pincode/coords reference")
        return pd.DataFrame()

    known = set(pd.read_csv(COORDS, dtype={"pincode": str})["pincode"])
    pc_map = pd.read_csv(PINCODE_REF, dtype={"pincode": str})
    pc_map = pc_map[pc_map["pincode"].isin(known)]
    pc_map["state_norm"] = pc_map["state_name"].apply(_norm_state)
    pc_map["district_norm"] = pc_map["district"].apply(_norm_district)
    pc_map["district_norm"] = pc_map["district_norm"].apply(lambda n: CENSUS_DISTRICT_ALIASES.get(n, n))

    rows = []
    for r in pc_map.itertuples():
        key = (r.state_norm, r.district_norm)
        val = per_capita.get(key)
        if val is not None and not pd.isna(val):
            rows.append({"pincode": r.pincode, "upi_txn_value_per_capita": val})

    return pd.DataFrame(rows).drop_duplicates("pincode").set_index("pincode")


def write_output(upi: pd.DataFrame, dry_run: bool = False) -> None:
    out_path = RAW / "upi_activity.csv"
    if dry_run:
        log.info("[DRY RUN] upi_activity.csv (%d rows):\n%s", len(upi), upi.head(10))
        return
    upi.to_csv(out_path)
    log.info("Written: %s (%d rows)", out_path.name, len(upi))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    per_capita = build_district_per_capita()
    upi = build_pincode_upi_activity(per_capita)
    log.info("upi_txn_value_per_capita computed for %d pincodes", len(upi))
    write_output(upi, dry_run=args.dry_run)


if __name__ == "__main__":
    main()

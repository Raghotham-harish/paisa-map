#!/usr/bin/env python3
"""
fetch_economic.py — Per-capita income by pincode, from the RBI Handbook of
Statistics on Indian States, Table 19: Per Capita Net State Domestic Product
(Current Prices).

Source: rbi.org.in/Scripts/AnnualPublications.aspx?head=Handbook+of+Statistics+on+Indian+States
-> "STATE DOMESTIC PRODUCT" section -> Table 19. Direct download is bot-walled
(same F5/TSPD JS challenge as every other rbidocs.rbi.org.in file used so far
in this project) so the user downloaded it manually via browser, same proven
pattern as the industrial/education signals.

Two sheets, split by year range: T_19(i) covers 2011-12 to 2016-17, T_19(ii)
covers 2017-18 to 2024-25. Only T_19(ii) is used. Latest year with complete
coverage across all 34 listed states/UTs is 2022-23 — 2023-24 and 2024-25 both
have several states marked "-" (not yet published), so using those would silently
drop states rather than give current numbers. This mirrors fetch_industrial.py's
own ASI data landing on 2022-23 as the latest complete year, coincidentally the
same vintage.

nsdp_per_capita is already a per-capita figure (₹ per person per year) — no
further Census population normalization needed, unlike factories_per_lakh or
schools_per_lakh which start as raw counts.

State-level only (no district breakdown in this source) — applied uniformly
to every pincode in a state, same approach as fetch_agriculture.py and
fetch_industrial.py. Missing from this table entirely (no row at all, not
even a "-"): Lakshadweep and Dadra & Nagar Haveli and Daman & Diu — those
pincodes get no value, same documented gap as fetch_industrial.py hit for
Lakshadweep. Ladakh has data here but isn't a state pincode_district_state_india.csv
recognizes, so it's parsed but unused.

Output: data/raw/economic.csv — pincode, nsdp_per_capita

Usage:
  python3 etl/fetch_economic.py --xlsx /path/to/19T_....XLSX
  python3 etl/fetch_economic.py --xlsx /path/to/19T_....XLSX --dry-run
"""

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from fetch_rbi_bsr import _norm_state  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
REF  = ROOT / "data" / "reference"
RAW  = ROOT / "data" / "raw"
PINCODE_REF = REF / "pincode_district_state_india.csv"
COORDS      = RAW / "pincode_coords.csv"

SHEET = "T_19(ii)"
YEAR_HEADER_ROW = 4
LATEST_YEAR_COL = "2022-23"

# Aliases specific to this source's spelling — one-off typo, same fix
# fetch_industrial.py needed, kept local rather than joining the shared
# STATE_NAME_ALIASES in fetch_rbi_bsr.py.
STATE_ALIASES = {
    "CHATTISGARH": "CHHATTISGARH",
}


def _canon_state(name: str) -> str:
    return STATE_ALIASES.get(_norm_state(name), _norm_state(name))


def load_state_nsdp_per_capita(xlsx_path: Path) -> pd.Series:
    """state_norm -> per-capita NSDP (current prices, ₹), 2022-23."""
    df = pd.read_excel(xlsx_path, sheet_name=SHEET, header=None)

    header_row = df.iloc[YEAR_HEADER_ROW]
    matches = header_row[header_row == LATEST_YEAR_COL].index
    if len(matches) == 0:
        raise ValueError(f"Could not find column {LATEST_YEAR_COL!r} in header row {YEAR_HEADER_ROW}")
    year_col = matches[0]

    data = df.iloc[YEAR_HEADER_ROW + 1:].copy()
    data["state_norm"] = data[1].apply(lambda s: _canon_state(s) if pd.notna(s) else None)
    data[year_col] = pd.to_numeric(data[year_col], errors="coerce")
    data = data.dropna(subset=["state_norm", year_col])

    log.info("Parsed NSDP per capita for %d states/UTs (%s)", len(data), LATEST_YEAR_COL)
    return data.set_index("state_norm")[year_col].rename("nsdp_per_capita")


def build_pincode_output(state_nsdp: pd.Series) -> pd.DataFrame:
    known = set(pd.read_csv(COORDS, dtype={"pincode": str})["pincode"])
    pc_map = pd.read_csv(PINCODE_REF, dtype={"pincode": str})
    pc_map = pc_map[pc_map["pincode"].isin(known)].copy()
    pc_map["state_norm"] = pc_map["state_name"].apply(_canon_state)

    matched_states = set(state_nsdp.index) & set(pc_map["state_norm"].unique())
    log.info("Matched %d/%d states with pincode coverage", len(matched_states), pc_map["state_norm"].nunique())
    unmatched = set(pc_map["state_norm"].unique()) - set(state_nsdp.index)
    if unmatched:
        log.warning("No NSDP data for states in pincode coverage: %s", sorted(unmatched))

    pc_map["nsdp_per_capita"] = pc_map["state_norm"].map(state_nsdp)
    out = pc_map.dropna(subset=["nsdp_per_capita"])[["pincode", "nsdp_per_capita"]]
    return out.drop_duplicates("pincode").set_index("pincode")


def write_output(out: pd.DataFrame, dry_run: bool = False) -> None:
    out_path = RAW / "economic.csv"
    if dry_run:
        log.info("[DRY RUN] economic.csv (%d rows):\n%s", len(out), out.head(10))
        return
    out.to_csv(out_path)
    log.info("Written: %s (%d rows)", out_path.name, len(out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", required=True, help="RBI Handbook Table 19 XLSX — see module docstring")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    state_nsdp = load_state_nsdp_per_capita(Path(args.xlsx))
    out = build_pincode_output(state_nsdp)
    log.info("nsdp_per_capita computed for %d pincodes", len(out))
    write_output(out, dry_run=args.dry_run)


if __name__ == "__main__":
    main()

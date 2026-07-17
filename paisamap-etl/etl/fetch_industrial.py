#!/usr/bin/env python3
"""
fetch_industrial.py — Factories per lakh population by pincode, from the
Annual Survey of Industries (ASI), state-wise factory counts 2022-23.

Source: Rajya Sabha Unstarred Question No. 2397 (266th session, answered
16 Dec 2024), Ministry of Statistics and Programme Implementation —
downloaded as RS_Session_266_AU_2397_1.csv via data.gov.in's resource
"State/UT-wise Total Number of Factories as per the Published Reports of
ASI Data from 2018-19 to 2022-23". Real 35-state/UT coverage including
Gujarat, Karnataka, Tamil Nadu, UP, West Bengal — the industrial
heavyweights. Sanity-checked against a known fact: Tamil Nadu comes out
highest (39,666 factories in 2022-23), matching ASI 2023-24's widely
reported result that Tamil Nadu tops the factory count.

This was chosen over two other candidates that turned out not to work:
- RBI Handbook of Statistics on Indian States, Table 116 (same title,
  "State-wise Number of Factories") only covers 20 of 36 states/UTs —
  missing Gujarat, Karnataka, Tamil Nadu, UP, West Bengal, Delhi entirely.
  Worse, spot-checking its later-year rows (2016-17 on) against this RS
  source found the SAME numbers appearing under DIFFERENT state column
  headers (e.g. Table 116's 2018-19 row has 13789 under its "Daman & Diu"
  column, but 13789 is this RS source's Karnataka figure for that year) —
  a real column/header misalignment in the RBI file, not just a coverage
  gap. Not used.
- Dataful.in's matching dataset (36 states, 1990-91 to 2023-24) looked
  best on paper but its download is gated behind a paid/signed-in export
  (₹299) — this free RS source was tried first and turned out sufficient.

State-level only (no district breakdown in this source) — applied
uniformly to every pincode in a state, same approach as
fetch_agriculture.py. Normalized to factories_per_lakh using Census state
population (aggregated from district_population_census.csv), same pattern
as fetch_rbi_bsr.py's backfill_deposits_state_level().

Output: data/raw/industrial.csv — pincode, factories_per_lakh

Usage:
  python3 etl/fetch_industrial.py --csv /path/to/RS_Session_266_AU_2397_1.csv
  python3 etl/fetch_industrial.py --csv /path/to/RS_Session_266_AU_2397_1.csv --dry-run
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
POP_REF     = REF / "district_population_census.csv"
PINCODE_REF = REF / "pincode_district_state_india.csv"
COORDS      = RAW / "pincode_coords.csv"

LATEST_YEAR_COL = "2022-23"

# Aliases specific to this source's spelling (typos in the RS answer's own
# transcription) and to bridge pincode_district_state_india.csv's own
# "Chattisgarh" typo against Census's correctly-spelled "Chhattisgarh" —
# applied identically to source, population, and pincode-reference state
# names below so all three land on the same canonical key. (No live
# pincodes currently fall in Chhattisgarh anyway — see enrichment
# coverage — so this doesn't change today's output, but keeps the join
# correct once/if that coverage grows.)
STATE_ALIASES = {
    "CHATTISGARH": "CHHATTISGARH",
    "DADRA AND NICOBAR HAVELI AND DAMAN AND DIU": "DADRA AND NAGAR HAVELI AND DAMAN AND DIU",
}


def _canon_state(name: str) -> str:
    return STATE_ALIASES.get(_norm_state(name), _norm_state(name))


def load_state_factory_counts(csv_path: Path) -> pd.Series:
    """state_norm -> factory count, latest available year (2022-23)."""
    df = pd.read_csv(csv_path)
    df = df[df["State/UT"] != "All India"].copy()
    df["state_norm"] = df["State/UT"].apply(_canon_state)
    df[LATEST_YEAR_COL] = pd.to_numeric(df[LATEST_YEAR_COL], errors="coerce")
    df = df.dropna(subset=[LATEST_YEAR_COL])
    log.info("Parsed factory counts for %d states/UTs (%s)", len(df), LATEST_YEAR_COL)
    return df.set_index("state_norm")[LATEST_YEAR_COL]


def build_state_per_lakh(factories: pd.Series) -> pd.Series:
    pop = pd.read_csv(POP_REF)
    state_pop = pop.groupby(pop["state_name"].apply(_canon_state))["population"].sum()

    common = factories.index.intersection(state_pop.index)
    log.info("Matched %d/%d states to Census population", len(common), len(factories))
    if len(common) < len(factories):
        log.warning("Unmatched states: %s", sorted(set(factories.index) - set(state_pop.index)))

    per_lakh = (factories.loc[common] / state_pop.loc[common] * 100_000).round(2)
    return per_lakh


def build_pincode_output(state_per_lakh: pd.Series) -> pd.DataFrame:
    known = set(pd.read_csv(COORDS, dtype={"pincode": str})["pincode"])
    pc_map = pd.read_csv(PINCODE_REF, dtype={"pincode": str})
    pc_map = pc_map[pc_map["pincode"].isin(known)].copy()
    pc_map["state_norm"] = pc_map["state_name"].apply(_canon_state)

    matched_states = set(state_per_lakh.index) & set(pc_map["state_norm"].unique())
    log.info("Matched %d/%d states with pincode coverage", len(matched_states), pc_map["state_norm"].nunique())

    pc_map["factories_per_lakh"] = pc_map["state_norm"].map(state_per_lakh)
    out = pc_map.dropna(subset=["factories_per_lakh"])[["pincode", "factories_per_lakh"]]
    return out.drop_duplicates("pincode").set_index("pincode")


def write_output(out: pd.DataFrame, dry_run: bool = False) -> None:
    out_path = RAW / "industrial.csv"
    if dry_run:
        log.info("[DRY RUN] industrial.csv (%d rows):\n%s", len(out), out.head(10))
        return
    out.to_csv(out_path)
    log.info("Written: %s (%d rows)", out_path.name, len(out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="RS_Session_266_AU_2397_1.csv — see module docstring")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    factories = load_state_factory_counts(Path(args.csv))
    state_per_lakh = build_state_per_lakh(factories)
    out = build_pincode_output(state_per_lakh)
    log.info("factories_per_lakh computed for %d pincodes", len(out))
    write_output(out, dry_run=args.dry_run)


if __name__ == "__main__":
    main()

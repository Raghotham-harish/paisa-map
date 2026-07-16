#!/usr/bin/env python3
"""
fetch_education.py — School density (schools per lakh population) by pincode,
from UDISE+ (Unified District Information System for Education Plus).

Two input modes, in preference order:

  1. --school-csv — the raw school-level profile export (one row per
     school, ~1.47M rows, with real state/district/pincode columns).
     Downloaded from UDISE+'s bulk-export feature as a per-state-group zip
     (e.g. "profile_data_1_All State_2024-25.zip" -> 100_prof1.csv).
     Counted and grouped by district here, then run through the exact same
     district-population-per-lakh pipeline as every other district-level
     signal in this project (bank_branches_per_lakh, msme_per_lakh,
     upi_txn_value_per_capita) — genuinely real district counts, not an
     approximation.

  2. --csv — a state-wise summary CSV (e.g. data.gov.in's "State/UT-wise
     Details of Highlights of Schools, Enrolments and Teachers as per
     UDISE+ Data", or UDISE's own published "Table 2.2"). Lower fidelity —
     applies one state total uniformly to every pincode in that state — but
     needs only a small file, useful if the full school-level export isn't
     available. Column layout matched by name/keyword since data.gov.in
     doesn't guarantee stable headers release to release.

Both data.gov.in (the state-wise summary's usual source) and UDISE+'s own
portal 403 non-browser requests / require a login for bulk export — see
fetch_commercial.py's docstring for the data.gov.in User-Agent-block
finding, which does NOT apply to UDISE+'s own export (that one's a real
login wall, not just a UA block; a human export is the only proven path).

Verified 2026-07-17 against real downloaded files: state-wise Table 2.2
(2022-23) matches 36/37 rows via --csv, and the school-level 2024-25
profile export matches 659/782 districts to Census population via
--school-csv (~20 new CENSUS_DISTRICT_ALIASES entries added for this
source's spelling variants). Remaining district gaps there are mostly
post-2011-Census reorganizations (Andhra Pradesh's 13->26 district split,
a few Assam renames) — not fixable by aliasing without a newer district-
population reference.

Not yet included: AISHE (higher-education institution counts). AISHE's
open releases are PDF reports, not clean CSV (aishe.gov.in) — a natural
follow-up: parse the widely-cited state-wise college-count table with
pdfplumber, same pattern as parse_rbi_branch_master.py.

Output: data/raw/education.csv — pincode, schools_per_lakh

Usage:
  python3 etl/fetch_education.py --school-csv /path/to/100_prof1.csv
  python3 etl/fetch_education.py --csv /path/to/udise_state_schools.csv
  python3 etl/fetch_education.py --school-csv /path/to/100_prof1.csv --dry-run
"""

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

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

# Matched case-insensitively against the downloaded CSV's headers —
# data.gov.in resources don't guarantee stable column names release to
# release, so this matches by keyword rather than position/exact name.
STATE_COL_KEYWORDS  = ["state/ut", "state", "ut"]
SCHOOL_COL_KEYWORDS = ["total no. of school", "total schools", "no. of schools",
                       "number of schools", "schools"]


def _find_col(columns, keywords):
    """First column whose lowercased header contains any keyword, keywords checked
    longest-first so a specific phrase wins over a generic substring like 'state'."""
    for kw in sorted(keywords, key=len, reverse=True):
        for c in columns:
            if kw in c.strip().lower():
                return c
    return None


def load_state_schools(csv_path: Path) -> pd.Series:
    """state_norm -> total school count, from a manually-downloaded UDISE+ state-wise CSV."""
    df = pd.read_csv(csv_path)
    state_col  = _find_col(df.columns, STATE_COL_KEYWORDS)
    school_col = _find_col(df.columns, SCHOOL_COL_KEYWORDS)
    if state_col is None or school_col is None:
        raise SystemExit(
            f"Could not find state/school columns in {csv_path}.\n"
            f"  Columns found: {list(df.columns)}\n"
            f"  Looked for state in {STATE_COL_KEYWORDS}, schools in {SCHOOL_COL_KEYWORDS}."
        )
    log.info("Using column %r for state, %r for school count", state_col, school_col)

    df = df[[state_col, school_col]].copy()
    df[school_col] = (
        df[school_col].astype(str).str.replace(",", "", regex=False).str.extract(r"(\d+)")[0]
    )
    df = df.dropna(subset=[school_col])
    df[school_col] = df[school_col].astype(int)
    df["state_norm"] = df[state_col].apply(_norm_state)
    # Drop any "India" / "All India" / grand-total summary row state-wise tables often include
    df = df[~df["state_norm"].str.contains(r"^INDIA$|ALL INDIA|GRAND TOTAL|^TOTAL$", regex=True, na=False)]
    return df.set_index("state_norm")[school_col]


def build_pincode_schools_per_lakh_state(state_schools: pd.Series) -> pd.DataFrame:
    """State-level path: one total applied uniformly to every pincode in that state."""
    pop = pd.read_csv(POP_REF)
    pop["state_norm"] = pop["state_name"].apply(_norm_state)
    state_pop = pop.groupby("state_norm")["population"].sum()

    matched = state_schools.index.isin(state_pop.index)
    log.info("Matched %d/%d states to Census population", matched.sum(), len(state_schools))
    if not matched.all():
        log.warning("Unmatched states: %s", list(state_schools.index[~matched]))

    per_lakh = (state_schools[matched] / state_pop.reindex(state_schools.index[matched]) * 100_000).round(2)

    known = set(pd.read_csv(COORDS, dtype={"pincode": str})["pincode"])
    pc_map = pd.read_csv(PINCODE_REF, dtype={"pincode": str})
    pc_map = pc_map[pc_map["pincode"].isin(known)].copy()
    pc_map["state_norm"] = pc_map["state_name"].apply(_norm_state)

    pc_map["schools_per_lakh"] = pc_map["state_norm"].map(per_lakh)
    out = pc_map.dropna(subset=["schools_per_lakh"])[["pincode", "schools_per_lakh"]]
    return out.drop_duplicates("pincode").set_index("pincode")


def load_district_school_counts(school_csv: Path) -> pd.Series:
    """(state_norm, district_norm) -> real school count, from the raw school-level export."""
    df = pd.read_csv(school_csv, usecols=["state", "district"], dtype=str)
    log.info("Loaded %d school rows from %s", len(df), school_csv.name)
    df["state_norm"] = df["state"].apply(_norm_state)
    df["district_norm"] = df["district"].apply(_norm_district)
    df["district_norm"] = df.apply(lambda r: _add_delhi_suffix(r["district_norm"], r["state_norm"]), axis=1)
    df["district_norm"] = df["district_norm"].apply(lambda n: CENSUS_DISTRICT_ALIASES.get(n, n))
    return df.groupby(["state_norm", "district_norm"]).size()


def build_pincode_schools_per_lakh_district(district_schools: pd.Series) -> pd.DataFrame:
    """District-level path: real per-district counts, same pipeline as fetch_commercial.py."""
    pop = pd.read_csv(POP_REF)
    pop["state_norm"] = pop["state_name"].apply(_norm_state)
    pop["district_norm"] = pop["district"].apply(_norm_district)
    pop["district_norm"] = pop["district_norm"].apply(lambda n: CENSUS_DISTRICT_ALIASES.get(n, n))
    pop_idx = pop.drop_duplicates(["state_norm", "district_norm"]).set_index(
        ["state_norm", "district_norm"])["population"]

    matched = district_schools.index.isin(pop_idx.index)
    log.info("Matched %d/%d districts to Census population", matched.sum(), len(district_schools))
    if not matched.all():
        log.warning("Unmatched (state, district): %s", list(district_schools.index[~matched][:15]))

    per_lakh = (district_schools[matched] / pop_idx.reindex(district_schools.index[matched]) * 100_000).round(2)

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
            rows.append({"pincode": r.pincode, "schools_per_lakh": val})
    return pd.DataFrame(rows).drop_duplicates("pincode").set_index("pincode")


def write_output(out: pd.DataFrame, dry_run: bool = False) -> None:
    out_path = RAW / "education.csv"
    if dry_run:
        log.info("[DRY RUN] education.csv (%d rows):\n%s", len(out), out.head(10))
        return
    out.to_csv(out_path)
    log.info("Written: %s (%d rows)", out_path.name, len(out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--school-csv", help="Raw school-level UDISE+ profile export (preferred — see module docstring)")
    ap.add_argument("--csv", help="State-wise UDISE+ summary CSV (fallback, lower fidelity)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.school_csv and not args.csv:
        raise SystemExit("Pass --school-csv (preferred), --csv (fallback), or both — see module docstring.")

    district_out = state_out = None
    if args.school_csv:
        district_schools = load_district_school_counts(Path(args.school_csv))
        district_out = build_pincode_schools_per_lakh_district(district_schools)
    if args.csv:
        state_schools = load_state_schools(Path(args.csv))
        log.info("Loaded school counts for %d states/UTs", len(state_schools))
        state_out = build_pincode_schools_per_lakh_state(state_schools)

    if district_out is not None and state_out is not None:
        # Real district-level counts where available; state-level uniform
        # fallback fills in pincodes whose district didn't match (mostly
        # post-2011-Census district reorganizations — see docstring).
        missing = state_out.index.difference(district_out.index)
        out = pd.concat([district_out, state_out.loc[missing]]).sort_index()
        log.info("Combined: %d district-level + %d state-level-fallback = %d pincodes",
                  len(district_out), len(missing), len(out))
    else:
        out = district_out if district_out is not None else state_out
        log.info("schools_per_lakh computed for %d pincodes", len(out))

    write_output(out, dry_run=args.dry_run)


if __name__ == "__main__":
    main()

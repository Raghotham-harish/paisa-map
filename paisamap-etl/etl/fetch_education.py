#!/usr/bin/env python3
"""
fetch_education.py — School density (schools per lakh population) by pincode,
from UDISE+ (Unified District Information System for Education Plus).

Source: Ministry of Education, published via the Open Government Data (OGD)
Platform:
  https://www.data.gov.in/resource/stateut-wise-details-highlights-schools-enrolments-and-teachers-udise-data-during-2022-23
  ("State/UT-wise Details of Highlights of Schools, Enrolments and Teachers
  as per UDISE+ Data during 2022-23")

data.gov.in returns HTTP 403 to non-browser requests on both the catalog
page and individual resource pages (confirmed 2026-07-17) — same class of
bot-wall already documented for RBI's DBIE/rbidocs in fetch_rbi_bsr.py. The
proven path from that precedent applies here too:

  1. Open the resource URL above in a real browser.
  2. Use the page's own "Download" -> CSV option (or, for a scriptable pull
     next time, the API tab with your own free api.data.gov.in key).
  3. python3 etl/fetch_education.py --csv /path/to/downloaded.csv

Only STATE-level granularity was findable as an open, non-gated dataset —
no pan-India district-wise school count exists outside UDISE+'s own
dashboard/report module, which needs a login for district drill-down.
Applied uniformly to every pincode within a state, same approach already
used for deposits_per_capita / credit_deposit_ratio — also state-level RBI
data (see fetch_rbi_bsr.py).

Column layout is NOT verified against a real download yet (blocked from
fetching one directly) — load_state_schools() matches columns by
name/keyword rather than position specifically so a slightly different
release-to-release header still works, but check the printed "Using
column" line the first time this runs against a real file before trusting
the output. Verified against a synthetic sample CSV (plausible data.gov.in
header names) to confirm the parsing/matching/per-lakh math is correct;
that is not the same as confirming the real export's column names match.

Not yet included: AISHE (higher-education institution counts). AISHE's
open releases are PDF reports, not clean CSV (aishe.gov.in) — a natural
follow-up once schools_per_lakh is validated against a real download:
parse the widely-cited state-wise college-count table with pdfplumber,
same pattern as parse_rbi_branch_master.py.

Output: data/raw/education.csv — pincode, schools_per_lakh

Usage:
  python3 etl/fetch_education.py --csv /path/to/udise_state_schools.csv
  python3 etl/fetch_education.py --csv /path/to/udise_state_schools.csv --dry-run
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
    """state_norm -> total school count, from a manually-downloaded UDISE+ CSV."""
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
    # Drop any "All India" / grand-total summary row state-wise tables often include
    df = df[~df["state_norm"].str.contains(r"ALL INDIA|GRAND TOTAL|^TOTAL$", regex=True, na=False)]
    return df.set_index("state_norm")[school_col]


def build_pincode_schools_per_lakh(state_schools: pd.Series) -> pd.DataFrame:
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


def write_output(out: pd.DataFrame, dry_run: bool = False) -> None:
    out_path = RAW / "education.csv"
    if dry_run:
        log.info("[DRY RUN] education.csv (%d rows):\n%s", len(out), out.head(10))
        return
    out.to_csv(out_path)
    log.info("Written: %s (%d rows)", out_path.name, len(out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True,
                     help="Manually-downloaded UDISE+ state-wise school-count CSV (see module docstring)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    state_schools = load_state_schools(Path(args.csv))
    log.info("Loaded school counts for %d states/UTs", len(state_schools))
    out = build_pincode_schools_per_lakh(state_schools)
    log.info("schools_per_lakh computed for %d pincodes", len(out))
    write_output(out, dry_run=args.dry_run)


if __name__ == "__main__":
    main()

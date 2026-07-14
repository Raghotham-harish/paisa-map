"""
parse_rbi_branch_master.py — Real per-pincode bank branch counts from RBI branch master data.

Source : RBI branch banking data export, pan-India, public sector banks only
          (SBI, Bank of Baroda, Bank of India, Bank of Maharashtra, Canara,
          Central Bank of India, Indian Bank, Indian Overseas Bank, Punjab &
          Sind Bank, Punjab National Bank, UCO Bank, Union Bank of India).
          Does NOT include private banks (HDFC, ICICI, Axis, Kotak, IDBI, ...),
          small finance banks, or cooperative banks — so counts here are a
          floor on true branch density, not the total. Only rows whose
          Banking Channel Type is BRANCH are counted (source export also
          includes BCs/CSPs/ATMs/offices, which aren't branches).

Input  : data/reference/rbi_branch_master_india/Banking_Export_Data_India_PSU.csv
          Pipe-delimited, one row per outlet, all 36 states/UTs. Pincode is not
          a dedicated column — it's the last comma-separated token of the
          free-text Address field. Not committed to git (136MB, over GitHub's
          size limit) — re-export from https://data.rbi.org.in/DBIE/#/banking-outlet
          (Search By Bank -> Bank Group: Public Sector Banks -> all banks
          selected, no state filter -> Type of Banking Channel: Branch) to
          regenerate it.

Output : data/raw/rbi_branch_counts_india.csv
          Columns: pincode, state, district, psu_branch_count
          One row per distinct pincode that has >=1 branch in the source data.

Usage:
  python3 etl/parse_rbi_branch_master.py
  python3 etl/parse_rbi_branch_master.py --dry-run
"""

import argparse
import csv
import logging
import re
from pathlib import Path

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

ETL     = Path(__file__).parent.parent
REF     = ETL / "data" / "reference"
RAW     = ETL / "data" / "raw"
SRC_DIR = REF / "rbi_branch_master_india"

PINCODE_RE = re.compile(r"(\d{6})\s*\"?\s*$")


def _iter_branch_rows(path: Path):
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="|", quotechar='"')
        header = next(reader, None)
        if header is None:
            return
        for row in reader:
            if len(row) < 17:
                continue
            yield row


def parse_branch_master() -> pd.DataFrame:
    """Read the pan-India branch master export, extract (pincode, state, district, bank)."""
    parts = sorted(SRC_DIR.glob("Banking_Export_Data*.csv"))
    if not parts:
        raise SystemExit(f"No source files found in {SRC_DIR}")

    rows = []
    skipped_no_pin = 0
    skipped_not_branch = 0
    for part in parts:
        n_before = len(rows)
        for r in _iter_branch_rows(part):
            state, district, address, channel_type, bank_name = r[2], r[3], r[15], r[16], r[7]
            if channel_type.strip().upper() != "BRANCH":
                skipped_not_branch += 1
                continue
            m = PINCODE_RE.search(address.strip())
            if not m:
                skipped_no_pin += 1
                continue
            rows.append({
                "pincode":  m.group(1),
                "state":    state.strip().upper(),
                "district": district.strip().upper(),
                "bank":     bank_name.strip(),
            })
        log.info("%s: %d branch rows", part.name, len(rows) - n_before)

    log.info(
        "Parsed %d branches total (skipped %d non-branch, %d without a parseable pincode)",
        len(rows), skipped_not_branch, skipped_no_pin,
    )
    return pd.DataFrame(rows)


def aggregate_by_pincode(branches: pd.DataFrame) -> pd.DataFrame:
    """
    One row per pincode. A handful of source rows carry a district (or even
    state) label that doesn't match their own address pincode (data entry
    noise in the RBI export — e.g. a Mumbai-pincode branch filed under
    'PUNE'), so pincode is the grouping key and state/district are resolved
    as the most common label seen for that pincode, not group keys themselves.
    """
    def modal(col):
        return branches.groupby("pincode")[col].agg(lambda s: s.value_counts().idxmax()).reset_index()

    counts = branches.groupby("pincode").size().reset_index(name="psu_branch_count")
    counts = counts.merge(modal("state"), on="pincode").merge(modal("district"), on="pincode")
    return counts.sort_values(["state", "district", "pincode"])[
        ["pincode", "state", "district", "psu_branch_count"]
    ]


def write_output(counts: pd.DataFrame, dry_run: bool = False) -> None:
    out_path = RAW / "rbi_branch_counts_india.csv"
    if dry_run:
        log.info("[DRY RUN] %s head:\n%s", out_path.name, counts.head(15))
        log.info("[DRY RUN] %d distinct pincodes, %d branches total, %d states",
                  len(counts), counts["psu_branch_count"].sum(), counts["state"].nunique())
        return
    counts.to_csv(out_path, index=False)
    log.info("Written: %s (%d pincodes, %d branches, %d states)",
              out_path.name, len(counts), counts["psu_branch_count"].sum(), counts["state"].nunique())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    branches = parse_branch_master()
    counts = aggregate_by_pincode(branches)
    write_output(counts, dry_run=args.dry_run)


if __name__ == "__main__":
    main()

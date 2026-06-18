#!/usr/bin/env python3
"""
fetch_itr_stats.py — IT Dept Annual Income Tax Statistics parser

Extracts state/district-wise ITR filer count and gross income from the
CBDT "All India Income Tax Statistics" PDF → itr_stats.csv

This is one of the strongest income signals available: direct measure of
how many people in a state/district are filing tax returns and at what
income levels.

────────────────────────────────────────────────────────────────────────
HOW TO GET THE PDF (one-time manual step):
  1. Go to: https://www.incometaxindia.gov.in/pages/statistics.aspx
  2. Click "All India Income Tax Statistics" for the latest year
     (or "Time Series Data on Direct Taxes")
  3. Save the PDF locally, e.g. ~/Downloads/itr_stats_2023.pdf
  4. Run: python3 fetch_itr_stats.py ~/Downloads/itr_stats_2023.pdf

Alternative sources if the main site is down:
  - CBDT press release PDFs (search "CBDT direct tax statistics 2023")
  - NDAP / data.gov.in → search "income tax returns district"
────────────────────────────────────────────────────────────────────────

Output columns:
  state_ut          — State or Union Territory name
  district          — District name (if available; else blank)
  assessment_year   — e.g. "2022-23"
  num_returns       — Number of ITR returns filed
  gross_income_cr   — Gross Total Income in ₹ crore
  tax_payable_cr    — Tax payable in ₹ crore (optional)
  returns_per_lakh  — Proxy: filer density (if population data available)
  ppi_signal        — Derived: 0-100 normalised score for use in ML

Usage:
  python3 fetch_itr_stats.py path/to/itr_stats.pdf
  python3 fetch_itr_stats.py path/to/itr_stats.pdf --year 2022-23
  python3 fetch_itr_stats.py path/to/itr_stats.pdf --debug   # print raw tables
"""

import argparse
import csv
import re
import sys
from pathlib import Path

try:
    import pdfplumber
except ImportError:
    sys.exit("pdfplumber not installed — run: pip install pdfplumber")

OUT_FIELDS = [
    "state_ut", "district", "assessment_year",
    "num_returns", "gross_income_cr", "tax_payable_cr",
    "returns_per_lakh", "ppi_signal", "source_file",
]

DATA_DIR = Path(__file__).parent.parent / "data" / "raw"
OUT_CSV  = DATA_DIR / "itr_stats.csv"

# Known state name normalisation (IT Dept uses various spellings)
STATE_ALIASES = {
    "JAMMU & KASHMIR": "JAMMU AND KASHMIR",
    "J & K": "JAMMU AND KASHMIR",
    "DELHI": "NCT OF DELHI",
    "ANDAMAN & NICOBAR": "ANDAMAN AND NICOBAR ISLANDS",
    "DADRA & NAGAR HAVELI": "DADRA AND NAGAR HAVELI",
    "DAMAN & DIU": "DAMAN AND DIU",
}

# State → approximate population (2011 Census, crore) for density calc
STATE_POP_CR = {
    "UTTAR PRADESH": 19.98, "MAHARASHTRA": 11.24, "BIHAR": 10.41,
    "WEST BENGAL": 9.13, "MADHYA PRADESH": 7.26, "RAJASTHAN": 6.86,
    "TAMIL NADU": 7.21, "KARNATAKA": 6.11, "GUJARAT": 6.04,
    "ANDHRA PRADESH": 4.94, "ODISHA": 4.20, "TELANGANA": 3.50,
    "KERALA": 3.34, "JHARKHAND": 3.30, "ASSAM": 3.12,
    "PUNJAB": 2.77, "CHHATTISGARH": 2.56, "HARYANA": 2.54,
    "NCT OF DELHI": 1.68, "JAMMU AND KASHMIR": 1.25,
    "UTTARAKHAND": 1.01, "HIMACHAL PRADESH": 0.69,
    "TRIPURA": 0.37, "MEGHALAYA": 0.30, "MANIPUR": 0.29,
    "NAGALAND": 0.20, "ARUNACHAL PRADESH": 0.14, "GOA": 0.15,
    "MIZORAM": 0.11, "SIKKIM": 0.06,
}


def extract_tables_from_pdf(pdf_path: Path, debug: bool = False):
    """Extract all tables from a PDF using pdfplumber."""
    all_tables = []
    with pdfplumber.open(pdf_path) as pdf:
        print(f"  Pages: {len(pdf.pages)}")
        for i, page in enumerate(pdf.pages):
            tables = page.extract_tables()
            text   = page.extract_text() or ""
            if debug:
                print(f"\n--- Page {i+1} ---")
                print(text[:500])
                for t in tables:
                    print(f"  Table {len(all_tables)+1}: {len(t)} rows × {len(t[0]) if t else 0} cols")
                    if t:
                        print(f"  Header: {t[0]}")
            for t in tables:
                if t and len(t) > 2:
                    all_tables.append({"page": i+1, "rows": t})
    return all_tables


def _clean(val) -> str:
    if val is None:
        return ""
    return re.sub(r"\s+", " ", str(val)).strip()


def _num(val) -> float:
    """Parse a number string that may contain commas, lakhs notation etc."""
    s = re.sub(r"[^\d.]", "", str(val or ""))
    try:
        return float(s)
    except ValueError:
        return 0.0


def is_state_table(rows) -> bool:
    """Heuristic: does this table look like a state/district ITR table?"""
    if not rows or len(rows) < 3:
        return False
    header_text = " ".join(_clean(c) for c in rows[0] if c).upper()
    keywords = ["RETURN", "INCOME", "STATE", "UT", "DISTRICT", "GROSS", "FILER", "ASSESSED"]
    return sum(1 for k in keywords if k in header_text) >= 2


def find_col_indices(header_row):
    """Map column positions for: name, returns, gross income, tax payable."""
    idx = {"name": -1, "returns": -1, "income": -1, "tax": -1}
    for i, cell in enumerate(header_row):
        h = _clean(cell).upper()
        if idx["name"] < 0 and any(k in h for k in ["STATE", "UT", "DISTRICT", "NAME", "PARTICULARS"]):
            idx["name"] = i
        if idx["returns"] < 0 and any(k in h for k in ["RETURN", "NUMBER", "NO.", "FILER", "ASSESSEE"]):
            idx["returns"] = i
        if idx["income"] < 0 and any(k in h for k in ["GROSS", "INCOME", "GTI", "TOTAL INCOME"]):
            idx["income"] = i
        if idx["tax"] < 0 and any(k in h for k in ["TAX", "PAYABLE", "DEMAND"]):
            idx["tax"] = i
    return idx


def parse_state_table(table_rows, assessment_year: str, source_file: str) -> list:
    """Parse a state/district ITR table → list of row dicts."""
    rows = table_rows["rows"]
    # Try each row as potential header (sometimes row 0 is a title)
    header_idx = 0
    for i, row in enumerate(rows[:4]):
        if is_state_table([row]):
            header_idx = i
            break

    header   = rows[header_idx]
    col_idx  = find_col_indices(header)
    records  = []

    for row in rows[header_idx + 1:]:
        if not row:
            continue
        name_val = _clean(row[col_idx["name"]]) if col_idx["name"] >= 0 and col_idx["name"] < len(row) else ""
        if not name_val or len(name_val) < 2:
            continue
        # Skip sub-totals, totals, blank rows
        name_upper = name_val.upper()
        if any(k in name_upper for k in ["TOTAL", "ALL INDIA", "GRAND", "SUB-TOTAL", "OTHERS", "---"]):
            continue
        # Skip rows that are purely numeric (column indices etc.)
        if re.match(r"^[\d\s.]+$", name_val):
            continue

        returns    = _num(row[col_idx["returns"]])  if col_idx["returns"] >= 0 and col_idx["returns"] < len(row)  else 0
        income_cr  = _num(row[col_idx["income"]])   if col_idx["income"] >= 0  and col_idx["income"]  < len(row)  else 0
        tax_cr     = _num(row[col_idx["tax"]])      if col_idx["tax"] >= 0     and col_idx["tax"]      < len(row)  else 0

        if returns == 0 and income_cr == 0:
            continue

        # Normalise state name
        norm_name = STATE_ALIASES.get(name_upper, name_upper)

        # Returns per lakh population (if state-level)
        pop_cr = STATE_POP_CR.get(norm_name, 0)
        returns_per_lakh = round(returns / (pop_cr * 100), 1) if pop_cr > 0 else 0

        records.append({
            "state_ut":        norm_name,
            "district":        "",
            "assessment_year": assessment_year,
            "num_returns":     int(returns),
            "gross_income_cr": income_cr,
            "tax_payable_cr":  tax_cr,
            "returns_per_lakh": returns_per_lakh,
            "ppi_signal":      "",      # filled in normalisation pass
            "source_file":     source_file,
        })

    return records


def normalise_ppi(records: list) -> list:
    """Normalise returns_per_lakh → 0-100 PPI signal."""
    vals = [r["returns_per_lakh"] for r in records if r["returns_per_lakh"] > 0]
    if not vals:
        return records
    lo, hi = min(vals), max(vals)
    span = hi - lo or 1
    for r in records:
        if r["returns_per_lakh"] > 0:
            r["ppi_signal"] = round(50 + 50 * (r["returns_per_lakh"] - lo) / span, 1)
    return records


def parse_year_from_text(text: str) -> str:
    """Guess assessment year from PDF text."""
    m = re.search(r"(?:assessment\s+year|AY|A\.Y\.)\s*[:\-]?\s*(\d{4}-\d{2,4})", text, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"\b(20\d{2}-\d{2,4})\b", text)
    return m.group(1) if m else ""


def run(pdf_path: Path, year_override: str = "", debug: bool = False) -> list:
    print(f"\nIT Dept Statistics Parser")
    print(f"  File: {pdf_path.name}")

    all_tables = extract_tables_from_pdf(pdf_path, debug=debug)
    print(f"  Tables found: {len(all_tables)}")

    # Guess assessment year from first page text
    with pdfplumber.open(pdf_path) as pdf:
        first_text = pdf.pages[0].extract_text() or ""
    assessment_year = year_override or parse_year_from_text(first_text) or "unknown"
    print(f"  Assessment year: {assessment_year}")

    all_records = []
    for t in all_tables:
        if is_state_table(t["rows"]):
            recs = parse_state_table(t, assessment_year, pdf_path.name)
            if recs:
                print(f"  Page {t['page']}: extracted {len(recs)} rows")
                all_records.extend(recs)

    # Deduplicate by state_ut + district + year
    seen = set()
    deduped = []
    for r in all_records:
        key = (r["state_ut"], r["district"], r["assessment_year"])
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    deduped = normalise_ppi(deduped)

    if not deduped:
        print("\n  ⚠ No structured tables found.")
        print("  Try running with --debug to see raw page content.")
        print("  The PDF may use scanned images instead of text tables.")
        print("  In that case, download the Excel version from the same page.\n")

    return deduped


def write_csv(records: list, out_path: Path = OUT_CSV) -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    existing = []
    if out_path.exists():
        with open(out_path, newline="") as f:
            existing = list(csv.DictReader(f))

    # Deduplicate vs existing by state + district + year
    exist_keys = {(r["state_ut"], r["district"], r["assessment_year"]) for r in existing}
    new_rows = [r for r in records if (r["state_ut"], r["district"], r["assessment_year"]) not in exist_keys]
    all_rows = existing + new_rows

    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OUT_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_rows)

    print(f"\n  → {out_path}  ({len(all_rows)} total rows, +{len(new_rows)} new)\n")
    return len(new_rows)


def print_summary(records: list):
    if not records:
        return
    by_state = {r["state_ut"]: r for r in records}
    top = sorted(by_state.values(), key=lambda r: r["returns_per_lakh"], reverse=True)
    print(f"{'State/UT':<30} {'Returns':>12} {'Income (₹Cr)':>14} {'Per Lakh':>10} {'PPI Signal':>10}")
    print("-" * 80)
    for r in top[:15]:
        print(f"{r['state_ut']:<30} {r['num_returns']:>12,} {r['gross_income_cr']:>14,.0f} "
              f"{r['returns_per_lakh']:>10.1f} {r['ppi_signal']:>10}")


def main():
    ap = argparse.ArgumentParser(description="Parse IT Dept Annual Income Tax Statistics PDF")
    ap.add_argument("pdf", help="Path to the downloaded PDF")
    ap.add_argument("--year", default="", help="Override assessment year (e.g. 2022-23)")
    ap.add_argument("--out",  default=str(OUT_CSV), help="Output CSV path")
    ap.add_argument("--debug", action="store_true", help="Print raw extracted tables")
    args = ap.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        sys.exit(f"File not found: {pdf_path}")

    records = run(pdf_path, year_override=args.year, debug=args.debug)
    if records:
        write_csv(records, Path(args.out))
        print_summary(records)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()

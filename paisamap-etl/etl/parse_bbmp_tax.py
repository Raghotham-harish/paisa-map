#!/usr/bin/env python3
"""
parse_bbmp_tax.py — BBMP property tax receipt PDF parser

Extracts: zone A-F, ward, address, tax amounts → data/raw/bbmp_zones.csv
Zone A-F maps directly to a PPI tier range used as a property-value signal.

BBMP zone guide:
  A → CBD / prime commercial       PPI 128–150
  B → Near-CBD / premium resi      PPI 112–130
  C → Mid-city residential         PPI  96–115
  D → Outer residential            PPI  82– 98
  E → Peripheral residential       PPI  65– 82
  F → Far peripheral / semi-rural  PPI  50– 67

Usage:
  python3 parse_bbmp_tax.py path/to/receipt.pdf [more.pdf ...]
  python3 parse_bbmp_tax.py --dir path/to/folder/
  python3 parse_bbmp_tax.py receipt.pdf --no-nominatim   # skip pincode lookup
"""

import argparse
import csv
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

try:
    import pdfplumber
except ImportError:
    sys.exit("pdfplumber not installed — run: pip install pdfplumber")

ZONE_MAP = {
    "A": {"label": "CBD / prime commercial",         "ppi_low": 128, "ppi_high": 150},
    "B": {"label": "Near-CBD / premium residential", "ppi_low": 112, "ppi_high": 130},
    "C": {"label": "Mid-city residential",           "ppi_low":  96, "ppi_high": 115},
    "D": {"label": "Outer residential",              "ppi_low":  82, "ppi_high":  98},
    "E": {"label": "Peripheral residential",         "ppi_low":  65, "ppi_high":  82},
    "F": {"label": "Far peripheral / semi-rural",    "ppi_low":  50, "ppi_high":  67},
}

OUT_FIELDS = [
    "source_file", "year", "receipt_no", "date",
    "ward_no", "ward_name",
    "zone", "zone_label", "ppi_low", "ppi_high",
    "property_address", "area_hint",
    "property_tax", "cesses", "total_tax",
    "penalty", "interest", "swm_cess", "net_tax_paid",
    "pincode_guess",
]

DATA_DIR = Path(__file__).parent.parent / "data" / "raw"
OUT_CSV  = DATA_DIR / "bbmp_zones.csv"


def extract_text(pdf_path: Path) -> str:
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            t = page.extract_text(x_tolerance=3, y_tolerance=3) or ""
            pages.append(t)
    return "\n".join(pages)


def _re(pattern, text, default="", flags=re.IGNORECASE | re.DOTALL):
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else default


def _amount(pattern, text) -> float:
    m = re.search(pattern, text, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            pass
    return 0.0


def parse_receipt(text: str, source_file: str) -> dict:
    # Ward: "12- SHETTY HALLI" — appears mid-line after 10-digit app number,
    # before Kannada text. The "Ward No & Name as declared" label is on a
    # separate line due to the bilingual column layout.
    ward_no, ward_name = "", ""
    ward_m = re.search(r"(?:\d{8,})\s+(\d{1,3})\s*[-–]\s*([A-Z][A-Z ]+?)(?=\s+\(cid:|[^\x00-\x7F])", text)
    if not ward_m:
        # Fallback: any "NN- ALL CAPS" pattern
        ward_m = re.search(r"\b(\d{1,3})\s*[-–]\s*([A-Z]{2}[A-Z ]+?)(?=\s+\(cid:|\s+[^\x00-\x7F])", text)
    if ward_m:
        ward_no   = ward_m.group(1).strip()
        ward_name = ward_m.group(2).strip().rstrip("-– ").strip()

    # Zone — BBMP PDFs put the letter directly after the label without a colon
    # e.g. "Residential zone classification E Non Residential zone"
    zone = _re(r"Residential zone classification\s+([A-F])\b", text)
    if not zone:
        zone = _re(r"zone classification\s+([A-F])\b", text)

    # Receipt number and date
    receipt_no = _re(r"Receipt\s*No\.?\s*[:\-]?\s*(\d{8,})", text)
    date       = _re(r"Date\s*[:\-]?\s*(\d{1,2}[-/]\d{2}[-/]\d{4})", text)

    # Tax year
    year = _re(r"Tax Paid Year\s*[:\-]?\s*(\d{4}-\d{4})", text)
    if not year:
        year = _re(r"^(\d{4}-\d{4})", text, flags=re.MULTILINE)

    # Property address — PDF column layout puts the first part of the address BEFORE
    # the "Property Address :" label; grab both parts and merge.
    # Pattern A: label + rest of address (second part in layout)
    addr_b = _re(r"Property Address\s*[:\-]\s*(.+?)(?=\s+(?:Survey No|Old PID|\d/\d)|$)", text)
    # Pattern B: anything looking like a street address (number + locality + CROSS/MAIN/LAYOUT)
    addr_a_m = re.search(r"(\d+\s+[A-Z][a-zA-Z]+.{5,80}(?:LAYOUT|NAGAR|MAIN|CROSS|ROAD|COLONY))", text)
    addr_a   = addr_a_m.group(1).strip() if addr_a_m else ""
    # Merge: if both present and different, join them
    if addr_a and addr_b and addr_b not in addr_a:
        address = addr_a + ", " + addr_b
    else:
        address = addr_a or addr_b
    address = re.sub(r"\s+", " ", address).strip()

    # Area hint: first token before comma
    area_hint = re.split(r"[,\n]", address)[0].strip() if address else ""

    # Tax amounts — try named patterns, then fall back to the data row
    # The data row format: YEAR  prop_tax  cesses  total  rebate  penalty  interest  swm  net  advance  balance  excess
    property_tax = _amount(r"Property Tax\s+([\d,]+\.?\d*)", text)
    cesses       = _amount(r"Cesses\s+([\d,]+\.?\d*)", text)
    total_tax    = _amount(r"Total Tax\s+([\d,]+\.?\d*)", text)
    penalty      = _amount(r"Penalty\s+([\d,]+\.?\d*)", text)
    interest     = _amount(r"Interest\s+([\d,]+\.?\d*)", text)
    swm_cess     = _amount(r"SWM Cess[^\d]*([\d,]+\.?\d*)", text)
    net_tax_paid = _amount(r"Net Tax to be\s*Paid\s+([\d,]+\.?\d*)", text)

    # Fallback: parse the summary data row (space-separated numbers after the year)
    if not property_tax:
        row_m = re.search(r"(\d{4}-\d{4})\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)", text)
        if row_m:
            property_tax = float(row_m.group(2))
            cesses       = float(row_m.group(3))
            total_tax    = float(row_m.group(4))
            # group 5 = rebate, group 6 = penalty, group 7 = interest
            penalty      = float(row_m.group(6))
            interest     = float(row_m.group(7))
            swm_cess     = float(row_m.group(8))
            net_tax_paid = float(row_m.group(9))

    zone_info = ZONE_MAP.get(zone, {})

    return {
        "source_file":    source_file,
        "year":           year,
        "receipt_no":     receipt_no,
        "date":           date,
        "ward_no":        ward_no,
        "ward_name":      ward_name,
        "zone":           zone,
        "zone_label":     zone_info.get("label", ""),
        "ppi_low":        zone_info.get("ppi_low", ""),
        "ppi_high":       zone_info.get("ppi_high", ""),
        "property_address": address,
        "area_hint":      area_hint,
        "property_tax":   property_tax,
        "cesses":         cesses,
        "total_tax":      total_tax,
        "penalty":        penalty,
        "interest":       interest,
        "swm_cess":       swm_cess,
        "net_tax_paid":   net_tax_paid,
        "pincode_guess":  "",
    }


def _nominatim_postcode(query: str) -> str:
    params = urllib.parse.urlencode({
        "q": query, "format": "json",
        "addressdetails": 1, "countrycodes": "in", "limit": 1,
    })
    req = urllib.request.Request(
        f"https://nominatim.openstreetmap.org/search?{params}",
        headers={"User-Agent": "PaisaMap-ETL/1.0", "Accept-Language": "en"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read())
    if data:
        pc = data[0].get("address", {}).get("postcode", "").replace(" ", "")
        if re.match(r"^\d{6}$", pc):
            return pc
    return ""


def guess_pincode(address: str, ward_name: str = "", city: str = "Bengaluru") -> str:
    """Try progressively simpler queries until we get a 6-digit postcode."""
    # Extract first locality from address (before first comma)
    area = re.split(r"[,\n]", address)[0].strip() if address else ""

    queries = [q for q in [
        f"{address}, {city}, Karnataka, India" if address else "",
        f"{area}, {city}, Karnataka, India"     if area and area != address else "",
        f"{ward_name}, {city}, Karnataka, India" if ward_name else "",
    ] if q]

    for q in queries:
        try:
            pc = _nominatim_postcode(q)
            if pc:
                return pc
            time.sleep(0.5)
        except Exception:
            pass
    return ""


def run(pdf_paths: list, skip_nominatim: bool = False) -> list:
    rows = []
    for pdf in pdf_paths:
        pdf = Path(pdf)
        print(f"  [{pdf.name}] ", end="", flush=True)
        try:
            text = extract_text(pdf)
            row  = parse_receipt(text, pdf.name)

            if not skip_nominatim and (row["property_address"] or row["ward_name"]):
                pc = guess_pincode(row["property_address"], row["ward_name"])
                row["pincode_guess"] = pc
                time.sleep(1)

            z    = row["zone"] or "?"
            info = ZONE_MAP.get(z, {})
            print(
                f"zone={z} ({info.get('ppi_low','?')}–{info.get('ppi_high','?')} PPI)  "
                f"ward={row['ward_no']}-{row['ward_name']}  "
                f"net=₹{row['net_tax_paid']:,.0f}  "
                f"pincode={row['pincode_guess'] or '—'}"
            )
            rows.append(row)
        except Exception as e:
            print(f"ERROR: {e}")
    return rows


def write_csv(rows: list) -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    existing = []
    if OUT_CSV.exists():
        with open(OUT_CSV, newline="") as f:
            existing = list(csv.DictReader(f))

    seen     = {r["receipt_no"] for r in existing if r.get("receipt_no")}
    new_rows = [r for r in rows if r.get("receipt_no") not in seen]
    all_rows = existing + new_rows

    with open(OUT_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OUT_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_rows)

    print(f"\n  → {OUT_CSV}  ({len(all_rows)} total, +{len(new_rows)} new)\n")
    return len(new_rows)


def print_summary(rows: list):
    if not rows:
        return
    print("Zone signal extracted:")
    print(f"  {'Zone':<5} {'Label':<32} {'PPI range':<12} {'Wards'}")
    print(f"  {'-'*65}")
    by_zone: dict = {}
    for r in rows:
        z = r["zone"]
        if z not in by_zone:
            by_zone[z] = []
        by_zone[z].append(r["ward_name"])
    for z in sorted(by_zone):
        info  = ZONE_MAP.get(z, {})
        wards = ", ".join(by_zone[z])
        print(f"  {z:<5} {info.get('label',''):<32} {info.get('ppi_low','?')}–{info.get('ppi_high','?'):<9} {wards}")


def main():
    ap = argparse.ArgumentParser(description="Parse BBMP property tax receipt PDFs")
    ap.add_argument("pdfs", nargs="*", metavar="PDF", help="One or more PDF paths")
    ap.add_argument("--dir", metavar="DIR", help="Batch-process all PDFs in a folder")
    ap.add_argument("--no-nominatim", action="store_true", help="Skip Nominatim pincode lookup")
    args = ap.parse_args()

    pdf_paths = list(args.pdfs or [])
    if args.dir:
        pdf_paths += [str(p) for p in sorted(Path(args.dir).glob("*.pdf"))]
    if not pdf_paths:
        ap.print_help()
        sys.exit(1)

    print(f"\nBBMP Tax Receipt Parser  ({len(pdf_paths)} file{'s' if len(pdf_paths)!=1 else ''})\n")
    rows = run(pdf_paths, skip_nominatim=args.no_nominatim)
    if rows:
        write_csv(rows)
        print_summary(rows)


if __name__ == "__main__":
    main()

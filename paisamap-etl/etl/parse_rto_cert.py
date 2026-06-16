"""
parse_rto_cert.py — Extract vehicle signals from RTO registration certificates.

Reads:
  - Vehicle Registration Certificates (RC) — PARIVAHAN issued
  - Fitness certificates (for commercial vehicles)
  - Bulk state/district RTO data PDFs from MoRTH / individual RTOs

Output signals (added to rto_enhanced.csv):
  ev_share        — fraction of EVs by pincode
  car_2w_ratio    — LMV count / two-wheeler count
  luxury_share    — fraction of vehicles priced > ₹20L

The primary use case is batch processing a dump of RTO records (e.g. bulk RTI
response as a spreadsheet or PDF) to refine the district-level VAHAN signals.

Usage:
  python3 etl/parse_rto_cert.py --pdf /path/to/rto_rc.pdf --pincode 110016
  python3 etl/parse_rto_cert.py --dir /path/to/rc_pdfs/
  python3 etl/parse_rto_cert.py --csv /path/to/rto_dump.csv   # tabular bulk data
"""

import argparse
import logging
import re
from pathlib import Path
from collections import defaultdict

import pandas as pd

from etl.llm_extract import DocumentType, extract, pdf_to_text

log = logging.getLogger(__name__)

ETL       = Path(__file__).parent.parent
RAW       = ETL / "data" / "raw"
RTO_FILE  = RAW / "rto_enhanced.csv"

# RC field patterns (PARIVAHAN-format RCs have consistent labels)
_RE_FUEL  = re.compile(r"fuel\s*type\s*[\s:\-]*([A-Za-z/]+)", re.IGNORECASE)
_RE_CLASS = re.compile(r"vehicle\s*class\s*[\s:\-]*([A-Z0-9/]+)", re.IGNORECASE)
_RE_PC    = re.compile(r"owner.*?address.*?(\d{6})", re.IGNORECASE | re.DOTALL)
_RE_PRICE = re.compile(
    r"(?:purchase|ex[\-\s]showroom|invoice)\s*(?:price|value|amount)"
    r"[\s:\-]*(?:rs\.?|inr|₹)\s*([\d,]+)",
    re.IGNORECASE,
)

LUXURY_BRANDS = {
    "mercedes", "bmw", "audi", "jaguar", "land rover", "porsche",
    "bentley", "rolls royce", "maserati", "lamborghini", "ferrari",
    "volvo", "lexus", "infiniti", "genesis",
}
LUXURY_PRICE_INR = 2_000_000   # ₹20L


def _parse_num(s: str) -> float:
    return float(s.replace(",", "").strip())


def extract_rc_regex(text: str) -> dict:
    """Fast regex pass for digital PARIVAHAN RCs."""
    result = {"method": "regex"}

    m = _RE_FUEL.search(text)
    if m:
        result["fuel_type"] = m.group(1).strip().title()

    m = _RE_CLASS.search(text)
    if m:
        result["vehicle_class"] = m.group(1).strip().upper()

    m = _RE_PC.search(text)
    if m:
        result["owner_pincode"] = m.group(1)

    m = _RE_PRICE.search(text)
    if m:
        result["price_inr"] = _parse_num(m.group(1))

    # Luxury detection by brand name in text
    text_lower = text.lower()
    result["is_luxury"] = (
        any(brand in text_lower for brand in LUXURY_BRANDS)
        or ("price_inr" in result and result["price_inr"] >= LUXURY_PRICE_INR)
    )
    return result


def process_pdf(pdf_path: Path, pincode: str = "", force_ocr: bool = False) -> dict:
    text = pdf_to_text(pdf_path, force_ocr=force_ocr)

    regex_result = extract_rc_regex(text)
    has_basics = bool(regex_result.get("fuel_type") and regex_result.get("vehicle_class"))

    if has_basics:
        result = regex_result
        result["_source"] = "regex"
    else:
        llm_result = extract(DocumentType.RTO_CERT, text=text)
        result = {k: llm_result.get(k) for k in
                  ["registration_number", "owner_pincode", "vehicle_class",
                   "fuel_type", "make_model", "price_inr", "is_luxury"]}
        result["_source"] = llm_result.get("_source", "llm")

    result["pincode"] = pincode or result.get("owner_pincode", "")
    result["_file"] = str(pdf_path)
    return result


def process_csv(csv_path: Path) -> list:
    """
    Parse a tabular RTO data dump (e.g. bulk RTI response).
    Expected columns (flexible): registration_no, fuel_type, vehicle_class, owner_pincode, price
    """
    df = pd.read_csv(csv_path, low_memory=False)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    col_map = {}
    for target, candidates in [
        ("fuel_type",    ["fuel_type", "fuel", "propulsion"]),
        ("vehicle_class",["vehicle_class", "class", "category", "type"]),
        ("owner_pincode",["owner_pincode", "pincode", "pin_code", "zip"]),
        ("price_inr",    ["price", "price_inr", "ex_showroom", "invoice_value"]),
        ("make_model",   ["make_model", "make", "manufacturer", "model"]),
    ]:
        for c in candidates:
            if c in df.columns:
                col_map[target] = c
                break

    records = []
    for _, row in df.iterrows():
        r = {}
        for target, src in col_map.items():
            r[target] = row.get(src)
        price = pd.to_numeric(r.get("price_inr", None), errors="coerce") or 0
        make_lower = str(r.get("make_model", "")).lower()
        r["is_luxury"] = (
            price >= LUXURY_PRICE_INR
            or any(b in make_lower for b in LUXURY_BRANDS)
        )
        r["_source"] = "csv"
        records.append(r)

    log.info("CSV RTO dump: %d records from %s", len(records), csv_path.name)
    return records


def aggregate_to_pincode(records: list) -> pd.DataFrame:
    """
    Compute ev_share, car_2w_ratio, luxury_share per pincode.
    """
    by_pc = defaultdict(lambda: {"total": 0, "ev": 0, "lmv": 0, "2w": 0, "luxury": 0})

    for r in records:
        pc = str(r.get("pincode", "") or r.get("owner_pincode", "")).strip()
        if not re.match(r"^\d{6}$", pc):
            continue
        fuel  = str(r.get("fuel_type", "")).lower()
        cls   = str(r.get("vehicle_class", "")).upper()
        luxry = bool(r.get("is_luxury", False))

        by_pc[pc]["total"]  += 1
        if "electric" in fuel or fuel == "ev" or "battery" in fuel:
            by_pc[pc]["ev"] += 1
        if "lmv" in cls or "m_cab" in cls or "car" in cls.lower():
            by_pc[pc]["lmv"] += 1
        if "mcwg" in cls or "2w" in cls or "motorcycle" in cls.lower() or "scooter" in cls.lower():
            by_pc[pc]["2w"] += 1
        if luxry:
            by_pc[pc]["luxury"] += 1

    rows = []
    for pc, c in by_pc.items():
        total = max(c["total"], 1)
        rows.append({
            "pincode"     : pc,
            "ev_share"    : round(c["ev"] / total, 4),
            "car_2w_ratio": round(c["lmv"] / max(c["2w"], 1), 3),
            "luxury_share": round(c["luxury"] / total, 4),
            "sample_count": total,
        })

    df = pd.DataFrame(rows).set_index("pincode")
    log.info("RTO signals aggregated for %d pincodes", len(df))
    return df


def write_output(signals: pd.DataFrame, dry_run: bool = False) -> None:
    cols = ["ev_share", "car_2w_ratio", "luxury_share"]
    if RTO_FILE.exists():
        existing = pd.read_csv(RTO_FILE).set_index("pincode")
        existing.index = existing.index.astype(str)
        signals.index  = signals.index.astype(str)
        for col in cols:
            if col in signals.columns and col in existing.columns:
                existing.update(signals[[col]])
        out = existing
    else:
        out = signals[cols]

    if dry_run:
        log.info("[DRY RUN] rto_enhanced.csv:\n%s", out.head(10))
        return

    out.to_csv(RTO_FILE)
    log.info("Written: %s (%d rows)", RTO_FILE.name, len(out))


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf",      type=Path, help="Single RC PDF")
    ap.add_argument("--dir",      type=Path, help="Directory of RC PDFs")
    ap.add_argument("--csv",      type=Path, help="Tabular RTO data dump CSV")
    ap.add_argument("--pincode",  default="", help="Override pincode for --pdf")
    ap.add_argument("--force-ocr", action="store_true")
    ap.add_argument("--dry-run",  action="store_true")
    args = ap.parse_args()

    records = []

    if args.csv:
        records.extend(process_csv(args.csv))

    pdfs = []
    if args.pdf:
        pdfs = [args.pdf]
    elif args.dir:
        pdfs = list(args.dir.glob("*.pdf"))

    for pdf in pdfs:
        r = process_pdf(pdf, pincode=args.pincode if args.pdf else "", force_ocr=args.force_ocr)
        records.append(r)

    if not records:
        log.error("No input provided — use --pdf, --dir, or --csv")
        return

    signals = aggregate_to_pincode(records)
    write_output(signals, dry_run=args.dry_run)


if __name__ == "__main__":
    main()

"""
parse_ror.py — Extract property rate signals from ROR / land record PDFs.

Supported document types:
  - ROR (Record of Rights) — mutation entries, Jamabandi
  - Sale deeds / conveyance deeds
  - EC (Encumbrance Certificate) — shows transaction history
  - Property tax assessment extracts (municipal)

Output signal: rate_per_sqft (INR per sq ft) by pincode.

Approach:
  Tier 1 (digital PDF)  → pdfplumber extracts text → regex harvests numbers
  Tier 2 (semi-structured) → LLM extracts structured data
  Tier 3 (scanned PDF)  → marker-pdf OCR → LLM

Usage:
  # Single PDF
  python3 etl/parse_ror.py --pdf /path/to/sale_deed.pdf --pincode 110003

  # Batch: directory of PDFs (auto-detects pincodes via Nominatim if lat/lon CSV provided)
  python3 etl/parse_ror.py --dir /path/to/pdfs/

  # Dry run
  python3 etl/parse_ror.py --pdf sale_deed.pdf --pincode 110003 --dry-run
"""

import argparse
import logging
import re
from pathlib import Path

import pandas as pd

from etl.llm_extract import DocumentType, extract, pdf_to_text

log = logging.getLogger(__name__)

ETL = Path(__file__).parent.parent
RAW = ETL / "data" / "raw"
OUT_FILE = RAW / "property_rates.csv"

# ── Regex-based fast extraction for digital PDFs ──────────────────────────────

# Matches patterns like:
#   "Sale consideration: Rs. 1,45,00,000"
#   "Market value: ₹ 12000 per sq.ft"
#   "Total area: 1,200 sq.ft"
_RE_AMOUNT = re.compile(
    r"(?:sale\s*(?:consideration|value|deed\s*value)|"
    r"(?:market|circle|guidance)\s*value|"
    r"consideration\s*amount|"
    r"total\s*consideration)"
    r"[\s:\-]*(?:rs\.?|inr|₹)\s*"
    r"([\d,]+(?:\.\d+)?)",
    re.IGNORECASE,
)
_RE_AREA = re.compile(
    r"(?:total\s*area|built[\-\s]?up\s*area|plot\s*area|land\s*area)"
    r"[\s:\-]*([\d,\.]+)\s*"
    r"(sq\.?\s*ft\.?|sq\.?\s*m\.?|sqft|sqm|cent|bigha|gunta|marla|kanal|acre)",
    re.IGNORECASE,
)
_RE_RATE = re.compile(
    r"(?:rate|price)\s*(?:per\s*)?sq\.?\s*ft\.?"
    r"[\s:\-]*(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d+)?)",
    re.IGNORECASE,
)
_RE_PINCODE = re.compile(r"\b(\d{6})\b")

# Area unit converters to sq ft
UNIT_TO_SQFT = {
    "sqft": 1.0, "sq ft": 1.0, "sq.ft": 1.0, "sq.ft.": 1.0,
    "sqm": 10.764, "sq m": 10.764, "sq.m": 10.764,
    "cent": 435.6,
    "bigha": 27000.0,   # approx for UP/Delhi Bigha
    "gunta": 1089.0,
    "marla": 272.25,
    "kanal": 5445.0,
    "acre": 43560.0,
}


def _parse_number(s: str) -> float:
    return float(s.replace(",", "").strip())


def _to_sqft(value: float, unit: str) -> float:
    unit_key = unit.lower().replace(".", "").replace(" ", "")
    factor = UNIT_TO_SQFT.get(unit_key, 1.0)
    return value * factor


def extract_rate_regex(text: str) -> dict:
    """Fast regex pass for digital PDFs with predictable formatting."""
    result = {"method": "regex", "confidence": "low"}

    # Try direct rate-per-sqft mention first
    m = _RE_RATE.search(text)
    if m:
        result["rate_per_sqft"] = _parse_number(m.group(1))
        result["confidence"] = "high"
        return result

    # Derive from amount ÷ area
    m_amt  = _RE_AMOUNT.search(text)
    m_area = _RE_AREA.search(text)
    if m_amt and m_area:
        amount   = _parse_number(m_amt.group(1))
        area_val = _parse_number(m_area.group(1))
        area_sqft = _to_sqft(area_val, m_area.group(2))
        if area_sqft > 0 and amount > 0:
            rate = amount / area_sqft
            if 500 <= rate <= 500_000:   # sanity: ₹500–₹5L per sqft is plausible in India
                result["rate_per_sqft"] = round(rate, 0)
                result["transaction_value"] = amount
                result["area_sqft"] = round(area_sqft, 0)
                result["confidence"] = "medium"
                return result

    return result


def extract_pincode_from_text(text: str) -> str:
    """Find a 6-digit Indian pincode mentioned in the document text."""
    matches = _RE_PINCODE.findall(text)
    # Filter to plausible Indian pincodes (1-9 first digit)
    valid = [p for p in matches if p[0] in "123456789"]
    if valid:
        # Return most frequently mentioned
        from collections import Counter
        return Counter(valid).most_common(1)[0][0]
    return ""


def process_pdf(pdf_path: Path, pincode: str = "", force_ocr: bool = False) -> dict:
    """
    Extract rate_per_sqft from a single ROR/deed PDF.
    Returns dict with: pincode, rate_per_sqft, area_sqft, transaction_value, confidence, source.
    """
    log.info("Processing ROR: %s", pdf_path.name)

    # Get text (pdfplumber or OCR)
    text = pdf_to_text(pdf_path, force_ocr=force_ocr)

    # Try regex first (fast, no LLM)
    regex_result = extract_rate_regex(text)

    if regex_result.get("confidence") in ("high", "medium"):
        result = regex_result
        result["_source"] = "regex"
    else:
        # Fall back to LLM
        llm_result = extract(DocumentType.LAND_RECORD, text=text)
        rate = llm_result.get("rate_per_sqft") or 0.0
        if not rate and llm_result.get("transaction_value") and llm_result.get("area_sqft"):
            area = float(llm_result.get("area_sqft") or 0)
            val  = float(llm_result.get("transaction_value") or 0)
            rate = round(val / area, 0) if area > 0 else 0.0
        result = {
            "rate_per_sqft"    : rate,
            "area_sqft"        : llm_result.get("area_sqft"),
            "transaction_value": llm_result.get("transaction_value"),
            "confidence"       : llm_result.get("confidence", "low"),
            "_source"          : llm_result.get("_source", "llm"),
        }

    # Attach pincode
    if not pincode:
        pincode = extract_pincode_from_text(text)
    result["pincode"] = pincode
    result["_file"] = str(pdf_path)
    return result


def aggregate_to_pincode(records: list) -> pd.DataFrame:
    """
    Average rate_per_sqft across multiple documents per pincode.
    Uses median to reduce outlier influence from distressed sales or stamp duty anomalies.
    """
    df = pd.DataFrame(records)
    df = df[df["pincode"].astype(str).str.match(r"^\d{6}$")]
    df["rate_per_sqft"] = pd.to_numeric(df["rate_per_sqft"], errors="coerce")
    df = df.dropna(subset=["rate_per_sqft"])
    df = df[df["rate_per_sqft"] > 0]

    agg = (
        df.groupby("pincode")["rate_per_sqft"]
          .agg(["median", "count"])
          .rename(columns={"median": "rate_per_sqft", "count": "sample_count"})
    )
    agg["rate_per_sqft"] = agg["rate_per_sqft"].round(0)
    log.info("Aggregated property rates for %d pincodes (%d documents)", len(agg), len(df))
    return agg


def write_output(rates: pd.DataFrame, dry_run: bool = False) -> None:
    if OUT_FILE.exists():
        existing = pd.read_csv(OUT_FILE).set_index("pincode")
        existing.index = existing.index.astype(str)
        rates.index    = rates.index.astype(str)
        existing.update(rates[["rate_per_sqft"]])
        out = existing
    else:
        out = rates

    if dry_run:
        log.info("[DRY RUN] property_rates.csv:\n%s", out.head(10))
        return

    out.to_csv(OUT_FILE)
    log.info("Written: %s (%d pincodes)", OUT_FILE.name, len(out))


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf",      type=Path, help="Single PDF to process")
    ap.add_argument("--dir",      type=Path, help="Directory of PDFs to process")
    ap.add_argument("--pincode",  default="",  help="Pincode for the --pdf document")
    ap.add_argument("--force-ocr", action="store_true")
    ap.add_argument("--dry-run",  action="store_true")
    args = ap.parse_args()

    if not args.pdf and not args.dir:
        ap.error("Provide --pdf or --dir")

    pdfs = [args.pdf] if args.pdf else list(args.dir.glob("*.pdf"))
    log.info("%d PDFs to process", len(pdfs))

    records = []
    for pdf in pdfs:
        pc = args.pincode if args.pdf else ""
        r = process_pdf(pdf, pincode=pc, force_ocr=args.force_ocr)
        records.append(r)
        log.info("  %s → pincode=%s rate=₹%s/sqft [%s]",
                 pdf.name, r.get("pincode"), r.get("rate_per_sqft"), r.get("confidence"))

    if records:
        rates = aggregate_to_pincode(records)
        write_output(rates, dry_run=args.dry_run)


if __name__ == "__main__":
    main()

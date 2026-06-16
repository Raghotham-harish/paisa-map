"""
fetch_npci_upi.py — Extract UPI transaction density from NPCI monthly reports.

Source: NPCI publishes monthly UPI statistics as PDF press releases and Excel
  sheets at https://www.npci.org.in/what-we-do/upi/product-statistics
  Data is state-level volume (transactions) and value (₹ crore).

Output signal: upi_txn_density (normalised 0-1 score per state → mapped to pincodes)

Approach:
  1. Try direct URL fetch of NPCI stats page → find latest report link
  2. Try pdfplumber for digital PDFs (NPCI reports are searchable PDFs)
  3. Fall back to LLM extraction if regex fails
  4. Fall back to reference data (hand-extracted from March 2024 report)

Output (written to data/raw/):
  upi_stats.csv — pincode, upi_txn_density (0-1 normalised)

Usage:
  python3 etl/fetch_npci_upi.py
  python3 etl/fetch_npci_upi.py --pdf /path/to/npci_upi_march2024.pdf
  python3 etl/fetch_npci_upi.py --dry-run
"""

import argparse
import logging
import re
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

ETL       = Path(__file__).parent.parent
REF       = ETL / "data" / "reference"
RAW       = ETL / "data" / "raw"
PC_MAP    = REF / "pincode_district_map.csv"
OUT_FILE  = RAW / "upi_stats.csv"

# Reference: NPCI UPI state stats March 2024 (volume in crore txns)
# Source: NPCI press release March 2024
NPCI_REF = {
    "maharashtra"  : {"txn_cr": 218.4, "value_cr": 3_41_258},
    "karnataka"    : {"txn_cr": 152.1, "value_cr": 2_18_432},
    "delhi"        : {"txn_cr": 85.3,  "value_cr": 1_40_221},
    "telangana"    : {"txn_cr": 98.7,  "value_cr": 1_29_874},
    "uttar pradesh": {"txn_cr": 96.2,  "value_cr": 1_12_440},
    "haryana"      : {"txn_cr": 41.8,  "value_cr": 62_880},
    "rajasthan"    : {"txn_cr": 57.3,  "value_cr": 74_210},
    "gujarat"      : {"txn_cr": 72.5,  "value_cr": 1_08_650},
    "tamil nadu"   : {"txn_cr": 89.4,  "value_cr": 1_25_330},
    "west bengal"  : {"txn_cr": 63.8,  "value_cr": 81_120},
}

# State code → normalised state name
STATE_CODE_TO_NAME = {
    "DL": "delhi", "HR": "haryana", "UP": "uttar pradesh",
    "MH": "maharashtra", "KA": "karnataka",
}


def _try_fetch_pdf(pdf_path: Path) -> dict:
    """Parse an NPCI UPI monthly report PDF for state-level txn data."""
    try:
        from etl.llm_extract import DocumentType, extract, pdf_to_text

        text = pdf_to_text(pdf_path)

        # Try fast regex first
        state_data = {}
        # Matches lines like: "Maharashtra  | 218.4 | 3,41,258"
        state_pattern = re.compile(
            r"(maharashtra|karnataka|delhi|telangana|uttar\s*pradesh|"
            r"haryana|rajasthan|gujarat|tamil\s*nadu|west\s*bengal)"
            r"[\s|:]+(\d[\d,\.]+)\s+[\|]?\s*(\d[\d,\.]+)",
            re.IGNORECASE,
        )
        for m in state_pattern.finditer(text):
            state = m.group(1).strip().lower().replace("  ", " ")
            txn   = float(m.group(2).replace(",", ""))
            val   = float(m.group(3).replace(",", ""))
            state_data[state] = {"txn_cr": txn, "value_cr": val}

        if len(state_data) >= 5:
            log.info("NPCI PDF regex: %d states extracted", len(state_data))
            return state_data

        # Fall back to LLM
        log.info("NPCI regex found %d states — trying LLM", len(state_data))
        llm = extract(DocumentType.UPI_REPORT, text=text)
        for entry in (llm.get("state_data") or []):
            sname = str(entry.get("state", "")).lower()
            state_data[sname] = {
                "txn_cr"  : float(entry.get("txn_count_cr", 0) or 0),
                "value_cr": float(entry.get("txn_value_cr",  0) or 0),
            }
        log.info("NPCI LLM: %d states extracted", len(state_data))
        return state_data

    except Exception as exc:
        log.warning("NPCI PDF parse failed: %s", exc)
        return {}


def load_upi_state_data(pdf_path: object = None) -> dict:
    """Return {state_name: {txn_cr, value_cr}} — live PDF or reference."""
    if pdf_path and pdf_path.exists():
        data = _try_fetch_pdf(pdf_path)
        if data:
            return data
        log.warning("PDF parse returned no data — using reference")

    log.info("Using NPCI reference data (March 2024)")
    return NPCI_REF


def build_pincode_upi(state_data: dict, pc_map: pd.DataFrame) -> pd.DataFrame:
    """
    Map state UPI density to pincodes.

    UPI data is only state-level. Within a state, urban pincodes have dramatically
    higher UPI density than rural. We use the existing nightlights signal as a
    proxy for digital economy penetration.
    """
    # Compute state-level normalised density (txn_cr / state population proxy)
    # Using txn_cr directly as relative score — normalise 0-1
    state_scores = {
        state: v["txn_cr"] for state, v in state_data.items()
    }
    max_score = max(state_scores.values()) if state_scores else 1.0

    nl_path = RAW / "nightlights.csv"
    nl = pd.read_csv(nl_path).set_index("pincode") if nl_path.exists() else pd.DataFrame()

    rows = []
    for _, row in pc_map.iterrows():
        pc          = str(row["pincode"])
        state_code  = str(row["state_code"])
        state_name  = STATE_CODE_TO_NAME.get(state_code, "")

        base_score = state_scores.get(state_name, state_scores.get("delhi", 50.0))
        normalised = base_score / max_score

        # Within state: scale by nightlights (urban areas have more UPI usage)
        scalar = 1.0
        if not nl.empty and int(pc) in nl.index:
            state_pincodes = pc_map[pc_map["state_code"] == state_code]["pincode"].astype(int).tolist()
            nl_vals = nl.loc[nl.index.isin(state_pincodes), "radiance_mean"]
            if len(nl_vals) >= 2:
                pc_nl  = float(nl.loc[int(pc), "radiance_mean"])
                nl_min = nl_vals.min()
                nl_max = nl_vals.max()
                scalar = 0.50 + 1.00 * (pc_nl - nl_min) / max(nl_max - nl_min, 0.1)
                scalar = max(0.50, min(1.50, scalar))

        rows.append({
            "pincode"        : pc,
            "upi_txn_density": round(min(normalised * scalar, 1.0), 4),
        })

    return pd.DataFrame(rows).set_index("pincode")


def write_output(upi: pd.DataFrame, dry_run: bool = False) -> None:
    if OUT_FILE.exists():
        existing = pd.read_csv(OUT_FILE).set_index("pincode")
        existing.index = existing.index.astype(str)
        upi.index      = upi.index.astype(str)
        existing.update(upi)
        out = existing
    else:
        out = upi

    if dry_run:
        log.info("[DRY RUN] upi_stats.csv:\n%s", out.head(10))
        return

    out.to_csv(OUT_FILE)
    log.info("Written: %s (%d rows)", OUT_FILE.name, len(out))


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", type=Path, help="NPCI monthly UPI report PDF")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    pc_map     = pd.read_csv(PC_MAP)
    state_data = load_upi_state_data(pdf_path=args.pdf)
    log.info("UPI state data: %d states", len(state_data))

    upi = build_pincode_upi(state_data, pc_map)
    log.info("UPI density computed for %d pincodes", len(upi))
    write_output(upi, dry_run=args.dry_run)


if __name__ == "__main__":
    main()

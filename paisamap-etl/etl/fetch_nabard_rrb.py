"""
fetch_nabard_rrb.py — Rural financial inclusion signal from NABARD RRB data.

Source: NABARD publishes "Status of Microfinance in India" and "Annual Report"
  with district/state-level Regional Rural Bank (RRB) statistics.
  Key metrics: branch count, deposits, advances, credit-deposit ratio.

The rrb_branch_density signal proxies rural financial access and micro-lending
activity. High RRB density in peri-urban pincodes indicates growing rural
purchasing power that might not show in other signals.

Output signal: rrb_branch_density (branches per lakh population, normalised)

Reference data: NABARD Annual Report 2022-23 (state-level RRB summary)

Usage:
  python3 etl/fetch_nabard_rrb.py
  python3 etl/fetch_nabard_rrb.py --pdf /path/to/nabard_annual_2023.pdf
  python3 etl/fetch_nabard_rrb.py --dry-run
"""

import argparse
import logging
import re
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

ETL      = Path(__file__).parent.parent
REF      = ETL / "data" / "reference"
RAW      = ETL / "data" / "raw"
PC_MAP   = REF / "pincode_district_map.csv"
OUT_FILE = RAW / "rrb_density.csv"

# Reference: NABARD Annual Report 2022-23, state-level RRB branches per lakh pop
# Higher = more rural banking penetration
NABARD_REF = {
    "UP"  : {"branches_per_lakh": 8.4,  "cd_ratio": 0.58, "rrb_name": "Prathama UP Gramin Bank"},
    "MH"  : {"branches_per_lakh": 3.2,  "cd_ratio": 0.71, "rrb_name": "Maharashtra Gramin Bank"},
    "KA"  : {"branches_per_lakh": 5.1,  "cd_ratio": 0.68, "rrb_name": "Karnataka Gramin Bank"},
    "HR"  : {"branches_per_lakh": 6.8,  "cd_ratio": 0.62, "rrb_name": "Sarva Haryana Gramin Bank"},
    "DL"  : {"branches_per_lakh": 0.8,  "cd_ratio": 0.45, "rrb_name": "N/A (Urban, no RRB)"},
}

# Population estimates (2023, in lakh) for our states
STATE_POP_LAKH = {"DL": 200, "HR": 280, "UP": 2350, "MH": 1260, "KA": 670}


def _try_parse_pdf(pdf_path: Path) -> dict:
    """Extract RRB state data from NABARD annual report PDF."""
    try:
        from etl.llm_extract import DocumentType, extract, pdf_to_text

        text = pdf_to_text(pdf_path)

        # Regex: look for state + branch count + deposits table
        state_pattern = re.compile(
            r"(uttar\s*pradesh|maharashtra|karnataka|haryana|delhi)"
            r"[\s|:]+(\d[\d,\.]+)\s+[\|]?\s*(\d[\d,\.]+)",
            re.IGNORECASE,
        )
        state_data = {}
        for m in state_pattern.finditer(text):
            state = m.group(1).strip().lower().replace("  ", " ")
            branches = float(m.group(2).replace(",", ""))
            deposits = float(m.group(3).replace(",", ""))
            state_data[state] = {"branches": branches, "deposits_cr": deposits}

        if len(state_data) >= 3:
            log.info("NABARD PDF regex: %d states", len(state_data))
            return state_data

        log.info("NABARD regex found %d states — trying LLM", len(state_data))
        llm = extract(DocumentType.NABARD_RRB, text=text)
        for entry in (llm.get("state_data") or []):
            sname = str(entry.get("state", "")).lower()
            state_data[sname] = {
                "branches"   : float(entry.get("branches",    0) or 0),
                "deposits_cr": float(entry.get("deposits_cr", 0) or 0),
            }
        log.info("NABARD LLM: %d states", len(state_data))
        return state_data

    except Exception as exc:
        log.warning("NABARD PDF parse failed: %s", exc)
        return {}


def load_rrb_data(pdf_path: object = None) -> dict:
    """Return {state_code: {branches_per_lakh, cd_ratio}} — live or reference."""
    if pdf_path and pdf_path.exists():
        raw = _try_parse_pdf(pdf_path)
        if raw:
            # Convert parsed data to per-lakh format
            name_to_code = {
                "uttar pradesh": "UP", "maharashtra": "MH",
                "karnataka": "KA", "haryana": "HR", "delhi": "DL",
            }
            result = {}
            for state_name, v in raw.items():
                code = name_to_code.get(state_name)
                if not code:
                    continue
                pop = STATE_POP_LAKH.get(code, 100)
                bpl = v["branches"] / pop if v["branches"] > 0 else 0.0
                result[code] = {"branches_per_lakh": round(bpl, 2)}
            if result:
                return result
            log.warning("NABARD PDF had no usable state data — using reference")

    log.info("Using NABARD reference data (2022-23)")
    return NABARD_REF


def build_pincode_rrb(rrb: dict, pc_map: pd.DataFrame) -> pd.DataFrame:
    """
    Map state RRB density to pincodes.

    RRBs serve rural/semi-urban areas; urban pincodes within a state have LOWER
    RRB presence (they have SCBs instead). We use an inverted nightlights scalar:
    darker (more rural) pincodes get higher RRB density scores.
    """
    nl_path = RAW / "nightlights.csv"
    nl = pd.read_csv(nl_path).set_index("pincode") if nl_path.exists() else pd.DataFrame()

    # Normalise branches_per_lakh across states
    max_bpl = max((v["branches_per_lakh"] for v in rrb.values()), default=1.0)

    rows = []
    for _, row in pc_map.iterrows():
        pc         = str(row["pincode"])
        state_code = str(row["state_code"])
        state_rrb  = rrb.get(state_code, {"branches_per_lakh": 2.0})
        base_score = state_rrb["branches_per_lakh"] / max_bpl

        # Invert nightlights for RRB: rural pincodes (low NL) → higher RRB density
        scalar = 1.0
        if not nl.empty and int(pc) in nl.index:
            state_pincodes = pc_map[pc_map["state_code"] == state_code]["pincode"].astype(int).tolist()
            nl_vals = nl.loc[nl.index.isin(state_pincodes), "radiance_mean"]
            if len(nl_vals) >= 2:
                pc_nl  = float(nl.loc[int(pc), "radiance_mean"])
                nl_min = nl_vals.min()
                nl_max = nl_vals.max()
                # Inverted: low NL → higher scalar
                scalar = 1.50 - 1.00 * (pc_nl - nl_min) / max(nl_max - nl_min, 0.1)
                scalar = max(0.50, min(1.50, scalar))

        rows.append({
            "pincode"          : pc,
            "rrb_branch_density": round(min(base_score * scalar, 1.0), 4),
        })

    return pd.DataFrame(rows).set_index("pincode")


def write_output(rrb: pd.DataFrame, dry_run: bool = False) -> None:
    if OUT_FILE.exists():
        existing = pd.read_csv(OUT_FILE).set_index("pincode")
        existing.index = existing.index.astype(str)
        rrb.index      = rrb.index.astype(str)
        existing.update(rrb)
        out = existing
    else:
        out = rrb

    if dry_run:
        log.info("[DRY RUN] rrb_density.csv:\n%s", out.head(10))
        return

    out.to_csv(OUT_FILE)
    log.info("Written: %s (%d rows)", OUT_FILE.name, len(out))


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", type=Path, help="NABARD annual report PDF")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    pc_map = pd.read_csv(PC_MAP)
    rrb    = load_rrb_data(pdf_path=args.pdf)
    log.info("RRB data: %d states loaded", len(rrb))

    signals = build_pincode_rrb(rrb, pc_map)
    log.info("RRB branch density computed for %d pincodes", len(signals))
    write_output(signals, dry_run=args.dry_run)


if __name__ == "__main__":
    main()

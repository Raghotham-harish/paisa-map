"""
merge_real_signals.py — Validate and cross-check all Phase 1 signal updates.

This script runs AFTER the three fetch scripts have written their outputs to
data/raw/.  It:
  1. Loads all raw CSVs and the ML-refined output
  2. Computes a delta report showing how much each signal changed vs the old values
  3. Runs the 10-gate validation suite
  4. Prints a summary — does NOT re-run ml_refinement (that is done by run_phase1.py)

Useful for reviewing what changed before committing the updated signals.

Usage:
  python3 etl/merge_real_signals.py
"""

import logging
from pathlib import Path

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

ETL    = Path(__file__).parent.parent
REF    = ETL / "data" / "reference"
RAW    = ETL / "data" / "raw"
OUT    = ETL / "data" / "output"
PC_MAP = REF / "pincode_district_map.csv"

# The 10 validation gates (higher-income pincode MUST score higher PPI)
VALIDATION_GATES = [
    ("110003", "Golf Links",        "110040", "Narela"),
    ("110003", "Golf Links",        "110017", "Saket"),
    ("110017", "Saket",             "110040", "Narela"),
    ("122022", "Golf Course Rd GGN","122002", "Gurgaon City"),
    ("400021", "Cuffe Parade",      "400086", "Borivali"),
    ("400006", "Malabar Hill",      "400097", "Malad East"),
    ("400049", "Bandra West",       "400614", "Vashi"),
    ("560025", "Indiranagar",       "560035", "Electronic City"),
    ("560025", "Indiranagar",       "560064", "Yelahanka"),
    ("560027", "Koramangala",       "560047", "Hebbal"),
]


def load_all_signals() -> pd.DataFrame:
    """Merge all raw signal CSVs into a single wide DataFrame indexed by pincode."""
    pc_map = pd.read_csv(PC_MAP)[["pincode", "name", "district", "state_code"]]
    pc_map["pincode"] = pc_map["pincode"].astype(str)
    merged = pc_map.set_index("pincode")

    signal_files = {
        "deposits_per_capita" : (RAW / "bank_deposits.csv",      "deposits_per_capita"),
        "filers_per_capita"   : (RAW / "itr_filers.csv",         "filers_per_capita"),
        "cars_per_1000"       : (RAW / "vehicle_density.csv",     "cars_per_1000"),
        "ev_share"            : (RAW / "rto_enhanced.csv",        "ev_share"),
        "car_2w_ratio"        : (RAW / "rto_enhanced.csv",        "car_2w_ratio"),
        "luxury_share"        : (RAW / "rto_enhanced.csv",        "luxury_share"),
        "radiance_mean"       : (RAW / "nightlights.csv",         "radiance_mean"),
        "premium_poi_per_km2" : (RAW / "poi_density.csv",         "premium_poi_per_km2"),
        "rate_per_sqft"       : (RAW / "property_rates.csv",      "rate_per_sqft"),
    }

    loaded: dict[str, pd.Series] = {}
    for key, (path, col) in signal_files.items():
        if path.exists():
            df = pd.read_csv(path).set_index("pincode")
            df.index = df.index.astype(str)
            if col in df.columns:
                loaded[key] = df[col]
            else:
                # rto_enhanced has multiple cols
                for c in df.columns:
                    if c in signal_files:
                        loaded[c] = df[c]

    for key, series in loaded.items():
        merged[key] = series

    return merged


def delta_report(current: pd.DataFrame, prev_path: Path) -> None:
    """Print a before/after comparison for the signals we updated in Phase 1."""
    if not prev_path.exists():
        log.info("No previous ML output found at %s — skipping delta report", prev_path)
        return

    prev = pd.read_csv(prev_path).set_index("pincode")
    prev.index = prev.index.astype(str)

    compare_cols = ["deposits_per_capita", "filers_per_capita", "cars_per_1000", "ev_share"]
    available = [c for c in compare_cols if c in current.columns and c in prev.columns]
    if not available:
        log.info("No overlapping columns to compare")
        return

    print("\n── Phase 1 Signal Delta (sample: top 10 by PPI) ──────────────────")
    top10 = prev.sort_values("ppi_ml", ascending=False).head(10)
    for pc in top10.index:
        if pc not in current.index:
            continue
        print(f"\n  {current.loc[pc, 'name'] if 'name' in current.columns else pc} ({pc})")
        for col in available:
            old_val = prev.loc[pc, col] if col in prev.columns else None
            new_val = current.loc[pc, col] if col in current.columns else None
            if old_val is not None and new_val is not None:
                pct = (float(new_val) - float(old_val)) / max(abs(float(old_val)), 1e-9) * 100
                arrow = "▲" if pct > 2 else ("▼" if pct < -2 else "≈")
                print(f"    {col:28s}: {old_val:>12.2f}  →  {new_val:>12.2f}  {arrow} {pct:+.1f}%")
    print()


def run_validation_gates(signals: pd.DataFrame) -> int:
    """Run 10-gate PPI ordering checks using current signal data as a proxy."""
    # Use deposits_per_capita as proxy since ml_refinement hasn't run yet
    proxy_col = next(
        (c for c in ["deposits_per_capita", "rate_per_sqft", "radiance_mean"]
         if c in signals.columns),
        None,
    )
    if proxy_col is None:
        log.warning("No suitable proxy column for validation gates")
        return 0

    passed = 0
    print("── Validation Gates (proxy: %s) ─────────────────────────────────" % proxy_col)
    for pc_a, name_a, pc_b, name_b in VALIDATION_GATES:
        val_a = signals[proxy_col].get(pc_a)
        val_b = signals[proxy_col].get(pc_b)
        if val_a is None or val_b is None:
            print(f"  [SKIP] {name_a} vs {name_b} — missing data")
            continue
        ok = float(val_a) > float(val_b)
        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        print(f"  [{status}] {name_a} ({float(val_a):,.0f}) > {name_b} ({float(val_b):,.0f})")

    print(f"\n  Gates passed: {passed}/10")
    return passed


def coverage_report(signals: pd.DataFrame) -> None:
    """Report which signals have real (Phase 1) vs modelled values."""
    phase1_cols = {
        "deposits_per_capita" : "RBI BSR 2023",
        "filers_per_capita"   : "CBDT ITR AY2023",
        "cars_per_1000"       : "VAHAN 2024",
        "ev_share"            : "VAHAN 2024",
        "car_2w_ratio"        : "VAHAN 2024",
    }
    keep_cols = {
        "radiance_mean"       : "Nightlights (satellite)",
        "premium_poi_per_km2" : "Overpass / OSM",
        "rate_per_sqft"       : "Market property rates",
    }

    print("\n── Signal Coverage ───────────────────────────────────────────────")
    for col, src in {**phase1_cols, **keep_cols}.items():
        filled = signals[col].notna().sum() if col in signals.columns else 0
        total  = len(signals)
        tag    = "● REAL DATA" if col in phase1_cols else "◌ unchanged"
        print(f"  {tag}  {col:28s}: {filled}/{total} pincodes  [{src}]")
    print()


def main():
    signals = load_all_signals()
    log.info("Signals loaded for %d pincodes", len(signals))

    ml_path = OUT / "ppi_ml_refined.csv"
    delta_report(signals, ml_path)
    coverage_report(signals)
    run_validation_gates(signals)

    print("Next step: run  python3 run_phase1.py  to re-run ML refinement with updated signals.")


if __name__ == "__main__":
    main()

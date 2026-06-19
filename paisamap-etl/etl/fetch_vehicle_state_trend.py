#!/usr/bin/env python3
"""
fetch_vehicle_state_trend.py — Parse RS_Session_248 vehicle registration CSV.

Source: Rajya Sabha AU 978 (Session 248) — total registered vehicles by state/UT,
quarterly Q1 2014-15 through Q2 2018-19 (18 quarters, cumulative stock).

Outputs data/raw/vehicle_state_trend.csv with:
  state_name, vehicles_base (Q1 FY2014-15), vehicles_latest (Q2 FY2018-19),
  growth_4yr_pct, calibration_weight

calibration_weight = (growth_4yr_pct / national_median_growth) ^ 0.2
  Dampened: a state with 2× median growth only gets ~1.15× PPI nudge.

Usage:
  python3 etl/fetch_vehicle_state_trend.py
  python3 etl/fetch_vehicle_state_trend.py --csv /path/to/rs248.csv
"""

import argparse
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW  = ROOT / "data" / "raw"
REF  = ROOT / "data" / "reference"

DEFAULT_CSV = REF / "rs248_vehicle_state.csv"

# Map RS_Session state names → internal state_name (matches pincode_district_map)
STATE_FIX = {
    "NCT of Delhi":           "Delhi",
    "Andaman & Nicobar Islands": "Andaman and Nicobar Islands",
    "Dadra & Nagar Haveli":   "Dadra and Nagar Haveli",
    "Daman & Diu":            "Daman and Diu",
    "Jammu & Kashmir":        "Jammu and Kashmir",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV,
                    help="RS_Session_248 vehicle CSV (default: data/reference/rs248_vehicle_state.csv)")
    args = ap.parse_args()

    if not args.csv.exists():
        print(f"ERROR: file not found: {args.csv}")
        print("  Copy RS_Session_248_AU_978.2.csv to data/reference/rs248_vehicle_state.csv")
        raise SystemExit(1)

    print(f"\nParsing vehicle state trend from {args.csv.name} ...")
    df = pd.read_csv(args.csv)

    # Columns: Sl. No. | States/UTs | 2018-19: Q2 | ... | 2014-15: Q1
    state_col   = "States/UTs"
    latest_col  = "2018-19: Q2"    # most recent quarter in dataset
    base_col    = "2014-15: Q1"    # earliest quarter

    # Drop header/footer rows that sneak in
    df = df.dropna(subset=[state_col, latest_col, base_col])
    df = df[df[state_col].str.strip() != ""]

    df["state_name"]       = df[state_col].str.strip().replace(STATE_FIX)
    df["vehicles_latest"]  = pd.to_numeric(df[latest_col].astype(str).str.replace(",", ""), errors="coerce")
    df["vehicles_base"]    = pd.to_numeric(df[base_col].astype(str).str.replace(",", ""), errors="coerce")

    df = df.dropna(subset=["vehicles_latest", "vehicles_base"])
    df = df[df["vehicles_base"] > 0]

    df["growth_4yr_pct"] = ((df["vehicles_latest"] / df["vehicles_base"]) - 1) * 100

    national_median_growth = df["growth_4yr_pct"].median()
    print(f"  National median 4yr vehicle growth: {national_median_growth:.1f}%")

    df["calibration_weight"] = (df["growth_4yr_pct"] / national_median_growth).clip(lower=0.2) ** 0.2

    out = df[["state_name", "vehicles_base", "vehicles_latest",
              "growth_4yr_pct", "calibration_weight"]].copy()
    out["growth_4yr_pct"]     = out["growth_4yr_pct"].round(1)
    out["calibration_weight"] = out["calibration_weight"].round(4)

    out_path = RAW / "vehicle_state_trend.csv"
    out.to_csv(out_path, index=False)
    print(f"  Written: {out_path}  ({len(out)} states)")

    # Print the 6 states that matter for our pincodes
    our_states = {"Delhi", "Haryana", "Karnataka", "Maharashtra", "Punjab", "Uttar Pradesh"}
    subset = out[out["state_name"].isin(our_states)].sort_values("growth_4yr_pct", ascending=False)
    print(f"\n  Our 6 states:")
    print(subset[["state_name", "vehicles_latest", "growth_4yr_pct", "calibration_weight"]].to_string(index=False))


if __name__ == "__main__":
    main()

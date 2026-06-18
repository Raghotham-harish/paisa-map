#!/usr/bin/env python3
"""
fetch_hces_mpce.py — HCES 2023-24 District-Level MPCE signal for PaisaMap

Source: HCES (Household Consumption Expenditure Survey) 2023-24
        District-Level Fractile Classes of MPCE in India
        https://github.com/Ayansheikh034/District-Level-Fractile-Classes-of-MPCE-in-India--HCES-2023-24

Data: 628 districts × 12 fractile classes × 2 sectors (Rural/Urban)
      MPCE = Monthly Per Capita Consumption Expenditure in ₹

Signal computed per district:
  mpce_combined  — expenditure-weighted mean MPCE across all fractiles + both sectors
  mpce_rural     — rural-only weighted mean MPCE
  mpce_urban     — urban-only weighted mean MPCE
  mpce_p50       — 50th-percentile fractile MPCE (median household)
  ppi_signal     — 0–100 normalised from min/max MPCE across all districts

This is a DIRECT government income signal — far stronger than proxy signals
(vehicle density, bank branch count etc.) because it measures actual spending.

Output: data/raw/hces_mpce.csv
Usage:
  python3 fetch_hces_mpce.py                         # fetch from GitHub
  python3 fetch_hces_mpce.py --local file.csv        # use downloaded file
"""

import argparse
import csv
import io
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data" / "raw"
OUT_CSV  = DATA_DIR / "hces_mpce.csv"

SOURCE_URL = (
    "https://raw.githubusercontent.com/Ayansheikh034/"
    "District-Level-Fractile-Classes-of-MPCE-in-India--HCES-2023-24/"
    "main/district_fractile_summary.csv"
)

OUT_FIELDS = [
    "state", "district",
    "mpce_combined", "mpce_rural", "mpce_urban", "mpce_p50",
    "ppi_signal",
    "survey_year", "source",
]

# Fractile bands that straddle the 50th percentile (use for p50 proxy)
P50_FRACTILES = {"40-50", "50-60"}

# Excel corrupts "5-10" → "05-Oct" and "10-20" → "Oct-20" when saved as CSV
FRACTILE_FIX = {
    "05-Oct": "5-10",
    "Oct-20": "10-20",
}

# State name normalisation to match our pincode_district_map.csv
STATE_NORM = {
    "J & K":               "JAMMU AND KASHMIR",
    "Jammu & Kashmir":     "JAMMU AND KASHMIR",
    "Chattisgarh":         "CHHATTISGARH",
    "Uttrakhand":          "UTTARAKHAND",
    "Odisha":              "ODISHA",
    "Tamilnadu":           "TAMIL NADU",
    "A & N Islands":       "ANDAMAN AND NICOBAR ISLANDS",
    "D & N Haveli":        "DADRA AND NAGAR HAVELI",
    "Pondicherry":         "PUDUCHERRY",
    "NCT Delhi":           "DELHI",
    "Delhi":               "DELHI",
}


def _fetch_csv(url: str) -> str:
    print(f"  Downloading HCES district MPCE data...")
    req = urllib.request.Request(url, headers={"User-Agent": "PaisaMap-ETL/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        content = r.read().decode(errors="ignore")
    print(f"  Downloaded {len(content)//1024}KB")
    return content


def _norm_state(name: str) -> str:
    name = name.strip()
    return STATE_NORM.get(name, name.upper())


def _norm_district(name: str) -> str:
    return name.strip().upper()


def compute_district_mpce(raw_rows: list) -> list:
    """
    Collapse 12 fractile bands × 2 sectors per district into per-district MPCE metrics.
    Weighting: tot_exp (total expenditure of households in that band) is the weight.
    """
    # Key: (state_name, district_name) → accumulators
    acc = defaultdict(lambda: {
        "tot_w": 0.0, "wmean": 0.0,      # combined (all sectors)
        "r_w": 0.0,   "r_wmean": 0.0,    # rural sector=1
        "u_w": 0.0,   "u_wmean": 0.0,    # urban sector=2
        "p50_mpce": 0.0, "p50_w": 0.0,   # 40-60 percentile bands
    })

    skipped = 0
    for row in raw_rows:
        state    = row.get("State_Name", "").strip()
        district = row.get("District_Name", "").strip()
        sector   = row.get("Sector", "").strip()
        fractile = FRACTILE_FIX.get(row.get("fractile", ""), row.get("fractile", "")).strip()
        if not state or not district or not fractile:
            skipped += 1
            continue

        try:
            tot_exp = float(row.get("tot_exp") or 0)
            mpce    = float(row.get("mpce") or 0)
        except ValueError:
            skipped += 1
            continue

        if tot_exp <= 0 or mpce <= 0:
            skipped += 1
            continue

        key = (state, district)
        a   = acc[key]

        a["tot_w"]  += tot_exp
        a["wmean"]  += tot_exp * mpce

        if sector == "1":       # Rural
            a["r_w"]      += tot_exp
            a["r_wmean"]  += tot_exp * mpce
        elif sector == "2":     # Urban
            a["u_w"]      += tot_exp
            a["u_wmean"]  += tot_exp * mpce

        if fractile in P50_FRACTILES:
            a["p50_w"]    += tot_exp
            a["p50_mpce"] += tot_exp * mpce

    if skipped:
        print(f"  Skipped {skipped} malformed rows")

    results = []
    for (state, district), a in acc.items():
        if a["tot_w"] <= 0:
            continue
        results.append({
            "state":        _norm_state(state),
            "district":     _norm_district(district),
            "mpce_combined": round(a["wmean"]  / a["tot_w"],  0),
            "mpce_rural":    round(a["r_wmean"] / a["r_w"],   0) if a["r_w"] > 0 else 0,
            "mpce_urban":    round(a["u_wmean"] / a["u_w"],   0) if a["u_w"] > 0 else 0,
            "mpce_p50":      round(a["p50_mpce"] / a["p50_w"], 0) if a["p50_w"] > 0 else 0,
            "ppi_signal":    "",    # filled in next pass
            "survey_year":   "2023-24",
            "source":        "HCES 2023-24 district fractile summary (GitHub/MoSPI)",
        })

    return results


def normalise_ppi(records: list) -> list:
    """Min-max scale mpce_combined → ppi_signal (0–100)."""
    vals = [r["mpce_combined"] for r in records if r["mpce_combined"] > 0]
    if not vals:
        return records
    lo, hi = min(vals), max(vals)
    span = hi - lo or 1
    for r in records:
        if r["mpce_combined"] > 0:
            r["ppi_signal"] = round(100 * (r["mpce_combined"] - lo) / span, 1)
    return records


def write_csv(records: list, out_path: Path = OUT_CSV) -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    existing = []
    if out_path.exists():
        with open(out_path, newline="") as f:
            existing = list(csv.DictReader(f))

    exist_keys = {(r["state"], r["district"]) for r in existing}
    new_rows   = [r for r in records if (r["state"], r["district"]) not in exist_keys]
    all_rows   = existing + new_rows

    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OUT_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_rows)

    print(f"\n  → {out_path}  ({len(all_rows)} districts total, +{len(new_rows)} new)")
    return len(new_rows)


def print_summary(records: list):
    top    = sorted(records, key=lambda r: r["mpce_combined"], reverse=True)
    bottom = top[-8:]
    print(f"\n{'State':<22} {'District':<28} {'MPCE ₹':>7} {'Rural':>6} {'Urban':>6} {'PPI':>5}")
    print("─" * 78)
    for r in top[:12]:
        print(f"{r['state']:<22} {r['district']:<28} {r['mpce_combined']:>7.0f} "
              f"{r['mpce_rural']:>6.0f} {r['mpce_urban']:>6.0f} {r['ppi_signal']:>5}")
    print("  ...")
    for r in bottom:
        print(f"{r['state']:<22} {r['district']:<28} {r['mpce_combined']:>7.0f} "
              f"{r['mpce_rural']:>6.0f} {r['mpce_urban']:>6.0f} {r['ppi_signal']:>5}")

    vals = [r["mpce_combined"] for r in records]
    vals.sort()
    print(f"\n  Districts: {len(records)}")
    print(f"  MPCE range: ₹{vals[0]:,.0f} – ₹{vals[-1]:,.0f}")
    print(f"  Median MPCE: ₹{vals[len(vals)//2]:,.0f}")
    print(f"  PPI signal range: {min(r['ppi_signal'] for r in records if r['ppi_signal'] != '')} – "
          f"{max(r['ppi_signal'] for r in records if r['ppi_signal'] != '')}")


def run(csv_content: str) -> list:
    reader = csv.DictReader(io.StringIO(csv_content))
    raw_rows = list(reader)
    print(f"  Raw rows: {len(raw_rows)}")

    records = compute_district_mpce(raw_rows)
    records = normalise_ppi(records)
    records.sort(key=lambda r: r["mpce_combined"], reverse=True)
    print(f"  Districts computed: {len(records)}")
    return records


def main():
    ap = argparse.ArgumentParser(description="Fetch HCES 2023-24 district MPCE signal")
    ap.add_argument("--local", metavar="PATH", default="",
                    help="Path to local district_fractile_summary.csv (skip download)")
    ap.add_argument("--out", default=str(OUT_CSV), help="Output CSV path")
    ap.add_argument("--no-write", action="store_true", help="Print only, don't write CSV")
    args = ap.parse_args()

    print("\nHCES 2023-24 District MPCE Parser")
    print("  Source: MoSPI HCES district fractile data")

    if args.local and Path(args.local).exists():
        csv_content = Path(args.local).read_text(errors="ignore")
        print(f"  Using local file: {args.local}  ({len(csv_content)//1024}KB)")
    else:
        csv_content = _fetch_csv(SOURCE_URL)

    records = run(csv_content)

    if records:
        print_summary(records)
        if not args.no_write:
            write_csv(records, Path(args.out))
    else:
        print("  No records computed — check input file")
        sys.exit(1)


if __name__ == "__main__":
    main()

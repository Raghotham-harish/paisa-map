#!/usr/bin/env python3
"""
fetch_wdi_baseline.py — World Development Indicators (World Bank) calibration pull

Extracts India-level macroeconomic anchors from the WDI dataset on HuggingFace
(datonic/world_development_indicators) and writes wdi_baseline.json.

These are COUNTRY-LEVEL signals — not geographic differentiators — but they
serve two purposes in PaisaMap:
  1. Calibrate the PPI scale (PPI=100 → what monthly spend in ₹?)
  2. Constrain ML outputs (top decile PPI should match top-quintile income share)

Key anchors extracted:
  gdp_per_capita_ppp_usd    — India GDP/capita PPP 2024 in int'l $
  hh_consumption_ppp_usd    — Household final consumption/capita PPP 2024
  hh_consumption_usd_2015   — Same in constant 2015 USD (for time-series compare)
  ppp_lcu_per_usd           — PPP conversion factor ₹/$ (households, 2025)
  gini_index                — Gini 2022 (25.5 — relatively low for emerging mkt)
  poverty_3usd_pct          — % below $3/day PPP 2022 (5.3%)
  poverty_8usd_pct          — % below $8.30/day PPP 2022 (82.1%)
  income_share_bottom20     — Bottom quintile income share (10.4%)
  income_share_top20        — Top quintile income share (36.1%)
  bank_account_pct          — Financial account ownership % 2024 (89%)
  urban_pct                 — Urban population % 2024 (35.4%)
  wage_workers_pct          — Formal wage employment % 2025 (25.1%)

Usage:
  python3 fetch_wdi_baseline.py              # downloads fresh parquet from HuggingFace
  python3 fetch_wdi_baseline.py --local /tmp/wdi.parquet   # use cached parquet

Output:
  data/raw/wdi_baseline.json
"""

import argparse
import json
import sys
import urllib.request
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data" / "raw"
OUT_JSON = DATA_DIR / "wdi_baseline.json"

WDI_PARQUET_URL = (
    "https://huggingface.co/datasets/datonic/world_development_indicators"
    "/resolve/refs%2Fconvert%2Fparquet/default/train/0000.parquet"
)

# (indicator_name_prefix, output_key, description)
INDICATORS = [
    ("GDP per capita, PPP (current international $)",
     "gdp_per_capita_ppp_usd",
     "India GDP per capita PPP 2024 (current int'l $)"),

    ("Households and NPISHs Final consumption expenditure per capita, PPP (current international $)",
     "hh_consumption_ppp_usd",
     "Household consumption per capita PPP (current int'l $)"),

    ("Households and NPISHs Final consumption expenditure per capita (constant 2015 US$)",
     "hh_consumption_usd_2015",
     "Household consumption per capita (constant 2015 USD)"),

    ("PPP conversion factor, households and NPISHs Final consumption expenditure (LCU",
     "ppp_lcu_per_usd",
     "PPP conversion factor ₹ per int'l $ (households)"),

    ("Gini index",
     "gini_index",
     "Gini income inequality index (2022)"),

    ("Poverty headcount ratio at $3.00 a day (2021 PPP) (% of population)",
     "poverty_3usd_pct",
     "% population below $3/day PPP"),

    ("Poverty headcount ratio at $8.30 a day (2021 PPP) (% of population)",
     "poverty_8usd_pct",
     "% population below $8.30/day PPP"),

    ("Income share held by lowest 20%",
     "income_share_bottom20",
     "Bottom quintile income share (%)"),

    ("Income share held by highest 20%",
     "income_share_top20",
     "Top quintile income share (%)"),

    ("Account ownership at a financial institution or with a mobile-money-service prov",
     "bank_account_pct",
     "Financial account ownership % (adults)"),

    ("Urban population (% of total population)",
     "urban_pct",
     "Urban population share (%)"),

    ("Wage and salaried workers, total (% of total employment) (modeled ILO estimate)",
     "wage_workers_pct",
     "Formal wage employment as % of total employment"),
]


def _download_parquet(dest: Path) -> None:
    print("  Downloading WDI parquet from HuggingFace (~150 MB)...")
    req = urllib.request.Request(
        WDI_PARQUET_URL,
        headers={"User-Agent": "PaisaMap-ETL/1.0"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as f:
        total = 0
        while chunk := resp.read(1 << 20):
            f.write(chunk)
            total += len(chunk)
    print(f"  Downloaded {total/1e6:.1f} MB → {dest}")


def _load_parquet(path: Path):
    try:
        import pyarrow.parquet as pq  # type: ignore
        return pq.read_table(path).to_pandas()
    except ImportError:
        sys.exit("pyarrow not installed — run: pip install pyarrow pandas")


def _latest_value(df, name: str):
    """Return (year, float_value) for most recent India row — exact name match first,
    then prefix fallback. Exact match prevents ambiguity with similarly-named indicators
    (e.g. growth-rate variants that share the same 60-char prefix)."""
    india = df[df["country_code"] == "IND"]

    # Try exact match first
    sub = india[(india["indicator_name"] == name) & india["indicator_value"].notna()]
    if sub.empty:
        # Prefix fallback (safe for short/unambiguous indicators like "Gini index")
        sub = india[
            india["indicator_name"].str.startswith(name) &
            india["indicator_value"].notna()
        ]
    sub = sub.sort_values("year")
    if sub.empty:
        return None, None
    row = sub.iloc[-1]
    try:
        return int(row["year"]), float(row["indicator_value"])
    except (ValueError, TypeError):
        return int(row["year"]), None


def _derive_rupee_anchors(baseline: dict) -> dict:
    """
    Compute ₹-denominated anchors that PaisaMap can use directly.

    PPP conversion factor tells us how many ₹ = 1 international dollar
    (purchasing-power-parity adjusted).  Multiply USD PPP figures by this.
    """
    ppp = baseline.get("ppp_lcu_per_usd", {}).get("value")   # ₹ per int'l $
    hh  = baseline.get("hh_consumption_ppp_usd", {}).get("value")

    if ppp and hh:
        annual_inr = hh * ppp
        monthly_inr = annual_inr / 12
        baseline["hh_monthly_spend_inr"] = {
            "value": round(monthly_inr, 0),
            "year": baseline["hh_consumption_ppp_usd"]["year"],
            "description": "Estimated avg monthly household spend in ₹ (PPP-adjusted)",
        }
        # PPI calibration: median Indian household ≈ PPI 75 (mid-tier)
        # PPI 100 ~ 1.5× median → ~1.5 × monthly_inr
        baseline["ppi100_monthly_inr"] = {
            "value": round(monthly_inr * 1.5, 0),
            "description": "Estimated monthly spend ₹ for PPI=100 household (1.5× national avg)",
        }

    return baseline


def run(parquet_path: Path) -> dict:
    print(f"  Loading parquet: {parquet_path} ({parquet_path.stat().st_size/1e6:.0f} MB)")
    df = _load_parquet(parquet_path)

    baseline = {}
    for prefix, key, desc in INDICATORS:
        year, val = _latest_value(df, prefix)
        if val is not None:
            baseline[key] = {"value": val, "year": year, "description": desc}
            print(f"  {key:<30}  {year}  {val:.2f}")
        else:
            print(f"  {key:<30}  NOT FOUND")

    baseline = _derive_rupee_anchors(baseline)
    baseline["_source"]  = "datonic/world_development_indicators (HuggingFace)"
    baseline["_country"] = "IND"
    return baseline


def main():
    ap = argparse.ArgumentParser(description="Pull India WDI calibration baseline")
    ap.add_argument("--local", metavar="PATH", default="",
                    help="Use a local WDI parquet instead of downloading")
    ap.add_argument("--out", default=str(OUT_JSON), help="Output JSON path")
    args = ap.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if args.local and Path(args.local).exists():
        parquet_path = Path(args.local)
    else:
        parquet_path = DATA_DIR / "wdi.parquet"
        if not parquet_path.exists():
            _download_parquet(parquet_path)

    print("\nWDI Baseline Extractor")
    baseline = run(parquet_path)

    out = Path(args.out)
    with open(out, "w") as f:
        json.dump(baseline, f, indent=2)

    print(f"\n  → {out}")

    # Print derived ₹ anchors
    if "hh_monthly_spend_inr" in baseline:
        m = baseline["hh_monthly_spend_inr"]["value"]
        p = baseline.get("ppi100_monthly_inr", {}).get("value", "—")
        print(f"\n  India avg monthly household spend  ≈  ₹{m:,.0f}/month")
        print(f"  PaisaMap PPI=100 household spend   ≈  ₹{p:,.0f}/month")
        print(f"  Gini = {baseline.get('gini_index',{}).get('value','—')}  "
              f"(bottom 20% get {baseline.get('income_share_bottom20',{}).get('value','—')}%,  "
              f"top 20% get {baseline.get('income_share_top20',{}).get('value','—')}%)")
        print(f"  82% below $8.30/day PPP → wide PPI spread is realistic\n")


if __name__ == "__main__":
    main()

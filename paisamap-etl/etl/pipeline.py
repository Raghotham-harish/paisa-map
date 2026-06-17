"""
PaisaMap Phase-0 ETL — composite Purchasing Power Index per pin code.

Design rules:
  * Every proxy arrives as a CSV keyed on `pincode` (district sources are
    downscaled upstream or via `downscale_district()` here).
  * Missing proxies for a pin code -> weight is renormalized across the
    proxies that ARE present (never silently imputed as average).
  * All figures are area-level aggregates. No individual-level data, ever.
"""
from __future__ import annotations
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW, OUT = ROOT / "data" / "raw", ROOT / "data" / "output"

# ----------------------------------------------------------------------
# Proxy registry: filename -> (value column, weight)
# Replace the synthetic CSVs in data/raw with real extracts; nothing else changes.
PROXIES = {
    "property_rates.csv":      ("rate_per_sqft",         0.25),
    "bank_deposits.csv":       ("deposits_per_capita",   0.23),
    "vehicle_density.csv":     ("cars_per_1000",         0.15),
    "nightlights.csv":         ("radiance_mean",         0.15),
    "itr_filers.csv":          ("filers_per_capita",     0.10),
    "poi_density.csv":         ("premium_poi_per_km2",   0.08),
    "financial_inclusion.csv": ("fin_density_per_km2",   0.04),
    # Weight note: financial_inclusion added at 0.04 (small — OSM coverage uneven).
    # bank_deposits reduced 0.25→0.23 to keep total = 1.00.
    # When financial_inclusion.csv is absent, pipeline skips it gracefully.
}

HCES_URBAN_MPCE = 6_996          # ₹/person/month, HCES 2023-24 urban fact sheet
AVG_HH_SIZE     = 4.1            # urban household size assumption (document!)

# ----------------------------------------------------------------------
def winsorized_z(s: pd.Series, p_lo=0.01, p_hi=0.99) -> pd.Series:
    """Z-score after clipping tails so one Ambani pin code can't warp the scale."""
    clipped = s.clip(s.quantile(p_lo), s.quantile(p_hi))
    sd = clipped.std(ddof=0)
    return (clipped - clipped.mean()) / sd if sd > 0 else clipped * 0.0


def downscale_district(district_df: pd.DataFrame, mapping_df: pd.DataFrame,
                       value_col: str) -> pd.DataFrame:
    """district-keyed value -> pin codes via population share.
    mapping_df: pincode, district, pop_share (shares sum to 1 within district)."""
    m = mapping_df.merge(district_df, on="district", how="left")
    m[value_col] = m[value_col]          # per-capita values pass through as-is
    return m[["pincode", value_col]]


def load_proxies() -> pd.DataFrame:
    frames = []
    for fname, (col, _) in PROXIES.items():
        f = RAW / fname
        if not f.exists():
            print(f"  · skipping {fname} (not present)")
            continue
        df = pd.read_csv(f, dtype={"pincode": str})[["pincode", col]]
        frames.append(df.set_index("pincode")[col].rename(fname))
    if not frames:
        raise SystemExit("No proxy files found in data/raw/")
    return pd.concat(frames, axis=1)


def compute_ppi(raw: pd.DataFrame) -> pd.DataFrame:
    z = raw.apply(winsorized_z)
    weights = pd.Series({f: w for f, (_, w) in PROXIES.items() if f in z.columns})

    # per-row weight renormalization over non-missing proxies
    mask = z.notna()
    eff_w = mask.mul(weights, axis=1)
    eff_w = eff_w.div(eff_w.sum(axis=1), axis=0)
    composite = (z.fillna(0) * eff_w).sum(axis=1)

    out = pd.DataFrame({"composite_z": composite.round(3)})
    out["ppi"] = (100 + 30 * composite).clip(40, 200).round(0).astype(int)
    out["coverage"] = mask.sum(axis=1).astype(str) + f"/{len(weights)}"
    return out


def estimate_income_spend(out: pd.DataFrame) -> pd.DataFrame:
    """Anchor ₹ estimates to HCES. Income via log-linear lift on the composite;
    spend share declines with affluence (Engel-style)."""
    base_hh_spend = HCES_URBAN_MPCE * AVG_HH_SIZE                 # ₹/household/mo
    lift = np.exp(0.55 * out["composite_z"])                      # composite -> ₹ lift
    spend = base_hh_spend * lift
    spend_share = np.clip(0.82 - 0.10 * out["composite_z"], 0.45, 0.85)
    out["est_monthly_spend_hh"] = spend.round(-2)
    out["est_monthly_income_hh"] = (spend / spend_share).round(-2)
    out["est_annual_spend_hh"] = (out["est_monthly_spend_hh"] * 12).round(-3)
    return out


def validate(out: pd.DataFrame) -> list[str]:
    report = []
    gates = [("110003", "110017"), ("110017", "110040")]   # Golf Links > Saket > Narela
    for hi, lo in gates:
        if hi in out.index and lo in out.index:
            ok = out.loc[hi, "ppi"] > out.loc[lo, "ppi"]
            report.append(f"{'PASS' if ok else 'FAIL'}  PPI({hi}) > PPI({lo}) "
                          f"[{out.loc[hi,'ppi']} vs {out.loc[lo,'ppi']}]")
    # leave-one-out stability
    raw = load_proxies()
    for fname in list(PROXIES):
        if fname not in raw.columns:
            continue
        reduced = compute_ppi(raw.drop(columns=fname))
        max_swing = (out["ppi"] - reduced["ppi"]).abs().max()
        report.append(f"{'PASS' if max_swing <= 10 else 'WARN'}  drop "
                      f"{fname.split('.')[0]}: max PPI swing {max_swing:.0f} pts")
    return report


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    print("Loading proxies…")
    raw = load_proxies()
    print(f"  {raw.shape[0]} pin codes × {raw.shape[1]} proxies")

    out = estimate_income_spend(compute_ppi(raw))
    names = RAW / "pincode_names.csv"
    if names.exists():
        nm = pd.read_csv(names, dtype={"pincode": str}).set_index("pincode")
        out = nm.join(out, how="right")

    out.index.name = "pincode"
    out.sort_values("ppi", ascending=False).to_csv(OUT / "ppi_pincode.csv")

    report = validate(out)
    (OUT / "ppi_summary.md").write_text(
        "# PaisaMap PPI — validation report\n\n" + "\n".join(f"- {r}" for r in report) + "\n")
    print("\n".join(report))
    print(f"\nWrote {OUT/'ppi_pincode.csv'} and ppi_summary.md")


if __name__ == "__main__":
    main()

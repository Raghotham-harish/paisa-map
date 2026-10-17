#!/usr/bin/env python3
"""
downscale_mpce.py — household spending per person (MPCE) for each pincode,
split down from the HCES 2023-24 district figure.

Why: HCES gives ONE spending number per district (plus an urban and a rural
one). Every pincode in Bengaluru Urban gets the same value, so it can't tell
Indiranagar from a village at the district's edge. This spreads the district
figure across its pincodes using the only signals we have that really vary
INSIDE a district (measured 17 Oct 2026 — night lights, ITR filers, deposits
and vehicle signals are one value for ~98% of a district's pincodes, so they
can't split anything):

  * Overture shop density (retail_per_km2, CDLA-Permissive-2.0 / Apache-2.0 / CC0)
  * GHSL built-up share (CC BY 4.0, EU JRC)
  * WorldPop population (CC BY 4.0) — the weights, and the urban/rural split

Method (standard small-area estimation, kept deliberately simple):
  1. Index s = a·log(1+shops/km²) + b·built-up share. a, b are learned from how
     those signals line up with real HCES spending ACROSS districts of the same
     state (state averages removed, ridge, leave-one-district-out score reported).
  2. Each pincode is urban if WorldPop density ≥ 400 people/km² (the Census
     density rule), else rural.
  3. Where HCES has both an urban and a rural figure for the district, urban
     pincodes are anchored to the URBAN figure and rural ones to the RURAL figure
     (real survey information inside the district); within each class the index
     orders them: m = anchor × e^s / (population-weighted mean of e^s in the class).
     Otherwise (e.g. Delhi, Mumbai: urban only) the combined figure is the anchor.
  4. The whole district is rescaled so its population-weighted mean is EXACTLY
     the HCES combined figure — the survey total is never changed, only split.
  5. Ratios to the district figure are capped to [RATIO_MIN, RATIO_MAX] (then the
     district is re-benchmarked), so one odd pincode can't run away.

It is an ESTIMATE, labelled as such (`method`, `source`); not a pincode
statistic. It is not wired into the model or any paid surface yet — it is the
candidate training target for the retrain backtest (founder decision 17 Oct).
Licence-clean inputs only: SHRUG (CC BY-NC-SA) and Meta RWI (licence unclear)
are deliberately NOT used.

  python3 etl/downscale_mpce.py            # writes data/raw/mpce_pincode_est.csv + report
  python3 etl/downscale_mpce.py --dry-run
"""

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "output"
OUT_CSV = RAW / "mpce_pincode_est.csv"
REPORT = OUT / "mpce_downscale_report.json"

URBAN_DENSITY = 400          # people / km², Census town density criterion
RIDGE_LAMBDA = 50.0          # on standardised inputs; LOO score is flat across 1-50
RATIO_MIN, RATIO_MAX = 0.33, 3.0
MIN_STATE_DISTRICTS = 3      # states with fewer can't inform a within-state fit
SOURCE = ("PaisaMap estimate: HCES 2023-24 district MPCE (MoSPI) split by WorldPop 2020, "
          "GHSL built-up 2020 and Overture Places shop density")
LICENCE = "derived; inputs GODL-India (HCES, DoP PIN boundaries), CC BY 4.0 (WorldPop, GHSL), CDLA-P-2.0/Apache-2.0/CC0 (Overture)"

# The refit's ordering gates (ml_refinement.py): reported, not enforced here.
GATES = [("110003", "110017", "Golf Links > Saket"), ("110017", "110040", "Saket > Narela"),
         ("110003", "110075", "Golf Links > Dwarka"), ("122022", "122002", "Golf Course Rd > Gurgaon City"),
         ("400021", "400068", "Nariman Point > Dahisar"), ("400006", "400097", "Malabar Hill > Malad East"),
         ("400049", "400703", "Juhu > Vashi"), ("560038", "560100", "Indiranagar > Electronics City"),
         ("560038", "560064", "Indiranagar > Yelahanka"), ("560034", "560024", "Koramangala > Hebbal (H.A. Farm)")]


def _read(name, cols):
    return pd.read_csv(RAW / name, dtype={"pincode": str}, usecols=["pincode"] + cols)


def load_inputs() -> pd.DataFrame:
    m = _read("mpce_district.csv", ["mpce_combined", "mpce_rural", "mpce_urban", "hces_district", "hces_state"])
    m = m.dropna(subset=["mpce_combined"])
    m = m[m.mpce_combined > 0].drop_duplicates("pincode")
    m["dkey"] = m.hces_state + "|" + m.hces_district
    df = (m.merge(_read("worldpop_pincode_population.csv", ["population", "pop_per_km2"]), on="pincode", how="left")
           .merge(_read("ghsl_builtup_pincode.csv", ["builtup_share"]), on="pincode", how="left")
           .merge(_read("overture_poi_density.csv", ["retail_per_km2"]), on="pincode", how="left"))
    df["has_inputs"] = df[["population", "pop_per_km2", "builtup_share", "retail_per_km2"]].notna().all(axis=1)
    return df


def fit_index(df: pd.DataFrame) -> dict:
    """a, b for s = a·log1p(retail_per_km2) + b·builtup_share, from districts
    within the same state (state means removed). Returns weights + fit stats."""
    d = df[df.has_inputs & (df.population > 0)].copy()
    d["lret"] = np.log1p(d.retail_per_km2)
    feats = ["lret", "builtup_share"]
    agg = d.groupby("dkey").apply(lambda g: pd.Series({
        **{f: np.average(g[f], weights=g.population) for f in feats},
        "y": np.log(g.mpce_combined.iloc[0]), "state": g.hces_state.iloc[0]}))
    agg[feats + ["y"]] = agg[feats + ["y"]].astype(float)
    agg = agg[agg.groupby("state").y.transform("size") >= MIN_STATE_DISTRICTS]
    Z = (agg[feats] - agg.groupby("state")[feats].transform("mean")).values
    y = (agg.y - agg.groupby("state").y.transform("mean")).values
    sd = Z.std(0)
    Zn = Z / sd

    def ridge(A, t):
        return np.linalg.solve(A.T @ A + RIDGE_LAMBDA * np.eye(A.shape[1]), A.T @ t)

    loo = np.array([y[i] - Zn[i] @ ridge(np.delete(Zn, i, 0), np.delete(y, i)) for i in range(len(y))])
    w = ridge(Zn, y) / sd
    return {"weights": dict(zip(["log1p_retail_per_km2", "builtup_share"], map(float, w))),
            "districts": int(len(agg)), "states": int(agg.state.nunique()),
            "loo_r2_within_state": float(1 - (loo ** 2).sum() / (y ** 2).sum()),
            "loo_spearman_within_state": float(spearmanr(y - loo, y).correlation),
            "ridge_lambda": RIDGE_LAMBDA}


def _bench(m, w, target):
    return m * target / np.average(m, weights=w)


def _bench_capped(m, w, target):
    """Ratios clipped to [RATIO_MIN, RATIO_MAX] with the weighted mean still
    exactly `target`: find the one scale c with mean(clip(c·m)) == target.
    That mean only grows with c, so bisection on log c always finds it (a
    solution exists since MIN < 1 < MAX). Clip-then-rescale loops can push a
    pin straight back past its cap, or get stuck with every pin at a cap."""
    lo, hi = RATIO_MIN * target, RATIO_MAX * target

    def mean_at(logc):
        return np.average(np.clip(m * np.exp(logc), lo, hi), weights=w)

    a, b = -50.0, 50.0
    for _ in range(200):
        mid = (a + b) / 2
        if mean_at(mid) < target:
            a = mid
        else:
            b = mid
    out = np.clip(m * np.exp((a + b) / 2), lo, hi)
    return out * target / np.average(out, weights=w)    # remove the last ~1e-15 of bisection error


def downscale(df: pd.DataFrame, weights: dict) -> pd.DataFrame:
    out = df.copy()
    out["s"] = (weights["log1p_retail_per_km2"] * np.log1p(out.retail_per_km2)
                + weights["builtup_share"] * out.builtup_share)
    out["urban"] = out.pop_per_km2 >= URBAN_DENSITY
    out["mpce_est"] = np.nan
    out["method"] = "no_inputs"
    for _, g in out[out.has_inputs].groupby("dkey"):
        idx = g.index
        w = g.population.clip(lower=1.0).values          # an empty polygon still gets a value
        e = np.exp(g.s.values)
        comb, rur, urb = g.mpce_combined.iloc[0], g.mpce_rural.iloc[0], g.mpce_urban.iloc[0]
        u = g.urban.values
        if rur > 0 and urb > 0 and u.any() and (~u).any():
            m = np.empty(len(g))
            m[u] = _bench(e[u], w[u], urb)
            m[~u] = _bench(e[~u], w[~u], rur)
            method = "urban_rural_anchor"
        else:
            m = e
            method = "combined_anchor"
        out.loc[idx, "mpce_est"] = _bench_capped(_bench(m, w, comb), w, comb)
        out.loc[idx, "method"] = method
    out["ratio_to_district"] = out.mpce_est / out.mpce_combined
    return out


def report(out: pd.DataFrame, fit: dict) -> dict:
    ok = out[out.mpce_est.notna()]
    est = ok.set_index("pincode")
    gates = []
    for hi, lo, label in GATES:
        if hi in est.index and lo in est.index:
            a, b = est.loc[hi, "mpce_est"], est.loc[lo, "mpce_est"]
            gates.append({"gate": label, "pass": bool(a > b), "hi": round(float(a)), "lo": round(float(b)),
                          "same_district": bool(est.loc[hi, "dkey"] == est.loc[lo, "dkey"])})
        else:
            gates.append({"gate": label, "pass": None, "missing": [p for p in (hi, lo) if p not in est.index]})
    # Benchmark holds: pop-weighted district mean == HCES combined.
    bench = ok.groupby("dkey").apply(lambda g: abs(np.average(g.mpce_est, weights=g.population.clip(lower=1))
                                                   / g.mpce_combined.iloc[0] - 1)).max()
    within = ok.groupby("dkey").mpce_est.transform(lambda v: v.nunique() > 1)
    q = ok.ratio_to_district.quantile([0.05, 0.25, 0.5, 0.75, 0.95]).round(3)
    return {
        "pincodes_in": int(len(out)), "pincodes_estimated": int(len(ok)),
        "pincodes_no_inputs": int((out.method == "no_inputs").sum()),
        "methods": ok.method.value_counts().to_dict(),
        "districts": int(ok.dkey.nunique()),
        "index_fit": fit,
        "max_benchmark_error": float(bench),
        "share_of_pincodes_varying_within_district": float(within.mean()),
        "ratio_to_district_quantiles": {str(k): float(v) for k, v in q.items()},
        "capped_share": float(((ok.ratio_to_district <= RATIO_MIN + 1e-9) | (ok.ratio_to_district >= RATIO_MAX - 1e-9)).mean()),
        "gates": gates,
        "gates_passed": sum(1 for g in gates if g["pass"]),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    df = load_inputs()
    fit = fit_index(df)
    log.info("index weights %s; within-state leave-one-district-out R2=%.3f rho=%.3f (%d districts)",
             fit["weights"], fit["loo_r2_within_state"], fit["loo_spearman_within_state"], fit["districts"])
    out = downscale(df, fit["weights"])
    rep = report(out, fit)
    log.info("estimated %d pincodes (%s), %d without inputs; gates %d/10",
             rep["pincodes_estimated"], rep["methods"], rep["pincodes_no_inputs"], rep["gates_passed"])
    # Slim on purpose (like gdp_pincode.csv): source/licence live in the report,
    # not repeated on 15k rows.
    cols = ["pincode", "mpce_est", "ratio_to_district", "urban", "method"]
    res = out[out.mpce_est.notna()][cols].copy()
    res["mpce_est"] = res.mpce_est.round(0).astype(int)
    res["ratio_to_district"] = res.ratio_to_district.round(4)
    rep["source"], rep["licence"] = SOURCE, LICENCE
    if args.dry_run:
        print(json.dumps(rep, indent=1))
        return
    res.sort_values("pincode").to_csv(OUT_CSV, index=False)
    REPORT.write_text(json.dumps(rep, indent=1) + "\n")
    log.info("Written: %s (%d rows), %s", OUT_CSV.name, len(res), REPORT.name)


if __name__ == "__main__":
    main()

"""
test_downscale_mpce.py — the per-pincode spending estimate never changes the
survey: each district's population-weighted mean is exactly its HCES figure,
urban/rural anchors are honoured, ratios stay inside the caps, and pincodes
without inputs get no number (not a made-up one).

    python3 tests/test_downscale_mpce.py
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "paisamap-etl", "etl"))
import downscale_mpce as M  # noqa: E402

passed = 0


def check(cond, msg):
    global passed
    assert cond, f"FAIL: {msg}"
    passed += 1


def frame(rows):
    df = pd.DataFrame(rows, columns=["pincode", "dkey", "hces_state", "mpce_combined", "mpce_rural", "mpce_urban",
                                     "population", "pop_per_km2", "builtup_share", "retail_per_km2"])
    df["has_inputs"] = df[["population", "pop_per_km2", "builtup_share", "retail_per_km2"]].notna().all(axis=1)
    return df


W = {"log1p_retail_per_km2": 0.2, "builtup_share": 1.0}
# District A: urban + rural survey values, both kinds of pincode.
# District B: urban only (like Mumbai). District C: one pincode missing its inputs.
df = frame([
    ("100001", "S|A", "S", 10000, 7000, 14000, 50000, 9000, 0.40, 120.0),
    ("100002", "S|A", "S", 10000, 7000, 14000, 30000, 5000, 0.25, 30.0),
    ("100003", "S|A", "S", 10000, 7000, 14000, 20000, 150, 0.03, 0.5),
    ("100004", "S|A", "S", 10000, 7000, 14000, 10000, 80, 0.01, 0.0),
    ("200001", "S|B", "S", 13000, 0, 13000, 40000, 20000, 0.30, 80.0),
    ("200002", "S|B", "S", 13000, 0, 13000, 60000, 25000, 0.20, 10.0),
    ("300001", "S|C", "S", 8000, 6000, 11000, 1000, 500, 0.10, 5.0),
    ("300002", "S|C", "S", 8000, 6000, 11000, None, None, None, None),
])
out = M.downscale(df, W)
for d, g in out[out.mpce_est.notna()].groupby("dkey"):
    mean = np.average(g.mpce_est, weights=g.population.clip(lower=1))
    check(abs(mean / g.mpce_combined.iloc[0] - 1) < 1e-9, f"district {d}: pop-weighted mean == survey figure")
a = out[out.dkey == "S|A"]
check(set(a.method) == {"urban_rural_anchor"}, "urban+rural survey values and both kinds of pins -> urban/rural anchor")
check(a[a.urban].mpce_est.min() > a[~a.urban].mpce_est.max(), "urban pins above rural pins when urban survey value is higher")
check(a.set_index("pincode").mpce_est["100001"] > a.set_index("pincode").mpce_est["100002"],
      "within urban: more shops + more built-up -> higher")
check(set(out[out.dkey == "S|B"].method) == {"combined_anchor"}, "no rural survey value -> combined anchor")
check(out.set_index("pincode").method["300002"] == "no_inputs" and np.isnan(out.set_index("pincode").mpce_est["300002"]),
      "pincode without inputs gets no number, not a made-up one")
check(out.set_index("pincode").method["300001"] == "combined_anchor", "a single pin with only one class -> combined anchor")
check(abs(out.set_index("pincode").mpce_est["300001"] - 8000) < 1e-6, "a lone pin gets exactly its district figure")

# Caps: an extreme index can't push a pincode past RATIO_MAX / below RATIO_MIN.
ext = frame([("400001", "S|D", "S", 10000, 0, 10000, 1000, 30000, 0.99, 1e6),
             ("400002", "S|D", "S", 10000, 0, 10000, 100000, 30000, 0.0, 0.0)])
o2 = M.downscale(ext, {"log1p_retail_per_km2": 3.0, "builtup_share": 5.0})
check(o2.ratio_to_district.max() <= M.RATIO_MAX + 1e-9 and o2.ratio_to_district.min() >= M.RATIO_MIN - 1e-9, "ratios capped")
check(abs(np.average(o2.mpce_est, weights=o2.population) / 10000 - 1) < 1e-9, "…and still benchmarked after capping")

# Real data (skipped if the raw files aren't present).
if (M.RAW / "mpce_district.csv").exists() and (M.RAW / "worldpop_pincode_population.csv").exists():
    real = M.load_inputs()
    fit = M.fit_index(real)
    check(fit["weights"]["log1p_retail_per_km2"] > 0 and fit["weights"]["builtup_share"] > 0,
          "both learned weights are positive (more shops / built-up -> more spending)")
    check(fit["loo_spearman_within_state"] > 0.3, "out-of-sample within-state rank correlation is meaningful (> 0.3)")
    ro = M.downscale(real, fit["weights"])
    rep = M.report(ro, fit)
    check(rep["max_benchmark_error"] < 1e-9, "real data: every district mean equals its survey figure")
    check(rep["capped_share"] < 0.01, "real data: under 1% of pincodes hit a cap")
    check(ro[ro.method == "no_inputs"].mpce_est.isna().all(), "real data: no inputs -> no number")
print(f"OK — {passed} checks passed")

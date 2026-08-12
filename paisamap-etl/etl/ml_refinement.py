"""
ml_refinement.py — Multi-model ML refinement of PaisaMap PPI

Three models are trained and ensembled:

  Model A — Ridge PCA regression
    • PCA compresses all proxy signals into orthogonal components (no more
      correlated-signal double-counting, e.g. property_rate + bank_deposits
      both reflecting wealth).
    • Ridge regression with L2 regularisation learns optimal component weights
      calibrated against city-level HCES 2023-24 MPCE anchors.
    • Also produces anomaly scores via PCA reconstruction error.

  Model B — Gradient Boosting (HistGradientBoosting)
    • Captures non-linear interactions (e.g. high property_rate + low nightlights
      = residential premium area, not commercial hub → different income profile).
    • Trained with leave-one-out CV (n=38, so proper train/test split is too small).
    • Feature importances reveal which proxies drive PPI most.

  Model C — Spatial KNN smoother
    • Income is spatially autocorrelated (Tobler's First Law).
    • KNN with haversine distances averages the PPI of geographic neighbours.
    • Acts as a geographic consistency prior — smooths isolated outliers.

Ensemble: 0.45 × Model_A + 0.35 × Model_B + 0.20 × Model_C

Validation:
  • PPI gates: Golf Links > Saket > Narela
  • Spatial Moran's I: should be > 0 (positive autocorrelation)
  • Proxy anomaly flags: pincodes where reconstruction error > 2σ
  • LOO-CV RMSE for Models A and B

Outputs:
  data/output/ppi_ml_refined.csv   — ML-refined PPI (can replace ppi_pincode.csv)
  data/output/ml_diagnostics.json  — feature importances, anomaly flags, CV scores
"""

from __future__ import annotations
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.ensemble import HistGradientBoostingRegressor, IsolationForest
from sklearn.linear_model import Ridge
from sklearn.model_selection import LeaveOneOut
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_squared_error

import _db
from _filelock import write_lock

ROOT = Path(__file__).resolve().parents[1]
RAW  = ROOT / "data" / "raw"
OUT  = ROOT / "data" / "output"

# ── City-level HCES 2023-24 anchors (urban MPCE ₹/person/month) ─────────────
# Source: HCES 2023-24 Urban Fact Sheet (NSO, 2024)
HCES_MPCE_CITY = {
    "DL": 8_420,   # Delhi NCT urban
    "MH": 9_180,   # Mumbai/Pune metro composite
    "KA": 7_640,   # Bengaluru urban
    "UP": 6_950,   # Noida (UP urban is lower but Noida is significantly above state avg)
}
AVG_HH = 4.1   # urban HH size

# RTO-derived features require within-city normalization because:
#   car_2w_ratio  — an RTO covers a whole district; Narela (DL-1) gets North Delhi's ratio
#   luxury_share  — district-level denominator effect skews large districts
#   ev_share      — Karnataka has state EV incentives + Ola Electric HQ; city effect >≈ income effect
# We normalize within city so these features capture relative within-city variation.
RTO_FEATURES_WITHIN_CITY = {"car_2w_ratio", "luxury_share", "ev_share"}

PINCODE_STATE = {
    # ── Delhi NCT (original 13 + 9 new) ──────────────────────────────────────
    "110003":"DL","110021":"DL","110057":"DL","110024":"DL","110048":"DL",
    "110016":"DL","110017":"DL","110070":"DL","110034":"DL","110092":"DL",
    "110059":"DL","110093":"DL","110040":"DL",
    "110001":"DL","110006":"DL","110009":"DL","110026":"DL","110058":"DL",
    "110075":"DL","110085":"DL","110091":"DL","110032":"DL",
    # Gurgaon mapped to DL — same NCR metro, similar income distribution
    "122002":"DL","122022":"DL",
    # ── Uttar Pradesh ─────────────────────────────────────────────────────────
    "201301":"UP",
    # ── Maharashtra (original 12 + 12 new) ───────────────────────────────────
    "400021":"MH","400005":"MH","400049":"MH","400051":"MH","400053":"MH",
    "400059":"MH","400068":"MH","400050":"MH","400071":"MH","400070":"MH",
    "400063":"MH","400086":"MH",
    "400006":"MH","400013":"MH","400018":"MH","400028":"MH","400054":"MH",
    "400060":"MH","400062":"MH","400097":"MH","400074":"MH","400080":"MH",
    "400614":"MH","400708":"MH",
    # ── Karnataka (original 12 + 11 new) ─────────────────────────────────────
    "560025":"KA","560027":"KA","560001":"KA","560099":"KA","560034":"KA",
    "560017":"KA","560076":"KA","560037":"KA","560011":"KA","560068":"KA",
    "560085":"KA","560035":"KA",
    "560002":"KA","560003":"KA","560004":"KA","560008":"KA","560029":"KA",
    "560032":"KA","560047":"KA","560064":"KA","560066":"KA","560078":"KA",
    "560103":"KA",
}


# ── Helper: haversine distance matrix (km) ───────────────────────────────────
# Only used by anything that genuinely needs the full dense n×n matrix. At
# 15,545 pincodes that's ~242M entries / ~2GB — measured directly 2026-08-08
# at ~19s and ~2GB RAM per call, a real problem on a memory-constrained
# server. model_c_spatial()/morans_i() below were the two hot callers (both
# only ever need NEARBY pairs, never the full matrix) — refactored to
# _spatial_kdtree()'s radius queries instead. Kept for any small-n use.
def haversine_matrix(lats, lngs):
    """Return (n×n) distance matrix in km."""
    R = 6371.0
    lat_r = np.radians(np.array(lats))
    lng_r = np.radians(np.array(lngs))
    # broadcasting
    dlat = lat_r[:, None] - lat_r[None, :]
    dlng = lng_r[:, None] - lng_r[None, :]
    a = np.sin(dlat/2)**2 + np.cos(lat_r[:,None]) * np.cos(lat_r[None,:]) * np.sin(dlng/2)**2
    return 2 * R * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


_EARTH_R_KM = 6371.0


def _to_cartesian(lats, lngs):
    """lat/lng (degrees) -> 3D Cartesian km on Earth's surface. Euclidean chord
    distance between two such points is a distortion-free proxy for great-circle
    distance at the scales this module cares about (tens of km) — same technique
    used in fetch_phonepe_grid.py/build_broad_coverage.py this session, avoids
    the latitude-dependent distortion a raw lat/lng KDTree would have."""
    lat_r = np.radians(np.asarray(lats, dtype=float))
    lng_r = np.radians(np.asarray(lngs, dtype=float))
    x = _EARTH_R_KM * np.cos(lat_r) * np.cos(lng_r)
    y = _EARTH_R_KM * np.cos(lat_r) * np.sin(lng_r)
    z = _EARTH_R_KM * np.sin(lat_r)
    return np.stack([x, y, z], axis=-1)


def _km_to_chord_radius(km):
    """Great-circle radius (km) -> equivalent Cartesian chord radius, for
    scipy.spatial.cKDTree radius queries on _to_cartesian() points."""
    return 2 * _EARTH_R_KM * np.sin(min(km, np.pi * _EARTH_R_KM) / (2 * _EARTH_R_KM))


# ── Load all proxies ──────────────────────────────────────────────────────────
def load_features() -> pd.DataFrame:
    frames = {}

    # Standard single-column proxies
    for fname, col in [
        ("property_rates.csv",   "rate_per_sqft"),
        ("bank_deposits.csv",    "deposits_per_capita"),
        ("bank_deposits.csv",    "bank_branches_per_lakh"),
        ("bank_deposits.csv",    "credit_deposit_ratio"),
        # upi_txn_value_per_capita (upi_activity.csv) deliberately excluded from the shared
        # feature matrix: its source (PhonePe Pulse district-level data) is a confirmed dead
        # end frozen at 2024 Q4, real for only 195/15,551 pincodes (1.3%) — the pincode
        # universe grew ~79x around it since it shipped, while a district-level source can't
        # grow past ~195-219 pincodes no matter what. X_df.fillna(X_df.median()) below would
        # median-impute it for the other 98.7%, and because the whole ensemble refits from
        # scratch every run, that imputed value leaks into everyone's PPI — measured directly
        # (A/B, same snapshot, with vs without this column): 672 pincodes (4.3%) moved >10pt,
        # some flipping the full 40-200 floor-to-ceiling range. Same leakage class already
        # fixed once for Karnataka income (see load_karnataka_income() below) — that one was
        # worth a scoped post-ensemble blend since it had real regional coverage; this one
        # doesn't (it's superseded pan-India by fetch_phonepe_grid.py's upi_txn_count_nearby,
        # a different but better-covered metric), so it's simplest to just keep it out of the
        # model. Still served as-is via server.py's EXPORT_SIGNAL_FILES for the 195 pincodes
        # it's real for — this only removes it from PPI scoring, not from the map.
        ("vehicle_density.csv",  "cars_per_1000"),
        ("nightlights.csv",      "radiance_mean"),
        ("itr_filers.csv",       "filers_per_capita"),
        ("poi_density.csv",      "premium_poi_per_km2"),
        ("financial_inclusion.csv", "fin_density_per_km2"),
    ]:
        p = RAW / fname
        if not p.exists():
            print(f"  · {fname:<35} (not present — skipped)")
            continue
        full = pd.read_csv(p, dtype={"pincode": str}).set_index("pincode")
        if col not in full.columns:
            # A file can exist but not (yet, or no longer) carry every column that's
            # ever been derived from it — e.g. credit_deposit_ratio is only added by
            # `fetch_rbi_bsr.py --cdr-xlsx <manually-exported Handbook Table 153>`, an
            # optional one-off flag, not part of the recurring cron. Without this
            # check, a run that regenerates bank_deposits.csv without that flag
            # crashes the *entire* pipeline on a KeyError instead of just running
            # without this one optional feature (confirmed happened 2026-07-16 —
            # the column vanished on the next post-add write and every full
            # pipeline rerun since would have crashed here, undetected because nothing
            # exercises this path outside a manual full rerun).
            print(f"  · {fname:<35}::{col} (column not present — skipped)")
            continue
        df = full[col]
        frames[col] = df
        print(f"  ✓ {fname:<35} {len(df)} rows")

    # HCES 2023-24 district MPCE (direct government spend signal)
    mpce_path = RAW / "mpce_district.csv"
    if mpce_path.exists():
        mpce = pd.read_csv(mpce_path, dtype={"pincode": str}).set_index("pincode")
        if "mpce_combined" in mpce.columns:
            frames["mpce_combined"] = mpce["mpce_combined"]
            print(f"  ✓ {'mpce_district.csv::mpce_combined':<35} {mpce['mpce_combined'].notna().sum()} rows")
    else:
        print(f"  · {'mpce_district.csv':<35} (run build_mpce_pincode.py first)")

    # Vehicle state trend (RS Session 248 — 4yr growth 2014-2019 as economic dynamism signal)
    vst_path = RAW / "vehicle_state_trend.csv"
    if vst_path.exists():
        vst = pd.read_csv(vst_path)
        _STATE_NAME_TO_CODE = {
            "Delhi": "DL", "Haryana": "HR", "Karnataka": "KA",
            "Maharashtra": "MH", "Punjab": "PB", "Uttar Pradesh": "UP",
            "Rajasthan": "RJ", "Gujarat": "GJ", "Tamil Nadu": "TN",
            "West Bengal": "WB", "Telangana": "TS", "Andhra Pradesh": "AP",
            "Kerala": "KL", "Odisha": "OD", "Assam": "AS", "Bihar": "BR",
            "Jharkhand": "JH", "Madhya Pradesh": "MP", "Chhattisgarh": "CG",
            "Uttarakhand": "UK", "Himachal Pradesh": "HP", "Jammu and Kashmir": "JK",
        }
        vst["state_code"] = vst["state_name"].map(_STATE_NAME_TO_CODE)
        vst_map = vst.dropna(subset=["state_code"]).set_index("state_code")["growth_4yr_pct"]
        # Expand to pincode-level using existing pincodes from other frames
        all_pincodes = set()
        for s in frames.values():
            all_pincodes.update(s.index)
        growth_series = pd.Series(
            {pc: vst_map.get(PINCODE_STATE.get(pc) or _PREFIX_STATE.get(str(pc)[:2]))
             for pc in all_pincodes},
            name="vehicle_growth_4yr"
        )
        frames["vehicle_growth_4yr"] = growth_series
        n_matched = growth_series.notna().sum()
        print(f"  ✓ {'vehicle_state_trend.csv::growth_4yr':<35} {n_matched}/{len(all_pincodes)} pincodes matched")
    else:
        print(f"  · {'vehicle_state_trend.csv':<35} (run fetch_vehicle_state_trend.py first)")

    # Multi-column RTO enhanced
    rto_path = RAW / "rto_enhanced.csv"
    if rto_path.exists():
        rto = pd.read_csv(rto_path, dtype={"pincode": str}).set_index("pincode")
        for col in ["car_2w_ratio", "luxury_share", "ev_share"]:
            if col in rto.columns:
                frames[col] = rto[col]
                print(f"  ✓ rto_enhanced.csv::{col:<27} {len(rto)} rows")
    else:
        print("  · rto_enhanced.csv (not present — run fetch_rto_enhanced.py first)")

    return pd.DataFrame(frames)


def winsorize(s: pd.Series, lo=0.02, hi=0.98) -> pd.Series:
    return s.clip(s.quantile(lo), s.quantile(hi))


# Pincode-prefix → state for pincodes not in PINCODE_STATE (dynamically added)
_PREFIX_STATE: dict = {
    "11": "DL", "12": "DL", "13": "HP", "14": "PB", "15": "PB",
    "16": "CH", "17": "HP", "18": "JK", "19": "JK",
    "20": "UP", "21": "UP", "22": "UP", "24": "UP",
    "25": "UP", "26": "UP", "27": "UP", "28": "UP",
    "30": "RJ", "31": "RJ", "32": "RJ", "33": "RJ", "34": "RJ",
    "36": "GJ", "37": "GJ", "38": "GJ", "39": "GJ",
    "40": "MH", "41": "MH", "42": "MH", "43": "MH", "44": "MH",
    "45": "MP", "46": "MP", "47": "MP", "48": "MP", "49": "CG",
    "50": "TS", "51": "AP", "52": "AP", "53": "AP",
    "56": "KA", "57": "KA", "58": "KA", "59": "KA",
    "60": "TN", "61": "TN", "62": "TN", "63": "TN", "64": "TN",
    "67": "KL", "68": "KL", "69": "KL",
    "70": "WB", "71": "WB", "72": "WB", "73": "WB", "74": "WB",
    "75": "OD", "76": "OD", "77": "OD",
    "78": "AS",
    "80": "BR", "81": "BR", "82": "JH", "83": "JH",
    "84": "BR", "85": "BR",
}


def _state(pc: str) -> str:
    """Return the city/state group for a pincode (explicit table → prefix fallback)."""
    return PINCODE_STATE.get(pc, _PREFIX_STATE.get(str(pc)[:2], "XX"))


def load_district_groups() -> dict[str, str]:
    """
    pincode -> 'STATE|DISTRICT' from the real HCES join in mpce_district.csv
    (built by build_mpce_pincode.py, pan-India since 2026-07-15). This is a
    much finer grouping than _state()'s 2-digit-prefix fallback, which lumps
    e.g. all of Maharashtra into one "city" group for pincodes outside the
    ~72-pincode PINCODE_STATE table. Falls back to _state() per-pincode
    wherever no real district match exists (see _group()).
    """
    p = RAW / "mpce_district.csv"
    if not p.exists():
        return {}
    df = pd.read_csv(p, dtype={"pincode": str})
    if "hces_district" not in df.columns or "hces_state" not in df.columns:
        return {}
    df = df.dropna(subset=["hces_district", "hces_state"])
    return {row.pincode: f"{row.hces_state}|{row.hces_district}" for row in df.itertuples()}


def load_district_mpce() -> dict[str, float]:
    """pincode -> real HCES mpce_combined, for direct per-pincode income anchoring
    (replaces the 4-state HCES_MPCE_CITY hardcode wherever a real match exists)."""
    p = RAW / "mpce_district.csv"
    if not p.exists():
        return {}
    df = pd.read_csv(p, dtype={"pincode": str}).dropna(subset=["mpce_combined"])
    return dict(zip(df["pincode"], df["mpce_combined"]))


def load_karnataka_income() -> pd.Series:
    """Real district-level income (Karnataka DES 2019-20, taluk-averaged) —
    see fetch_karnataka_income.py. Only ever populated for Karnataka
    pincodes (~58/585), so it's kept out of the shared PCA/HGB feature
    matrix — median-imputing it for the other 90%+ of pincodes would make
    a single-state signal perturb the whole-country model refit. Applied
    instead as a targeted post-ensemble blend in main(), scoped to the
    pincodes it actually describes."""
    p = RAW / "karnataka_income.csv"
    if not p.exists():
        return pd.Series(dtype=float)
    df = pd.read_csv(p, dtype={"pincode": str}).set_index("pincode")
    if "per_capita_income_karnataka" not in df.columns:
        return pd.Series(dtype=float)
    return df["per_capita_income_karnataka"].dropna()


def _group(pc: str, group_key: dict) -> str:
    """Real district-level group if we have one, else the coarser state fallback."""
    return group_key.get(pc) or _state(pc)


def within_city_normalize(df: pd.DataFrame, cols: set, group_key: dict | None = None,
                           min_group_size: int = 5) -> pd.DataFrame:
    """
    For each column in `cols`, subtract the city-group mean and divide by std.
    This removes inter-city policy / denominator effects (e.g. Karnataka EV incentives,
    large-district car/2W denominator) so the feature captures only within-city variation.
    Groups come from `group_key` (real HCES district, see load_district_groups())
    where available, falling back to _state() (explicit table → prefix) otherwise.
    Columns not in `cols` are returned unchanged.

    Groups smaller than `min_group_size` are too thin for a stable mean/std —
    found 2026-08-08: Narela's 3-pincode HCES district group ('DELHI|NORTH')
    let one real but outlying VAHAN car_2w_ratio value swing its normalized
    z-score to +1.15, nearly outranking Saket (PPI validation gate went from
    PASS to FAIL) despite Saket having 2-3x stronger property-rate/nightlight/
    POI/ITR fundamentals. 86% of district groups in the current dataset have
    <5 members, so this isn't a one-off — pincodes in a too-thin district
    group fall back to their coarser but more stable state-level group
    ("STATE|DISTRICT" -> "STATE") instead of a noisy district-only estimate.
    """
    result = df.copy()
    group_key = group_key or {}
    pc_group = {pc: _group(pc, group_key) for pc in df.index}

    group_sizes: dict[str, int] = {}
    for grp in pc_group.values():
        group_sizes[grp] = group_sizes.get(grp, 0) + 1

    def _effective_group(pc: str) -> str:
        grp = pc_group[pc]
        if group_sizes[grp] >= min_group_size:
            return grp
        return grp.split("|")[0] if "|" in grp else _state(pc)

    pc_group_eff = {pc: _effective_group(pc) for pc in df.index}

    for col in cols:
        if col not in df.columns:
            continue
        for grp in set(pc_group_eff.values()):
            mask = [pc_group_eff[pc] == grp for pc in df.index]
            vals = df.loc[mask, col]
            if len(vals) < 2:
                # Single-pincode group: set to 0 (no within-group variation to learn)
                result.loc[mask, col] = 0.0
                continue
            std = vals.std()
            result.loc[mask, col] = (vals - vals.mean()) / (std if std > 1e-9 else 1.0)
    return result


# ── Model A: PCA + Ridge ──────────────────────────────────────────────────────
def model_a_pca_ridge(X_scaled: np.ndarray, y_anchor: np.ndarray, n_components: int = 5):
    """
    PCA decomposition → Ridge regression.
    Target: y_anchor (within-city property-rate z-score, passed in from main).
    Returns: (z_scores, feature_importances, pca_obj)
    """
    pca = PCA(n_components=min(n_components, X_scaled.shape[1]))
    X_pca = pca.fit_transform(X_scaled)

    ridge = Ridge(alpha=1.0)
    ridge.fit(X_pca, y_anchor)
    z = ridge.predict(X_pca)

    # Feature importances: |PCA loading × Ridge coefficient| summed across components
    loadings = pca.components_   # (n_components × n_features)
    importance = np.abs(loadings * ridge.coef_[:, None]).sum(axis=0)
    importance /= importance.sum()

    return z, importance, pca


# ── Model B: HistGradientBoosting with LOO-CV ────────────────────────────────
# LOO_CV_MAX_SAMPLES: LOO-CV refits one full model per held-out row, purely to
# report an RMSE diagnostic (printed + ml_diagnostics.json — never affects the
# actual z_scores/predictions below, those come from a single separate
# full-dataset fit). Measured directly 2026-08-08 at the current dataset size
# (15,545 pincodes): ~253ms/fit -> ~66 minutes for the full LOO-CV loop alone,
# on a server with limited RAM already running the weekly cron_enrich.sh job
# alongside the live Flask app. Capping the loop to a random subsample keeps
# the RMSE a valid statistical estimate (same idea as k-fold CV) without
# paying O(n) refits — this was fine when the dataset was ~600-900 rows,
# not at 15k+.
LOO_CV_MAX_SAMPLES = 500


def model_b_hgb_loo(X_scaled: np.ndarray, y_anchor: np.ndarray, feature_names: list):
    """
    Gradient Boosting with Leave-One-Out cross-validation (subsampled above
    LOO_CV_MAX_SAMPLES rows — see module-level comment).
    Returns: (z_scores, feature_importances, loo_rmse)
    """
    model = HistGradientBoostingRegressor(
        max_iter=200, max_depth=3, learning_rate=0.05,
        min_samples_leaf=2, l2_regularization=1.0,
        random_state=42
    )

    # LOO-CV to estimate generalisation error — subsampled for large n (see
    # LOO_CV_MAX_SAMPLES above); the subsample is only used for this RMSE
    # estimate, not for the real z_scores computed below.
    n = len(y_anchor)
    if n > LOO_CV_MAX_SAMPLES:
        rng = np.random.RandomState(42)
        loo_idx = rng.choice(n, size=LOO_CV_MAX_SAMPLES, replace=False)
    else:
        loo_idx = np.arange(n)
    X_loo, y_loo = X_scaled[loo_idx], y_anchor[loo_idx]

    loo = LeaveOneOut()
    preds_loo = np.zeros(len(y_loo))
    for train_idx, test_idx in loo.split(X_loo):
        m = HistGradientBoostingRegressor(
            max_iter=200, max_depth=3, learning_rate=0.05,
            min_samples_leaf=2, l2_regularization=1.0, random_state=42
        )
        m.fit(X_loo[train_idx], y_loo[train_idx])
        preds_loo[test_idx] = m.predict(X_loo[test_idx])

    loo_rmse = math.sqrt(mean_squared_error(y_loo, preds_loo))

    # Fit on all data for final predictions
    model.fit(X_scaled, y_anchor)
    z = model.predict(X_scaled)

    # Permutation-based feature importance (swap each feature, measure score drop)
    importances = {}
    base_score = mean_squared_error(y_anchor, z)
    for i, fname in enumerate(feature_names):
        X_perm = X_scaled.copy()
        X_perm[:, i] = np.random.permutation(X_perm[:, i])
        perm_score = mean_squared_error(y_anchor, model.predict(X_perm))
        importances[fname] = max(0.0, perm_score - base_score)

    total = sum(importances.values()) or 1.0
    importances = {k: round(v / total, 4) for k, v in importances.items()}

    return z, importances, loo_rmse


# ── Model C: Spatial KNN smoother ────────────────────────────────────────────
def model_c_spatial(z_base: np.ndarray, lats: list, lngs: list,
                     max_dist_km: float = 20.0):
    """
    Smooth z_base by averaging each pincode's KNN within max_dist_km.
    Uses haversine distances; a pincode is always included in its own average.

    KD-tree radius query instead of the old dense n×n haversine_matrix — this
    only ever needs points within max_dist_km (typically a small, near-fixed-
    size neighbourhood regardless of n), so it's O(n log n) instead of O(n²)
    and doesn't need a ~2GB matrix in memory. See haversine_matrix()'s comment.
    """
    from scipy.spatial import cKDTree
    n = len(z_base)
    pts = _to_cartesian(lats, lngs)
    tree = cKDTree(pts)
    r = _km_to_chord_radius(max_dist_km)
    neighbor_lists = tree.query_ball_point(pts, r=r)

    z_smooth = np.zeros(n)
    for i, neighbors in enumerate(neighbor_lists):
        if len(neighbors) < 2:
            z_smooth[i] = z_base[i]
            continue
        idx = np.asarray(neighbors)
        dists = np.linalg.norm(pts[idx] - pts[i], axis=1)   # chord km ≈ great-circle km at this scale
        inv_d = 1.0 / np.maximum(dists, 0.1)
        z_smooth[i] = np.average(z_base[idx], weights=inv_d)

    return z_smooth


# ── Isolation Forest anomaly detection ───────────────────────────────────────
def detect_anomalies(X_scaled: np.ndarray, pincodes: list, feature_names: list,
                     contamination: float = 0.1, group_key: dict | None = None):
    """
    Flag pincodes where proxies conflict significantly.
    Returns dict: pincode → {score, is_anomaly, top_deviant_proxy}
    """
    group_key = group_key or {}
    iso = IsolationForest(contamination=contamination, random_state=42)
    iso.fit(X_scaled)
    scores = iso.score_samples(X_scaled)   # more negative = more anomalous
    is_anomaly = iso.predict(X_scaled) == -1

    # Precompute each pincode's group + each group's mean vector ONCE — the old
    # version rebuilt a full-n boolean mask (and recomputed the same group's
    # mean) inside the per-pincode loop, O(n²) overall. Measured 2026-08-08 at
    # n=15,545: ~41s. Same fix pattern as model_c_spatial/morans_i above.
    pc_groups = [_group(pc, group_key) for pc in pincodes]
    group_to_idx: dict[str, list[int]] = {}
    for i, g in enumerate(pc_groups):
        group_to_idx.setdefault(g, []).append(i)
    group_means = {g: X_scaled[idx].mean(axis=0) for g, idx in group_to_idx.items()}

    flags = {}
    for i, pc in enumerate(pincodes):
        city_mean = group_means[pc_groups[i]]
        deviations = np.abs(X_scaled[i] - city_mean)
        top_feat = feature_names[int(np.argmax(deviations))]

        flags[pc] = {
            "anomaly_score": round(float(-scores[i]), 4),
            "is_anomaly": bool(is_anomaly[i]),
            "top_deviant_proxy": top_feat,
            "deviation_magnitude": round(float(deviations.max()), 3),
        }
    return flags


# ── Moran's I spatial autocorrelation ────────────────────────────────────────
def morans_i(values: np.ndarray, lats: list, lngs: list,
             bandwidth_km: float = 25.0) -> float:
    """
    Compute Moran's I statistic for spatial autocorrelation.
    W_ij = 1/(dist_km+1)^2 if dist <= bandwidth_km else 0

    KD-tree pair query instead of the old dense n×n haversine_matrix — W is
    naturally sparse (only pairs within bandwidth_km are ever nonzero), so
    building it via query_pairs() is O(n log n + #pairs) instead of O(n²).
    See haversine_matrix()'s comment / model_c_spatial() for the same fix.
    Diagnostic-only metric (printed + ml_diagnostics.json), never feeds back
    into PPI/income/spend, so exact floating-point parity with the old dense
    version isn't required — this is mathematically the same formula.
    """
    from scipy.spatial import cKDTree
    n = len(values)
    values = np.asarray(values, dtype=float)
    pts = _to_cartesian(lats, lngs)
    tree = cKDTree(pts)
    r = _km_to_chord_radius(bandwidth_km)
    pairs = tree.query_pairs(r=r, output_type="ndarray")

    z = values - values.mean()
    if len(pairs) == 0:
        return 0.0

    i_idx, j_idx = pairs[:, 0], pairs[:, 1]
    dists = np.linalg.norm(pts[i_idx] - pts[j_idx], axis=1)
    w = 1.0 / (dists + 1.0) ** 2

    row_w_sum = np.zeros(n)
    np.add.at(row_w_sum, i_idx, w)
    np.add.at(row_w_sum, j_idx, w)   # W is symmetric before row-normalization

    contrib = w * z[i_idx] * z[j_idx] / (row_w_sum[i_idx] + 1e-9) \
            + w * z[j_idx] * z[i_idx] / (row_w_sum[j_idx] + 1e-9)
    numerator = n * contrib.sum()
    denominator = n * (z ** 2).sum()
    return float(numerator / (denominator + 1e-9))


# ── Income / spend estimation ─────────────────────────────────────────────────
def estimate_income(z_ensemble: np.ndarray, pincodes: list,
                     group_key: dict | None = None,
                     pincode_mpce: dict | None = None) -> pd.DataFrame:
    """
    Anchor ₹ estimates to HCES MPCE.
    Each group's pincode distribution is re-centred on HCES MPCE × HH size.
    `group_key` gives real HCES-district groups where available (falls back
    to _state()'s coarser state grouping); `pincode_mpce` gives the real
    per-pincode HCES MPCE anchor where available (falls back to the 4-state
    HCES_MPCE_CITY hardcode, then a flat ₹7,000 default).
    """
    group_key = group_key or {}
    pincode_mpce = pincode_mpce or {}
    rows = []
    city_groups = {}
    for i, pc in enumerate(pincodes):
        grp = _group(pc, group_key)
        city_groups.setdefault(grp, []).append(i)

    # Normalise z_ensemble per group so group mean → HCES anchor.
    # A group with a single pincode has no within-group variation to
    # standardise against -- re-centring a lone point on itself would
    # zero it out (PPI forced to exactly 100), discarding its real
    # signal. Leave those at their already-globally-standardised
    # z_ensemble value instead.
    #
    # Pure group-relative standardisation has its own failure mode: HCES
    # district groups are administrative boundaries, not "comparable peer
    # sets" — a district can span a dense premium urban core plus a much
    # larger, far less developed periphery (Bangalore Urban: 102 pincodes
    # in the core set, median PPI 101, but Indiranagar/Whitefield/MG Road
    # tower over it). Re-centring purely against that mostly-low-baseline
    # group inflates the urban standout's z far past what the same
    # locality would score against a genuinely comparable peer set —
    # confirmed live: Indiranagar and Noida Sector 18-27 both saturated
    # the PPI ceiling this way, while Mumbai's premium pincodes didn't,
    # because "Mumbai Suburban" happens to already be a uniformly urban
    # district (median PPI 119 across its 23 members) — no single Mumbai
    # locality stands nearly as far from its own group's average. This
    # isn't purely a group-*size* problem (Bangalore's group of 102 is
    # large by count) — it's group *homogeneity*, which raw n doesn't
    # capture, but shrinking every group toward the global scale by size
    # still directly bounds how far any single group's mean-shift can
    # distort an outlier within it, without requiring new data to detect
    # which specific groups are heterogeneous.
    #
    # Blend the group-relative z toward the already-globally-standardised
    # z_ensemble with credibility weight α = n/(n+K) (standard partial-
    # pooling / insurance-style credibility theory): small groups lean on
    # the global scale, large groups keep more of their own local signal.
    CREDIBILITY_K = 50
    z_adj = z_ensemble.copy()
    for grp, idxs in city_groups.items():
        if len(idxs) < 2:
            continue
        city_z = z_ensemble[idxs]
        # Map group mean z → ln(anchor_spend)
        z_mean = city_z.mean()
        z_std  = city_z.std() or 1.0
        alpha  = len(idxs) / (len(idxs) + CREDIBILITY_K)
        for i in idxs:
            z_group = (z_ensemble[i] - z_mean) / z_std   # re-standardise within group
            z_adj[i] = alpha * z_group + (1 - alpha) * z_ensemble[i]

    ppi_arr = np.clip(100 + 30 * z_adj, 40, 200).round().astype(int)

    # Income/spend must saturate at the same point PPI does. Without this, a within-group
    # outlier (a thin HCES group where one premium locality sits among mostly low-baseline
    # peers) can score a z_adj far past what's needed to hit the PPI display cap of 200 —
    # the exponential lift below keeps compounding on that uncapped value even though the
    # PPI shown for it is already pinned at the ceiling. Confirmed live: Bengaluru's
    # Indiranagar and Noida Sector 18-27 both saturate ppi_ml at 200 but still reported
    # est_monthly_income_hh of ₹9.9L / ₹15.4L — an order of magnitude above Mumbai's Cuffe
    # Parade / Malabar Hill (real premium South Mumbai, ppi_ml 134-136, ~₹1.5L/month),
    # because Mumbai's own group already contains many comparably premium peers, so no
    # single Mumbai locality's z-score runs away the way an isolated standout in a
    # thinner group's does. Deriving the lift from the same clipped range PPI uses keeps
    # the two numbers consistent with each other again.
    z_income = np.clip(z_adj, -2.0, 100 / 30)

    for i, pc in enumerate(pincodes):
        state = _state(pc)
        mpce  = pincode_mpce.get(pc) or HCES_MPCE_CITY.get(state, 7000)
        base_spend = mpce * AVG_HH
        lift  = math.exp(0.55 * float(z_income[i]))
        spend = base_spend * lift
        spend_share = max(0.45, min(0.85, 0.82 - 0.10 * float(z_income[i])))
        income = spend / spend_share
        rows.append({
            "pincode":              pc,
            "ppi_ml":               int(ppi_arr[i]),
            "est_monthly_income_hh": round(income / 100) * 100,
            "est_monthly_spend_hh":  round(spend / 100) * 100,
        })
    return pd.DataFrame(rows).set_index("pincode")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    OUT.mkdir(parents=True, exist_ok=True)

    # Snapshot existing PPI BEFORE this run so we can check for drift
    prev_ppi = None
    prev_ml_path = OUT / "ppi_ml_refined.csv"
    if prev_ml_path.exists():
        _prev = pd.read_csv(prev_ml_path, dtype={"pincode": str}).set_index("pincode")
        if "ppi_ml" in _prev.columns:
            prev_ppi = _prev["ppi_ml"]

    print("Loading proxy features…")
    raw = load_features()
    print(f"  Shape: {raw.shape[0]} pincodes × {raw.shape[1]} features\n")

    if raw.empty or raw.shape[0] < 10:
        raise SystemExit("Not enough data to run ML refinement.")

    # Load coordinates for spatial models
    coords = pd.read_csv(RAW / "pincode_coords.csv", dtype={"pincode": str}).set_index("pincode")
    names_df = pd.read_csv(RAW / "pincode_names.csv",  dtype={"pincode": str}).set_index("pincode") \
               if (RAW / "pincode_names.csv").exists() else pd.DataFrame()

    # Intersect: only pincodes present in both raw and coords
    common = raw.index.intersection(coords.index)
    raw    = raw.loc[common]
    lats   = coords.loc[common, "lat"].tolist()
    lngs   = coords.loc[common, "lng"].tolist()
    pincodes = list(common)

    print(f"Working with {len(pincodes)} pincodes\n")

    # Real HCES-district groups + per-pincode MPCE (pan-India since 2026-07-15,
    # see build_mpce_pincode.py) -- finer than _state()'s prefix fallback and
    # more accurate than the 4-state HCES_MPCE_CITY hardcode.
    group_key = load_district_groups()
    pincode_mpce = load_district_mpce()
    n_grouped = sum(1 for pc in pincodes if pc in group_key)
    n_mpce    = sum(1 for pc in pincodes if pc in pincode_mpce)
    print(f"  Real HCES district group: {n_grouped}/{len(pincodes)} pincodes "
          f"(rest fall back to state-level grouping)")
    print(f"  Real HCES MPCE anchor:    {n_mpce}/{len(pincodes)} pincodes "
          f"(rest fall back to HCES_MPCE_CITY / ₹7,000 default)\n")

    # ── Feature matrix ────────────────────────────────────────────────────────
    # Step 1: winsorize
    X_df = raw.apply(winsorize)
    X_df = X_df.fillna(X_df.median())

    # Step 2: within-city normalize RTO signals so inter-city policy /
    # district-denominator effects don't dominate the ML models.
    X_df = within_city_normalize(X_df, RTO_FEATURES_WITHIN_CITY, group_key)

    feature_names = list(X_df.columns)
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X_df.values)

    # Supervised target: within-city property-rate z-score.
    # Property rates are the most granular and time-current income proxy we have;
    # using them as the target makes Models A and B learn WITHIN-CITY variation.
    # HCES city anchoring is applied later (in estimate_income) as an additive offset.
    prop_col_idx = feature_names.index("rate_per_sqft") if "rate_per_sqft" in feature_names else None
    if prop_col_idx is not None:
        # A handful of live-enriched pincodes are missing a property_rates.csv
        # row entirely (pre-dating the write_lock() fix in enrich_single.py /
        # batch_enrich_hces.py — see _filelock.py). Same median-fill treatment
        # already applied to the feature matrix (X_df.fillna(X_df.median())
        # above) — without it these feed NaN into the Ridge target below and
        # crash the whole run.
        rate_raw = raw["rate_per_sqft"].fillna(raw["rate_per_sqft"].median())
        prop_raw = within_city_normalize(
            pd.DataFrame({"rate_per_sqft": rate_raw}, index=raw.index),
            {"rate_per_sqft"}, group_key
        )["rate_per_sqft"].reindex(pincodes).values
        prop_raw = np.nan_to_num(prop_raw, nan=np.nanmedian(prop_raw))
        y_anchor = (prop_raw - prop_raw.mean()) / (prop_raw.std() or 1.0)
    else:
        # Fallback to HCES city MPCE if property rates unavailable
        city_mpce = np.array([HCES_MPCE_CITY.get(PINCODE_STATE.get(pc, "DL"), 7000)
                              for pc in pincodes], dtype=float)
        y_anchor  = (city_mpce - city_mpce.mean()) / city_mpce.std()

    # ── Model A ─────────────────────────────────────────────────────────────
    print("Model A — PCA + Ridge…")
    np.random.seed(42)
    z_a, imp_a, pca_obj = model_a_pca_ridge(X_scaled, y_anchor)
    var_exp = pca_obj.explained_variance_ratio_
    print(f"  PCA {pca_obj.n_components_} components explain "
          f"{var_exp.sum()*100:.1f}% variance")
    for j, v in enumerate(var_exp):
        print(f"    PC{j+1}: {v*100:.1f}%")

    # ── Model B ─────────────────────────────────────────────────────────────
    print("\nModel B — HistGradientBoosting LOO-CV…")
    np.random.seed(42)
    z_b, imp_b, loo_rmse = model_b_hgb_loo(X_scaled, y_anchor, feature_names)
    print(f"  LOO-CV RMSE: {loo_rmse:.4f} (anchor z-score units)")

    # ── Model C ─────────────────────────────────────────────────────────────
    print("\nModel C — Spatial KNN smoother…")
    # Use z_a as the base signal for spatial smoothing (more stable than z_b)
    z_c = model_c_spatial(z_a, lats, lngs, max_dist_km=20.0)

    # ── Ensemble ─────────────────────────────────────────────────────────────
    z_ens = 0.45 * z_a + 0.35 * z_b + 0.20 * z_c

    # Re-standardise ensemble to avoid scale drift
    z_ens = (z_ens - z_ens.mean()) / (z_ens.std() or 1.0)

    # ── Targeted Karnataka income blend ──────────────────────────────────────
    # Real government income data, but only for ~58/585 pincodes — kept out of
    # the shared feature matrix (see load_karnataka_income()) and blended in
    # here so it only nudges the Karnataka rows it actually describes, not
    # every pincode in the country via median-imputation leakage into the
    # global PCA/HGB refit.
    KA_BLEND_WEIGHT = 0.3
    ka_income = load_karnataka_income()
    ka_common = [pc for pc in pincodes if pc in ka_income.index]
    if len(ka_common) >= 2:
        pc_to_idx = {pc: i for i, pc in enumerate(pincodes)}
        ka_vals = ka_income.loc[ka_common]
        ka_std = ka_vals.std() or 1.0
        ka_z = (ka_vals - ka_vals.mean()) / ka_std
        for pc in ka_common:
            i = pc_to_idx[pc]
            z_ens[i] = (1 - KA_BLEND_WEIGHT) * z_ens[i] + KA_BLEND_WEIGHT * float(ka_z[pc])
        print(f"\nKarnataka income blend: {len(ka_common)} pincodes "
              f"(weight={KA_BLEND_WEIGHT}, real DES 2019-20 taluk income)")

    # ── Anomaly detection ─────────────────────────────────────────────────────
    print("\nAnomaly detection (IsolationForest)…")
    anomaly_flags = detect_anomalies(X_scaled, pincodes, feature_names, group_key=group_key)
    n_flagged = sum(1 for v in anomaly_flags.values() if v["is_anomaly"])
    print(f"  {n_flagged}/{len(pincodes)} pincodes flagged as anomalous")

    # ── Spatial Moran's I ─────────────────────────────────────────────────────
    mi = morans_i(z_ens, lats, lngs)
    print(f"\n  Moran's I (spatial autocorrelation): {mi:.4f}"
          f"  ({'positive — expected ✓' if mi > 0 else 'negative — WARN'})")

    # ── Income estimation ─────────────────────────────────────────────────────
    income_df = estimate_income(z_ens, pincodes, group_key, pincode_mpce)

    # ── Compare with original fixed-weight PPI ───────────────────────────────
    orig_path = OUT / "ppi_pincode.csv"
    if orig_path.exists():
        orig = pd.read_csv(orig_path, dtype={"pincode": str}).set_index("pincode")
        if "ppi" in orig.columns:
            income_df["ppi_original"] = orig["ppi"].reindex(income_df.index)

    # Add name + coords
    if not names_df.empty and "name" in names_df.columns:
        income_df["name"] = names_df["name"].reindex(income_df.index)
    income_df["lat"] = coords.loc[income_df.index, "lat"]
    income_df["lng"] = coords.loc[income_df.index, "lng"]

    # ── Validation gates ─────────────────────────────────────────────────────
    print("\nValidation gates (ML PPI):")
    gates = [("110003","110017","Golf Links > Saket"),
             ("110017","110040","Saket > Narela"),
             ("110003","110075","Golf Links > Dwarka"),
             ("122022","122002","Golf Course Rd > Gurgaon City"),
             ("400021","400086","Cuffe Parade > Borivali"),
             ("400006","400097","Malabar Hill > Malad East"),
             ("400060","400614","Juhu > Vashi"),
             ("560025","560035","Indiranagar > Electronic City"),
             ("560025","560064","Indiranagar > Yelahanka"),
             ("560027","560047","Koramangala > Hebbal")]
    gate_results = []
    for hi, lo, label in gates:
        if hi in income_df.index and lo in income_df.index:
            ok = income_df.loc[hi,"ppi_ml"] > income_df.loc[lo,"ppi_ml"]
            r = (f"{'PASS' if ok else 'FAIL'}  {label}: "
                 f"PPI({hi})={income_df.loc[hi,'ppi_ml']} vs PPI({lo})={income_df.loc[lo,'ppi_ml']}")
            print(f"  {r}")
            gate_results.append(r)

    # ── PPI stability gate: no swing > 10pt vs previous run ──────────────────
    PPI_SWING_LIMIT = 10
    if prev_ppi is not None:
        common_pcs = income_df.index.intersection(prev_ppi.index)
        delta = (income_df.loc[common_pcs, "ppi_ml"] - prev_ppi.loc[common_pcs]).abs()
        swings = delta[delta > PPI_SWING_LIMIT].sort_values(ascending=False)
        print(f"\nPPI stability gate (threshold ±{PPI_SWING_LIMIT}pt):")
        if swings.empty:
            print(f"  PASS  All {len(common_pcs)} pincodes within ±{PPI_SWING_LIMIT}pt  "
                  f"(max swing: {delta.max():.1f}pt @ {delta.idxmax()})")
            gate_results.append(f"PASS  PPI stability: max drift {delta.max():.1f}pt (limit {PPI_SWING_LIMIT})")
        else:
            print(f"  WARN  {len(swings)} pincodes swung > {PPI_SWING_LIMIT}pt:")
            for pc, d in swings.head(10).items():
                name = income_df.loc[pc, "name"] if "name" in income_df.columns else pc
                old  = int(prev_ppi.loc[pc])
                new  = int(income_df.loc[pc, "ppi_ml"])
                print(f"    {pc} {name:<25} {old:>3} → {new:>3}  (Δ{d:+.0f})")
            gate_results.append(f"WARN  PPI stability: {len(swings)} pincodes drifted >{PPI_SWING_LIMIT}pt")
    else:
        print("\nPPI stability gate: skipped (no previous run to compare)")

    # ── Write outputs ─────────────────────────────────────────────────────────
    cols_out = ["name","lat","lng","ppi_ml","ppi_original",
                "est_monthly_income_hh","est_monthly_spend_hh"]
    out_df = income_df[[c for c in cols_out if c in income_df.columns]]

    # This computation takes minutes (PCA/HGB/spatial models over the whole
    # dataset) — long enough for a live visit (enrich_single.py) to land
    # mid-run. Hold the same lock it uses, and re-read the file one more time
    # right before writing so any pincode added *during* this run (not part
    # of what this computation started from) survives instead of being
    # silently overwritten — it just won't have benefited from this run's
    # fresh calibration yet, same as any newly-added pincode always is before
    # its first refit. Found + fixed the same class of bug in deploy.sh
    # (b020a5e) this session; this is the other real gap the audit found.
    # ppi_map_data.csv's poi column — computed before the lock, doesn't touch
    # any file another writer shares.
    poi_path = RAW / "poi_density.csv"
    poi_raw = pd.read_csv(poi_path, dtype={"pincode": str}).set_index("pincode")["premium_poi_per_km2"] \
              if poi_path.exists() else pd.Series(dtype=float)
    poi_p95 = float(poi_raw.quantile(0.95)) if not poi_raw.empty else 1.0
    poi_norm = (poi_raw / poi_p95 * 100).clip(0, 100).round(1)

    ml_out_path = OUT / "ppi_ml_refined.csv"
    app_path    = OUT / "ppi_map_data.csv"
    app_dest    = ROOT.parent / "data" / "output" / "ppi_map_data.csv"
    with write_lock():
        if ml_out_path.exists():
            existing = pd.read_csv(ml_out_path, dtype={"pincode": str}).set_index("pincode")
            added_during_run = existing.loc[~existing.index.isin(out_df.index)]
            if not added_during_run.empty:
                print(f"  {len(added_during_run)} pincodes added during this run — preserving them")
                keep_cols = [c for c in cols_out if c in added_during_run.columns]
                out_df = pd.concat([out_df, added_during_run[keep_cols]])
        out_df = out_df.sort_values("ppi_ml", ascending=False)
        out_df.to_csv(ml_out_path)

        # ppi_map_data.csv — frontend-facing format with poi column. Written
        # inside the same lock acquisition as ppi_ml_refined.csv above (not a
        # separate one) since enrich_single.py's own writes to this file are
        # part of the identical read-modify-write cycle it holds the lock for.
        app_df = pd.DataFrame({
            "name":   out_df["name"],
            "lat":    out_df["lat"],
            "lng":    out_df["lng"],
            "ppi":    out_df["ppi_ml"],
            "income": out_df["est_monthly_income_hh"],
            "poi":    poi_norm.reindex(out_df.index),
        })
        app_df.index.name = "pincode"
        app_df = app_df.sort_values("ppi", ascending=False)
        app_df.to_csv(app_path)
        app_dest.parent.mkdir(parents=True, exist_ok=True)
        app_df.to_csv(app_dest)
    print(f"  {app_dest}  ({len(app_df)} pincodes, poi included)")

    # Dual-write to the database (no-op unless DATABASE_URL is set — see _db.py).
    # Only enrich_single.py/batch_enrich_hces.py did this before — a full refit
    # never has, so every full refit silently left the DB further behind the
    # CSV (found 2026-08-08: DB had 409 pincodes, CSV had 600). CSV stays the
    # source of truth; this just keeps the DB from drifting again. Outside the
    # file lock — DB writes go through their own transaction, not this lock.
    try:
        db_rows = [
            {
                "pincode": pc, "name": r.get("name") or pc,
                "lat": r["lat"], "lng": r["lng"], "ppi_ml": r["ppi_ml"],
                "ppi_original": r.get("ppi_original"),
                "est_monthly_income_hh": r["est_monthly_income_hh"],
                "est_monthly_spend_hh": r.get("est_monthly_spend_hh"),
            }
            for pc, r in out_df.iterrows()
        ]
        n = _db.bulk_upsert_pincodes(db_rows)
        if n:
            print(f"  DB dual-write: upserted {n} pincodes")
    except Exception as e:
        print(f"  WARN: DB dual-write failed (CSV write already succeeded): {e}", flush=True)

    # ── Feature importance summary ────────────────────────────────────────────
    print("\nFeature importances:")
    print("  Model A (PCA+Ridge):")
    for fn, w in sorted(zip(feature_names, imp_a), key=lambda x: -x[1]):
        print(f"    {fn:<35} {w:.4f}")
    print("  Model B (HGB permutation):")
    for fn, w in sorted(imp_b.items(), key=lambda x: -x[1]):
        print(f"    {fn:<35} {w:.4f}")

    # ── Anomaly report ────────────────────────────────────────────────────────
    print("\nTop anomalous pincodes:")
    sorted_anom = sorted(anomaly_flags.items(), key=lambda x: -x[1]["anomaly_score"])
    for pc, info in sorted_anom[:8]:
        flag = "⚠ " if info["is_anomaly"] else "  "
        name_str = income_df.loc[pc,"name"] if "name" in income_df.columns and pc in income_df.index else ""
        print(f"  {flag}{pc} {name_str:<20} score={info['anomaly_score']:.3f}"
              f"  top_proxy={info['top_deviant_proxy']}")

    # ── JSON diagnostics ──────────────────────────────────────────────────────
    diagnostics = {
        "model_a": {
            "pca_n_components": int(pca_obj.n_components_),
            "variance_explained_pct": [round(float(v)*100, 2) for v in var_exp],
            "feature_importance": {fn: round(float(w), 4)
                                   for fn, w in zip(feature_names, imp_a)},
        },
        "model_b": {
            "loo_rmse": round(loo_rmse, 4),
            "feature_importance": imp_b,
        },
        "ensemble_weights": {"model_a": 0.45, "model_b": 0.35, "model_c": 0.20},
        "morans_i": round(mi, 4),
        "anomalies": {
            pc: info for pc, info in sorted_anom
            if info["is_anomaly"] or info["anomaly_score"] > 0.15
        },
        "validation_gates": gate_results,
    }
    (OUT / "ml_diagnostics.json").write_text(
        json.dumps(diagnostics, indent=2, ensure_ascii=False))

    print(f"\nWrote:")
    print(f"  {OUT/'ppi_ml_refined.csv'}")
    print(f"  {OUT/'ml_diagnostics.json'}")
    print(f"\nTop 10 pincodes by ML PPI:")
    print(out_df["ppi_ml"].nlargest(10).to_string())


if __name__ == "__main__":
    main()

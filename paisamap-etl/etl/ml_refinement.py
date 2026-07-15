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


# ── Load all proxies ──────────────────────────────────────────────────────────
def load_features() -> pd.DataFrame:
    frames = {}

    # Standard single-column proxies
    for fname, col in [
        ("property_rates.csv",   "rate_per_sqft"),
        ("bank_deposits.csv",    "deposits_per_capita"),
        ("bank_deposits.csv",    "bank_branches_per_lakh"),
        ("vehicle_density.csv",  "cars_per_1000"),
        ("nightlights.csv",      "radiance_mean"),
        ("itr_filers.csv",       "filers_per_capita"),
        ("poi_density.csv",      "premium_poi_per_km2"),
        ("financial_inclusion.csv", "fin_density_per_km2"),
    ]:
        p = RAW / fname
        if p.exists():
            df = pd.read_csv(p, dtype={"pincode": str}).set_index("pincode")[col]
            frames[col] = df
            print(f"  ✓ {fname:<35} {len(df)} rows")
        else:
            print(f"  · {fname:<35} (not present — skipped)")

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


def _group(pc: str, group_key: dict) -> str:
    """Real district-level group if we have one, else the coarser state fallback."""
    return group_key.get(pc) or _state(pc)


def within_city_normalize(df: pd.DataFrame, cols: set, group_key: dict | None = None) -> pd.DataFrame:
    """
    For each column in `cols`, subtract the city-group mean and divide by std.
    This removes inter-city policy / denominator effects (e.g. Karnataka EV incentives,
    large-district car/2W denominator) so the feature captures only within-city variation.
    Groups come from `group_key` (real HCES district, see load_district_groups())
    where available, falling back to _state() (explicit table → prefix) otherwise.
    Columns not in `cols` are returned unchanged.
    """
    result = df.copy()
    group_key = group_key or {}
    pc_group = {pc: _group(pc, group_key) for pc in df.index}
    for col in cols:
        if col not in df.columns:
            continue
        for grp in set(pc_group.values()):
            mask = [pc_group[pc] == grp for pc in df.index]
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
def model_b_hgb_loo(X_scaled: np.ndarray, y_anchor: np.ndarray, feature_names: list):
    """
    Gradient Boosting with Leave-One-Out cross-validation.
    Returns: (z_scores, feature_importances, loo_rmse)
    """
    model = HistGradientBoostingRegressor(
        max_iter=200, max_depth=3, learning_rate=0.05,
        min_samples_leaf=2, l2_regularization=1.0,
        random_state=42
    )

    # LOO-CV to estimate generalisation error
    loo = LeaveOneOut()
    preds_loo = np.zeros(len(y_anchor))
    for train_idx, test_idx in loo.split(X_scaled):
        m = HistGradientBoostingRegressor(
            max_iter=200, max_depth=3, learning_rate=0.05,
            min_samples_leaf=2, l2_regularization=1.0, random_state=42
        )
        m.fit(X_scaled[train_idx], y_anchor[train_idx])
        preds_loo[test_idx] = m.predict(X_scaled[test_idx])

    loo_rmse = math.sqrt(mean_squared_error(y_anchor, preds_loo))

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
    """
    dist_mat = haversine_matrix(lats, lngs)   # n×n km
    n = len(z_base)
    z_smooth = np.zeros(n)

    for i in range(n):
        dists = dist_mat[i]
        # Include only neighbours within distance limit
        mask = (dists <= max_dist_km)
        mask[i] = True   # always include self
        if mask.sum() < 2:
            z_smooth[i] = z_base[i]
            continue
        # Weight by inverse distance (self gets distance=0.1 to avoid /0)
        inv_d = 1.0 / np.maximum(dists[mask], 0.1)
        z_smooth[i] = np.average(z_base[mask], weights=inv_d)

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

    flags = {}
    for i, pc in enumerate(pincodes):
        # Find the proxy furthest from city-group median
        city = _group(pc, group_key)
        city_mask = np.array([_group(p, group_key) == city for p in pincodes])
        city_mean = X_scaled[city_mask].mean(axis=0)
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
    """
    n = len(values)
    dist = haversine_matrix(lats, lngs)
    W = np.where(dist <= bandwidth_km, 1.0 / (dist + 1.0) ** 2, 0.0)
    np.fill_diagonal(W, 0.0)
    W_row = W / (W.sum(axis=1, keepdims=True) + 1e-9)

    z = values - values.mean()
    numerator = n * (W_row * np.outer(z, z)).sum()
    denominator = W_row.sum() * (z ** 2).sum()
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
    z_adj = z_ensemble.copy()
    for grp, idxs in city_groups.items():
        if len(idxs) < 2:
            continue
        city_z = z_ensemble[idxs]
        # Map group mean z → ln(anchor_spend)
        z_mean = city_z.mean()
        z_std  = city_z.std() or 1.0
        for i in idxs:
            z_adj[i] = (z_ensemble[i] - z_mean) / z_std   # re-standardise within group

    ppi_arr = np.clip(100 + 30 * z_adj, 40, 200).round().astype(int)

    for i, pc in enumerate(pincodes):
        state = _state(pc)
        mpce  = pincode_mpce.get(pc) or HCES_MPCE_CITY.get(state, 7000)
        base_spend = mpce * AVG_HH
        lift  = math.exp(0.55 * float(z_adj[i]))
        spend = base_spend * lift
        spend_share = max(0.45, min(0.85, 0.82 - 0.10 * float(z_adj[i])))
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
    out_df.sort_values("ppi_ml", ascending=False).to_csv(OUT / "ppi_ml_refined.csv")

    # ppi_map_data.csv — frontend-facing format with poi column
    poi_path = RAW / "poi_density.csv"
    poi_raw = pd.read_csv(poi_path, dtype={"pincode": str}).set_index("pincode")["premium_poi_per_km2"] \
              if poi_path.exists() else pd.Series(dtype=float)
    poi_p95 = float(poi_raw.quantile(0.95)) if not poi_raw.empty else 1.0
    poi_norm = (poi_raw / poi_p95 * 100).clip(0, 100).round(1)

    app_df = pd.DataFrame({
        "name":   out_df["name"],
        "lat":    out_df["lat"],
        "lng":    out_df["lng"],
        "ppi":    out_df["ppi_ml"],
        "income": out_df["est_monthly_income_hh"],
        "poi":    poi_norm.reindex(out_df.index),
    })
    app_df.index.name = "pincode"
    app_path = OUT / "ppi_map_data.csv"
    app_df.sort_values("ppi", ascending=False).to_csv(app_path)

    # Sync to app data dir
    app_dest = ROOT.parent / "data" / "output" / "ppi_map_data.csv"
    app_dest.parent.mkdir(parents=True, exist_ok=True)
    app_df.sort_values("ppi", ascending=False).to_csv(app_dest)
    print(f"  {app_dest}  ({len(app_df)} pincodes, poi included)")

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

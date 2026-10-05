"""
enrich_single.py — On-demand single-pincode enrichment.

Usage:
  python3 etl/enrich_single.py <pincode> <lat> <lng> [<name>]

Adds one new pincode to all raw CSVs (using POI-density + state priors),
then re-runs pipeline.py + ml_refinement.py and copies the updated
ppi_map_data.csv to the app's data directory.

Called by server.py /api/enrich for "You are here" pin enrichment.
"""

import math
import sys
from pathlib import Path

import pandas as pd

from _filelock import write_lock
import pin_locate
from pincode_master import assert_real_pincode_ids, real_pincode_id, state_for_pincode
import _db

ROOT = Path(__file__).resolve().parents[1]
REF  = ROOT / "data" / "reference"
RAW  = ROOT / "data" / "raw"
OUT  = ROOT / "data" / "output"
APP  = ROOT.parent           # /paisa-map/

STATE_NAMES = {
    "DL": "Delhi",             "MH": "Maharashtra",       "KA": "Karnataka",
    "HR": "Haryana",           "TS": "Telangana",         "AP": "Andhra Pradesh",
    "TN": "Tamil Nadu",        "GJ": "Gujarat",           "WB": "West Bengal",
    "PB": "Punjab",            "RJ": "Rajasthan",         "MP": "Madhya Pradesh",
    "KL": "Kerala",            "UP": "Uttar Pradesh",     "HP": "Himachal Pradesh",
    "CH": "Chandigarh",        "JK": "Jammu & Kashmir",   "CG": "Chhattisgarh",
    "OD": "Odisha",            "AS": "Assam",             "BR": "Bihar",
    "JH": "Jharkhand",         "GA": "Goa",               "MN": "Manipur",
    "ML": "Meghalaya",         "TR": "Tripura",           "NL": "Nagaland",
    "AR": "Arunachal Pradesh", "MZ": "Mizoram",           "SK": "Sikkim",
}

# State comes from pincode_master.state_for_pincode (India Post reference).
# The old hand-written prefix table lived here and disagreed with that file
# for about 6% of pincodes.

# ── City-level priors for estimating signals ──────────────────────────────────
# Recalibrated 2026-06-18 from actual raw signal medians (DL=22, MH=25, KA=23, HR=2 pincodes).
# Data-derived states use actual medians directly.
# Non-data states apply a 60% correction towards DL ratios for rate/dep/itr/nl/c2w/lux;
# ev corrected upward (all three data states showed underestimation); veh kept per-state.
CITY_PRIORS: dict[str, dict] = {
    # ── Data-derived (actual medians from raw CSVs) ───────────────────────────
    "DL": dict(rate=18010, dep=399318, nl=38.0, itr=0.1836, c2w=0.319, lux=0.0424, ev=0.061,  veh=43.2),
    "MH": dict(rate=27000, dep=570000, nl=46.0, itr=0.2465, c2w=0.477, lux=0.1392, ev=0.0429, veh=20.1),
    "KA": dict(rate=12000, dep=320000, nl=36.0, itr=0.1760, c2w=0.358, lux=0.0759, ev=0.0963, veh=74.1),
    "HR": dict(rate=19000, dep=550000, nl=40.0, itr=0.2120, c2w=1.650, lux=0.1250, ev=0.060,  veh=70.0),
    # ── Corrected (60% DL-anchor correction applied to old hardcoded values) ──
    "TS": dict(rate=6684,  dep=231802, nl=33.5, itr=0.1513, c2w=0.298, lux=0.0458, ev=0.0392, veh=38),
    "AP": dict(rate=4876,  dep=179847, nl=27.2, itr=0.1261, c2w=0.269, lux=0.0393, ev=0.0261, veh=32),
    "TN": dict(rate=6920,  dep=239795, nl=30.8, itr=0.1597, c2w=0.227, lux=0.0393, ev=0.0392, veh=36),
    "GJ": dict(rate=5977,  dep=215816, nl=29.9, itr=0.1765, c2w=0.319, lux=0.0524, ev=0.0523, veh=40),
    "WB": dict(rate=5662,  dep=195833, nl=32.6, itr=0.1177, c2w=0.156, lux=0.0327, ev=0.0261, veh=28),
    "PB": dict(rate=6291,  dep=211819, nl=27.2, itr=0.1513, c2w=0.354, lux=0.0458, ev=0.0261, veh=44),
    "RJ": dict(rate=5190,  dep=177449, nl=25.4, itr=0.1177, c2w=0.283, lux=0.0393, ev=0.0261, veh=36),
    "MP": dict(rate=4404,  dep=155068, nl=21.8, itr=0.1093, c2w=0.255, lux=0.0327, ev=0.0261, veh=30),
    "KL": dict(rate=5662,  dep=195034, nl=23.6, itr=0.1681, c2w=0.198, lux=0.0327, ev=0.0392, veh=32),
    "UP": dict(rate=6448,  dep=187840, nl=27.2, itr=0.1429, c2w=0.298, lux=0.0393, ev=0.0392, veh=36),
}

# Fallback if state unknown — national median derived from data states
_DEFAULT_PRIOR = dict(rate=7000, dep=220000, nl=28.0, itr=0.14,
                      c2w=0.32, lux=0.045, ev=0.038, veh=34)

# ── State-prior proxy signals ─────────────────────────────────────────────────
# No OpenStreetMap lookup: Overpass POI density used to scale these priors per
# pincode, but OSM is ODbL and out of every paid surface (issue #8). A new
# pincode now gets its state prior as-is (the old ratio=1 case) and no
# premium_poi_per_km2 / fin_* values at all; issue #7 tags these as `prior`.
def prior_signals(prior: dict) -> dict:
    """Proxy signals for a pincode with no measured data: the state prior itself."""
    return {
        "rate_per_sqft":       round(prior["rate"]),
        "deposits_per_capita": round(prior["dep"]),
        "radiance_mean":       round(prior["nl"], 1),
        "filers_per_capita":   round(prior["itr"], 4),
        "cars_per_1000":       round(prior["veh"], 1),
        "car_2w_ratio":        prior["c2w"],
        "luxury_share":        prior["lux"],
        "ev_share":            prior["ev"],
    }


def detect_district(lat: float, lng: float):
    """Point-in-polygon → (district_name, locality_name). Returns ('','') on a miss.

    District and state come from the India Post reference; the locality is the
    post office name. No network call.
    """
    try:
        hit = pin_locate.locate(lat, lng)
    except Exception as e:
        print(f"  WARN district lookup failed: {e}", flush=True)
        return "", ""
    if not hit:
        return "", ""
    district = (hit.get("district") or "").strip()
    locality = (hit.get("office_name") or district).strip()
    return district, locality


def register_in_district_map(pc: str, name: str, lat: float, lng: float, state: str) -> None:
    """
    Add new pin-dropped pincode to pincode_district_map.csv so the next
    Phase 1 ETL run (VAHAN/RBI) automatically covers this pincode.
    """
    map_file = REF / "pincode_district_map.csv"
    if not map_file.exists():
        return
    df = pd.read_csv(map_file, dtype={"pincode": str}).set_index("pincode")
    if pc in df.index:
        return

    district, locality = detect_district(lat, lng)
    if not district:
        print(f"  WARN could not detect district for {pc} — skipping district map", flush=True)
        return

    # Pull district population from VAHAN reference if available
    pop_lakh = 10.0
    try:
        vahan = pd.read_csv(REF / "vahan_district_2024.csv")
        kw = district.lower().split()[0]
        match = vahan[vahan["district"].str.lower().str.contains(kw, na=False)]
        if not match.empty and "district_pop_lakh" in vahan.columns:
            pop_lakh = float(match.iloc[0]["district_pop_lakh"])
    except Exception:
        pass

    df.loc[pc] = {
        "name":             name or locality or pc,
        "district":         district,
        "state_code":       state,
        "state_name":       STATE_NAMES.get(state, state),
        "district_pop_lakh": pop_lakh,
    }
    df.to_csv(map_file)
    print(f"  Registered {pc} → {district}, {STATE_NAMES.get(state, state)}", flush=True)


def haversine_series(lat1, lng1, lat2s, lng2s):
    """Vectorized haversine distance (km) from one point to a pandas Series of points."""
    R = 6371.0
    p = math.pi / 180
    dlat = (lat2s - lat1) * p
    dlng = (lng2s - lng1) * p
    a = (dlat / 2).apply(lambda x: math.sin(x) ** 2) + \
        math.cos(lat1 * p) * \
        (lat2s * p).apply(math.cos) * \
        (dlng / 2).apply(lambda x: math.sin(x) ** 2)
    return (2 * R * a.apply(lambda x: math.asin(math.sqrt(max(0, x))))).round(3)


def estimate_via_idw(lat: float, lng: float, state: str, csv_path: Path, col: str,
                      k: int = 5, min_dist_km: float = 0.5):
    """
    Inverse-distance-weighted estimate of `col` for a new point, from the k
    nearest existing pincodes that already have a real (non-null) value in
    csv_path — same spatial-interpolation approach main() already uses for
    PPI/income below, generalized to any raw-signal column.

    Without this, columns like bank_branches_per_lakh (and financial_inclusion's
    branch counts) only ever got set by a one-off pan-India backfill and were
    never touched again for pincodes added afterward — coverage silently eroded
    from ~100% toward single digits as the dataset grew (see project memory:
    34%/15% by 2026-08-02). Every _append() call site for those columns should
    go through this instead of being left NaN for new pincodes.

    Returns None if there's no real data anywhere yet to interpolate from —
    callers should leave the column unset (NaN) in that case, not write a
    fabricated 0/guess.
    """
    if not csv_path.exists():
        return None
    df = pd.read_csv(csv_path, dtype={"pincode": str}).set_index("pincode")
    if col not in df.columns:
        return None
    have_val = df[df[col].notna()]
    if have_val.empty:
        return None

    coords_df = pd.read_csv(RAW / "pincode_coords.csv", dtype={"pincode": str}).set_index("pincode")
    pool = have_val.join(coords_df[["lat", "lng"]], how="inner")
    if pool.empty:
        return None

    # Same-state neighbours preferred, same fallback rule as the PPI interpolation below.
    same_state = pool[[state_for_pincode(idx) == state for idx in pool.index]]
    if len(same_state) >= 3:
        pool = same_state

    dists = haversine_series(lat, lng, pool["lat"], pool["lng"])
    k_eff = min(k, len(pool))
    nearest = dists.nsmallest(k_eff)
    inv_w = 1.0 / nearest.clip(lower=min_dist_km)
    return float((pool.loc[nearest.index, col] * inv_w).sum() / inv_w.sum())


def main():
    if len(sys.argv) < 4:
        print("Usage: python3 enrich_single.py <pincode> <lat> <lng> [<name>]")
        sys.exit(1)

    pc   = real_pincode_id(sys.argv[1])
    if not pc:
        print(f"  REFUSING {sys.argv[1].strip()}: not a real 6-digit pincode")
        sys.exit(2)
    lat  = float(sys.argv[2])
    lng  = float(sys.argv[3])
    name = sys.argv[4].strip() if len(sys.argv) > 4 else pc

    print(f"\n=== Enriching {pc} — {name} ({lat:.4f}, {lng:.4f}) ===\n")

    # ── Guard: already fully enriched (in ML output)? ────────────────────────
    coords = pd.read_csv(RAW / "pincode_coords.csv", dtype={"pincode": str})
    already_in_raw = pc in set(coords["pincode"])
    ppi_path = OUT / "ppi_ml_refined.csv"
    if already_in_raw and ppi_path.exists():
        ml_df = pd.read_csv(ppi_path, dtype={"pincode": str}).set_index("pincode")
        if pc in ml_df.index:
            row = ml_df.loc[pc]
            print(f"  {pc} already in ML output — nothing to do")
            print(f"\n  PPI (ML): {int(row['ppi_ml'])}  income: ₹{int(row['est_monthly_income_hh']):,}/mo")
            sys.exit(0)
    if already_in_raw:
        print(f"  {pc} in raw CSVs but not in ML output — skipping CSV append, re-running pipeline")

    # ── Determine state ───────────────────────────────────────────────────────
    state  = state_for_pincode(pc)
    prior  = CITY_PRIORS.get(state, _DEFAULT_PRIOR)
    if not already_in_raw:
        print(f"  State: {state}  prior: ₹{prior['rate']:,}/sqft")

    # ── Proxy signals from the state prior (no OSM lookup — issue #8) ────────
    signals = prior_signals(prior)
    if not already_in_raw:
        print(f"  Prior rate: ₹{signals['rate_per_sqft']:,}/sqft  "
              f"ITR: {signals['filers_per_capita']:.3f}  "
              f"cars: {signals['cars_per_1000']:.1f}/1k")

    # ── Append to all raw CSVs (only if not already present) ─────────────────
    def _append(fname, col, val):
        if val is None:   # estimate_via_idw() found nothing to interpolate from yet
            return None
        return _append_multi(fname, {col: val})

    def _append_multi(fname, col_vals):
        # Sets several columns on the same new row in one read-modify-write cycle.
        # A plain per-column _append() would break here: the second call's
        # `pc not in df.index` guard would already be False after the first
        # call added the row, silently dropping every column after the first
        # for the same file (exactly how bank_branches_per_lakh would have
        # been lost again if appended as a second separate _append() call
        # right after deposits_per_capita on the same bank_deposits.csv row).
        df = pd.read_csv(RAW / fname, dtype={"pincode": str}).set_index("pincode")
        if pc not in df.index:
            for col, val in col_vals.items():
                if val is not None:
                    df.loc[pc, col] = val
            assert_real_pincode_ids(df.index, context=fname)
            df.to_csv(RAW / fname)
        return df

    # Everything below reads-then-writes the shared raw/output CSVs. Hold one
    # cross-process lock for the whole cycle — server.py can have up to ~15
    # of these subprocesses running concurrently from a single pin-drop
    # (bulkEnrichNearby + prefetchHexNeighbors), plus the nightly cron. A
    # lock only around individual to_csv() calls wouldn't help: a process
    # that already read a stale snapshot before acquiring the lock would
    # still write that stale snapshot back and erase whatever the other
    # process just committed. So lock first, then read.
    with write_lock():
        print("\n  Updating raw CSVs…")

        coords_df = pd.read_csv(RAW / "pincode_coords.csv", dtype={"pincode": str}).set_index("pincode")
        coords_df.loc[pc] = {"lat": lat, "lng": lng}
        assert_real_pincode_ids(coords_df.index, context="pincode_coords.csv")
        coords_df.to_csv(RAW / "pincode_coords.csv")

        names_df = (pd.read_csv(RAW / "pincode_names.csv", dtype={"pincode": str}).set_index("pincode")
                    if (RAW / "pincode_names.csv").exists() else pd.DataFrame())
        names_df.loc[pc] = {"name": name}
        assert_real_pincode_ids(names_df.index, context="pincode_names.csv")
        names_df.to_csv(RAW / "pincode_names.csv")

        _append("property_rates.csv",      "rate_per_sqft",         signals["rate_per_sqft"])
        _append("nightlights.csv",         "radiance_mean",         signals["radiance_mean"])
        _append("itr_filers.csv",          "filers_per_capita",     signals["filers_per_capita"])
        _append("vehicle_density.csv",     "cars_per_1000",         signals["cars_per_1000"])

        # bank_branches_per_lakh via IDW (see estimate_via_idw() docstring — a one-off
        # pan-India backfill populated it once, no incremental path ever touched it
        # again, coverage eroded from ~100% to 34%). Estimated *before* writing this
        # pincode's row so the interpolation pool doesn't include itself, and set in
        # the same read-write cycle as deposits_per_capita (see _append_multi).
        bbpl = estimate_via_idw(lat, lng, state, RAW / "bank_deposits.csv", "bank_branches_per_lakh")
        _append_multi("bank_deposits.csv", {
            "deposits_per_capita":    signals["deposits_per_capita"],
            "bank_branches_per_lakh": round(bbpl, 1) if bbpl is not None else None,
        })

        # No poi_density.csv / financial_inclusion.csv rows: both are OSM-derived (issue #8).

        # rto_enhanced — 4 columns
        rto_df = pd.read_csv(RAW / "rto_enhanced.csv", dtype={"pincode": str}).set_index("pincode")
        if pc not in rto_df.index:
            rto_df.loc[pc, "lmv_per_1000"]  = signals["cars_per_1000"]
            rto_df.loc[pc, "car_2w_ratio"]  = signals["car_2w_ratio"]
            rto_df.loc[pc, "luxury_share"]  = signals["luxury_share"]
            rto_df.loc[pc, "ev_share"]      = signals["ev_share"]
            assert_real_pincode_ids(rto_df.index, context="rto_enhanced.csv")
            rto_df.to_csv(RAW / "rto_enhanced.csv")

        n_total = len(pd.read_csv(RAW / "pincode_coords.csv"))
        print(f"  Dataset: {n_total} pincodes")

        # Auto-register in district map so Phase 1 ETL picks up this pincode next run
        if not already_in_raw:
            register_in_district_map(pc, name, lat, lng, state)

        # ── Spatial interpolation from the stable ML baseline ─────────────────
        # We do NOT re-train the ML model (that would destabilise all 72 pincodes).
        # Instead we use inverse-distance weighting over the 5 nearest trusted
        # pincodes to estimate PPI and income for this new pincode.
        print("\n  Computing PPI via spatial interpolation from ML baseline…")
        ml_df = pd.read_csv(OUT / "ppi_ml_refined.csv", dtype={"pincode": str}).set_index("pincode")

        dists = haversine_series(lat, lng, ml_df["lat"], ml_df["lng"])

        # Use same-state neighbours preferentially; fall back to global if too few
        same_state = ml_df[[state_for_pincode(idx) == state for idx in ml_df.index]]
        pool = same_state if len(same_state) >= 3 else ml_df
        pool_dists = dists.reindex(pool.index)

        k = min(5, len(pool))
        nearest = pool_dists.nsmallest(k)
        inv_w = 1.0 / nearest.clip(lower=0.5)   # cap minimum distance at 0.5 km
        ppi_ml_new    = round(float((pool.loc[nearest.index, "ppi_ml"] * inv_w).sum() / inv_w.sum()))
        income_ml_new = round(float((pool.loc[nearest.index, "est_monthly_income_hh"] * inv_w).sum() / inv_w.sum()), -2)
        spend_ml_new  = round(float((pool.loc[nearest.index, "est_monthly_spend_hh"] * inv_w).sum() / inv_w.sum()), -2)

        nearest_names = pool.loc[nearest.index, "name"].tolist()
        print(f"  Nearest: {nearest_names}")
        print(f"  Interpolated PPI: {ppi_ml_new}  income: ₹{int(income_ml_new):,}/mo")

        # Append single row to the stable ML output (no re-train)
        ml_df.loc[pc] = {
            "name":                  name,
            "lat":                   lat,
            "lng":                   lng,
            "ppi_ml":                ppi_ml_new,
            "ppi_original":          None,
            "est_monthly_income_hh": income_ml_new,
            "est_monthly_spend_hh":  spend_ml_new,
        }
        assert_real_pincode_ids(ml_df.index, context="ppi_ml_refined.csv")
        ml_df.sort_values("ppi_ml", ascending=False).to_csv(OUT / "ppi_ml_refined.csv")

        # Dual-write to the database (no-op unless DATABASE_URL is set — see _db.py).
        # CSV stays the source of truth until the DB has been proven in production.
        try:
            _db.upsert_pincode(pc, name, lat, lng, ppi_ml_new, None,
                                income_ml_new, spend_ml_new)
        except Exception as e:
            print(f"  WARN: DB dual-write failed (CSV write already succeeded): {e}", flush=True)

        # ── Copy output to app directory ───────────────────────────────────────
        print("  Updating app data…")
        names_all  = pd.read_csv(RAW / "pincode_names.csv",   dtype={"pincode": str}).set_index("pincode")
        coords_all = pd.read_csv(RAW / "pincode_coords.csv",  dtype={"pincode": str}).set_index("pincode")

        app_out = APP / "data" / "output"
        app_out.mkdir(parents=True, exist_ok=True)

        combined = pd.DataFrame({
            "name":   names_all["name"].reindex(ml_df.index).combine_first(ml_df.get("name")),
            "lat":    coords_all["lat"].reindex(ml_df.index).combine_first(ml_df.get("lat")),
            "lng":    coords_all["lng"].reindex(ml_df.index).combine_first(ml_df.get("lng")),
            "ppi":    ml_df["ppi_ml"],
            "income": ml_df["est_monthly_income_hh"],
        })
        combined.index.name = "pincode"
        combined.sort_values("ppi", ascending=False).to_csv(app_out / "ppi_map_data.csv")

    # ── Print result ──────────────────────────────────────────────────────────
    if pc in ml_df.index:
        ppi    = int(ml_df.loc[pc, "ppi_ml"])
        income = int(ml_df.loc[pc, "est_monthly_income_hh"])
        rank   = int((ml_df["ppi_ml"] >= ppi).sum())
        print(f"\n  ✓ {name} ({pc})")
        print(f"    PPI (ML): {ppi}   rank #{rank}/{len(ml_df)}")
        print(f"    Est. household income: ₹{income:,}/mo")
    else:
        print(f"\n  ✓ Enrichment complete — {pc} added to dataset")


if __name__ == "__main__":
    main()

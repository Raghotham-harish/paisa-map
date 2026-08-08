#!/usr/bin/env python3
"""
expand_core_idw.py — scale core PPI coverage from the ~900-pincode deep-
enriched set toward the ~15,000 pincodes that have real HCES MPCE data,
using the exact same method batch_enrich_hces.py already uses for its
one-representative-pincode-per-district points (IDW from nearest trusted
neighbours + a dampened MPCE-vs-state-median adjustment) — just applied to
every real pincode in the MPCE crosswalk instead of one per district.

Why a new script instead of calling batch_enrich_hces.py's own per-pincode
functions in a loop: those do a full read-modify-write of every raw CSV for
EVERY new pincode (fine for the 30/day trickle case, but O(n^2) I/O at
~14,000 rows — would take hours). This does the same interpolation math but
vectorized/batched: one KD-tree query for all candidates against the trusted
set, one write per output file, computed once in memory.

Every new pincode still gets backfilled into the raw proxy CSVs
(property_rates.csv, bank_deposits.csv, etc.) using the same CITY_PRIORS/
scale_from_poi baseline batch_enrich_hces.py uses -- skipping this would
mean the next full ml_refinement.py refit silently drops every pincode
added here (the same coverage-erosion failure mode already hit multiple
times in this codebase's history: poi column, boundaries.geojson,
bank_branches_per_lakh, financial_inclusion branches).

Usage:
  cd paisamap-etl && python3 etl/expand_core_idw.py [--limit N] [--dry-run]
"""

from pathlib import Path
import argparse
import sys

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).parent))
from enrich_single import CITY_PRIORS, _DEFAULT_PRIOR, PREFIX_STATE, state_from_pincode, scale_from_poi
import _db

ROOT = Path(__file__).resolve().parents[1]
RAW  = ROOT / "data" / "raw"
OUT  = ROOT / "data" / "output"
APP_BOUNDARIES = ROOT.parent / "data" / "boundaries.geojson"
EARTH_R_KM = 6371.0


def to_cartesian(lat, lng):
    lat_r, lng_r = np.radians(lat), np.radians(lng)
    x = EARTH_R_KM * np.cos(lat_r) * np.cos(lng_r)
    y = EARTH_R_KM * np.cos(lat_r) * np.sin(lng_r)
    z = EARTH_R_KM * np.sin(lat_r)
    return np.stack([x, y, z], axis=-1)


def load_boundary_centroids() -> dict:
    import json
    with open(APP_BOUNDARIES) as f:
        gj = json.load(f)
    centroids = {}
    for feat in gj["features"]:
        pc = feat.get("properties", {}).get("pincode")
        geom = feat.get("geometry") or {}
        coords = geom.get("coordinates")
        if not pc or not coords:
            continue
        pts = []
        def _walk(c):
            if isinstance(c[0], (int, float)):
                pts.append(c)
            else:
                for s in c:
                    _walk(s)
        _walk(coords)
        if not pts:
            continue
        lngs = [p[0] for p in pts]
        lats = [p[1] for p in pts]
        centroids[pc] = ((min(lats) + max(lats)) / 2, (min(lngs) + max(lngs)) / 2)
    return centroids


def batch_idw(cand_coords: np.ndarray, ref_coords: np.ndarray, ref_vals: dict, k: int = 5):
    """ref_vals: dict of {colname: np.array aligned with ref_coords}. Returns
    dict of {colname: np.array aligned with cand_coords}, IDW-averaged over
    the k nearest ref points (Cartesian chord distance, min 0.5km floor)."""
    tree = cKDTree(to_cartesian(ref_coords[:, 0], ref_coords[:, 1]))
    kk = min(k, len(ref_coords))
    dists, idxs = tree.query(to_cartesian(cand_coords[:, 0], cand_coords[:, 1]), k=kk)
    if kk == 1:
        dists, idxs = dists[:, None], idxs[:, None]
    w = 1.0 / np.maximum(dists, 0.5)
    wsum = w.sum(axis=1)
    out = {}
    for col, vals in ref_vals.items():
        out[col] = (vals[idxs] * w).sum(axis=1) / wsum
    return out


def mpce_adj_factor(mpce: np.ndarray, state_median: np.ndarray) -> np.ndarray:
    """Same dampened formula as batch_enrich_hces.py: ratio^0.35."""
    ratio = np.where(state_median > 0, mpce / np.maximum(state_median, 1e-9), 1.0)
    ratio = np.where(mpce > 0, ratio, 1.0)
    return ratio ** 0.35


def append_batch(fname: str, new_rows: pd.DataFrame):
    """Single read-modify-write for one raw CSV, appending all new rows at once."""
    p = RAW / fname
    if not p.exists() or new_rows.empty:
        return
    df = pd.read_csv(p, dtype={"pincode": str}).set_index("pincode")
    new_rows = new_rows[~new_rows.index.isin(df.index)]
    if new_rows.empty:
        return
    combined = pd.concat([df, new_rows[[c for c in new_rows.columns if c in df.columns]]])
    combined.to_csv(p)
    print(f"  {fname}: +{len(new_rows)} rows ({len(combined)} total)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="Cap number of new pincodes (0=all)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    print("Loading trusted baseline (ppi_ml_refined.csv)...")
    ml_df = pd.read_csv(OUT / "ppi_ml_refined.csv", dtype={"pincode": str}).set_index("pincode")
    print(f"  {len(ml_df)} trusted pincodes")

    print("Loading MPCE candidates + boundary centroids...")
    mpce = pd.read_csv(RAW / "mpce_district.csv", dtype={"pincode": str}).dropna(subset=["mpce_combined"])
    centroids = load_boundary_centroids()
    mpce["lat"] = mpce["pincode"].map(lambda pc: centroids.get(pc, (None, None))[0])
    mpce["lng"] = mpce["pincode"].map(lambda pc: centroids.get(pc, (None, None))[1])
    mpce = mpce.dropna(subset=["lat", "lng"])
    mpce = mpce[~mpce["pincode"].isin(ml_df.index)]
    mpce = mpce.drop_duplicates(subset="pincode")
    print(f"  {len(mpce)} candidate pincodes (real MPCE + boundary, not already trusted)")

    if args.limit:
        mpce = mpce.head(args.limit)
        print(f"  Capped to {len(mpce)} via --limit")

    if mpce.empty:
        print("Nothing to do.")
        return

    # ── PPI/income/spend via IDW from the trusted set ────────────────────────
    print("IDW-interpolating PPI/income/spend from trusted baseline...")
    ref_coords = ml_df[["lat", "lng"]].to_numpy(dtype=float)
    cand_coords = mpce[["lat", "lng"]].to_numpy(dtype=float)
    ref_vals = {
        "ppi_ml": ml_df["ppi_ml"].to_numpy(dtype=float),
        "income": ml_df["est_monthly_income_hh"].to_numpy(dtype=float),
        "spend":  ml_df["est_monthly_spend_hh"].to_numpy(dtype=float),
    }
    idw = batch_idw(cand_coords, ref_coords, ref_vals, k=5)

    # ── MPCE-vs-state-median dampened adjustment (same as batch_enrich_hces.py) ──
    state_medians = mpce.groupby("hces_state")["mpce_combined"].transform("median")
    adj = mpce_adj_factor(mpce["mpce_combined"].to_numpy(), state_medians.to_numpy())

    mpce = mpce.copy()
    mpce["ppi_ml"] = np.round(idw["ppi_ml"] * adj).astype(int)
    mpce["est_monthly_income_hh"] = np.round(idw["income"] * adj, -2)
    mpce["est_monthly_spend_hh"]  = np.round(idw["spend"]  * adj, -2)
    # District+state alone is NOT unique per pincode — up to ~167 real, distinct
    # pincodes share one HCES district (found 2026-08-08 live: 97% of the
    # expanded set ended up with a duplicate name, e.g. "Thrissur, Kerala"
    # shown identically for 167 different localities in the UI). Append the
    # pincode so every row has a genuinely unique, still-informative name.
    mpce["name"] = (mpce["hces_district"].str.title() + ", " + mpce["hces_state"].str.title()
                     + " · " + mpce["pincode"])

    print(f"  PPI range: {mpce['ppi_ml'].min()}-{mpce['ppi_ml'].max()}, "
          f"median {mpce['ppi_ml'].median():.0f}")

    if args.dry_run:
        print("\nDRY RUN — not writing anything.")
        print(mpce[["pincode", "name", "ppi_ml", "est_monthly_income_hh"]].head(20).to_string())
        return

    # ── Raw proxy backfill (state-level CITY_PRIORS, batched) ────────────────
    print("Backfilling raw proxy columns (CITY_PRIORS baseline, per state)...")
    mpce["state_code"] = mpce["pincode"].map(state_from_pincode)

    poi_df = pd.read_csv(RAW / "poi_density.csv", dtype={"pincode": str}) \
             if (RAW / "poi_density.csv").exists() else pd.DataFrame(columns=["pincode", "premium_poi_per_km2"])
    poi_by_state = {}
    for sc in mpce["state_code"].unique():
        pcs = [p for p in poi_df["pincode"] if PREFIX_STATE.get(str(p)[:2]) == sc]
        med = poi_df.set_index("pincode").reindex(pcs)["premium_poi_per_km2"].dropna().median() \
              if pcs else np.nan
        poi_by_state[sc] = 15.0 if pd.isna(med) or med < 1 else float(med)

    proxy_rows = {"property_rates.csv": [], "nightlights.csv": [], "poi_density.csv": [],
                  "itr_filers.csv": [], "vehicle_density.csv": [], "rto_enhanced.csv": []}
    dep_rows, fin_rows = [], []

    for sc, grp in mpce.groupby("state_code"):
        prior = CITY_PRIORS.get(sc, _DEFAULT_PRIOR)
        poi_med = poi_by_state.get(sc, 15.0)
        sig = scale_from_poi(poi_med, prior, poi_med)
        idx = grp["pincode"]
        proxy_rows["property_rates.csv"].append(pd.DataFrame({"pincode": idx, "rate_per_sqft": sig["rate_per_sqft"]}))
        proxy_rows["nightlights.csv"].append(pd.DataFrame({"pincode": idx, "radiance_mean": sig["radiance_mean"]}))
        proxy_rows["poi_density.csv"].append(pd.DataFrame({"pincode": idx, "premium_poi_per_km2": sig["premium_poi_per_km2"]}))
        proxy_rows["itr_filers.csv"].append(pd.DataFrame({"pincode": idx, "filers_per_capita": sig["filers_per_capita"]}))
        proxy_rows["vehicle_density.csv"].append(pd.DataFrame({"pincode": idx, "cars_per_1000": sig["cars_per_1000"]}))
        proxy_rows["rto_enhanced.csv"].append(pd.DataFrame({
            "pincode": idx, "lmv_per_1000": sig["cars_per_1000"], "car_2w_ratio": sig["car_2w_ratio"],
            "luxury_share": sig["luxury_share"], "ev_share": sig["ev_share"],
        }))
        dep_rows.append(pd.DataFrame({"pincode": idx, "deposits_per_capita": sig["deposits_per_capita"]}))
        fin_rows.append(pd.DataFrame({"pincode": idx, "fin_density_per_km2": sig["fin_density_per_km2"]}))

    # bank_branches_per_lakh / financial_inclusion branch counts: batch IDW from
    # whichever existing rows already have a real value (same rationale as
    # estimate_via_idw(), just vectorized across all candidates at once).
    def idw_from_existing(fname, col):
        p = RAW / fname
        if not p.exists():
            return None
        df = pd.read_csv(p, dtype={"pincode": str}).set_index("pincode")
        if col not in df.columns:
            return None
        have = df[df[col].notna()]
        coords = pd.read_csv(RAW / "pincode_coords.csv", dtype={"pincode": str}).set_index("pincode")
        pool = have.join(coords[["lat", "lng"]], how="inner")
        if pool.empty:
            return None
        res = batch_idw(cand_coords, pool[["lat", "lng"]].to_numpy(dtype=float),
                        {col: pool[col].to_numpy(dtype=float)}, k=5)
        return res[col]

    bbpl = idw_from_existing("bank_deposits.csv", "bank_branches_per_lakh")
    dep_df = pd.concat(dep_rows, ignore_index=True)
    if bbpl is not None:
        dep_df["bank_branches_per_lakh"] = np.round(bbpl, 1)

    fin_df = pd.concat(fin_rows, ignore_index=True)
    for col in ("sfb_branches", "coop_branches", "rrb_branches", "fin_branches_total"):
        est = idw_from_existing("financial_inclusion.csv", col)
        if est is not None:
            fin_df[col] = np.round(est)

    for fname, parts in proxy_rows.items():
        combined = pd.concat(parts, ignore_index=True).set_index("pincode")
        append_batch(fname, combined)
    append_batch("bank_deposits.csv", dep_df.set_index("pincode"))
    append_batch("financial_inclusion.csv", fin_df.set_index("pincode"))

    # coords/names
    coords_new = mpce.set_index("pincode")[["lat", "lng"]]
    names_new  = mpce.set_index("pincode")[["name"]]
    append_batch("pincode_coords.csv", coords_new)
    append_batch("pincode_names.csv", names_new)

    # ── Core PPI outputs ──────────────────────────────────────────────────────
    print("\nWriting ppi_ml_refined.csv / ppi_map_data.csv...")
    new_ml = mpce.set_index("pincode")[["name", "lat", "lng", "ppi_ml",
                                          "est_monthly_income_hh", "est_monthly_spend_hh"]].copy()
    new_ml["ppi_original"] = None
    ml_out = pd.concat([ml_df, new_ml[ml_df.columns]])
    ml_out.sort_values("ppi_ml", ascending=False).to_csv(OUT / "ppi_ml_refined.csv")
    print(f"  ppi_ml_refined.csv: {len(ml_out)} total ({len(new_ml)} new)")

    poi_final = pd.read_csv(RAW / "poi_density.csv", dtype={"pincode": str}).set_index("pincode")["premium_poi_per_km2"]
    poi_p95 = float(poi_final.quantile(0.95)) if not poi_final.empty else 1.0
    poi_norm = (poi_final / poi_p95 * 100).clip(0, 100).round(1)
    app_df = pd.DataFrame({
        "name": ml_out["name"], "lat": ml_out["lat"], "lng": ml_out["lng"],
        "ppi": ml_out["ppi_ml"], "income": ml_out["est_monthly_income_hh"],
        "poi": poi_norm.reindex(ml_out.index),
    })
    app_df.index.name = "pincode"
    app_df.sort_values("ppi", ascending=False).to_csv(OUT / "ppi_map_data.csv")
    app_df.sort_values("ppi", ascending=False).to_csv(ROOT.parent / "data" / "output" / "ppi_map_data.csv")
    print(f"  ppi_map_data.csv: {len(app_df)} total")

    # ── DB dual-write ─────────────────────────────────────────────────────────
    try:
        db_rows = [
            {"pincode": pc, "name": r["name"], "lat": r["lat"], "lng": r["lng"],
             "ppi_ml": int(r["ppi_ml"]), "ppi_original": None,
             "est_monthly_income_hh": r["est_monthly_income_hh"],
             "est_monthly_spend_hh": r["est_monthly_spend_hh"]}
            for pc, r in new_ml.iterrows()
        ]
        n = _db.bulk_upsert_pincodes(db_rows)
        print(f"  DB dual-write: upserted {n}")
    except Exception as e:
        print(f"  WARN: DB dual-write failed (CSV write already succeeded): {e}")

    print(f"\nDone. {len(new_ml)} new pincodes added ({len(ml_out)} total core PPI).")


if __name__ == "__main__":
    main()

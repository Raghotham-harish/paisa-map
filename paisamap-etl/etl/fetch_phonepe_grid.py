#!/usr/bin/env python3
"""
fetch_phonepe_grid.py — Fresh, fine-grained UPI transaction density from
PhonePe Pulse's live geospatial grid, replacing the old district-level
approach (fetch_phonepe_pulse.py) which PhonePe stopped supporting: their
GitHub repo's per-state district drill-down path is gone, and the live
phonepe.com/pulse site itself no longer offers district navigation at all
(confirmed 2026-08-08 by driving the actual site with Playwright — clicking
a state does nothing, the feature was removed from the product, not just
relocated).

What the live site DOES still load (found via network inspection, same
public no-auth JSON its own page fetches):
  https://www.phonepe.com/pulsestatic/get-production/map/transaction/
  country/india/{year}/{quarter}.json
34,401 real lat/lng points nationwide, each carrying a transaction COUNT for
that quarter. Verified genuine (not some unrelated metric): summing every
point labelled "andaman & nicobar islands" gives exactly 7,753,095 — an
exact match to that state's official quarterly transaction count from the
site's own state-level "hover" endpoint.

This is a different metric than the old upi_txn_value_per_capita (₹ value
per capita, district-level) — this is raw transaction COUNT with no
population normalization, same style as nightlights.csv's radiance_mean.
Kept as its own distinct signal (upi_txn_count_nearby) rather than
conflated with the old column, which stays as-is (upi_activity.csv, frozen
at whatever historical vintage it already has — PhonePe hasn't published
district-level data since Q4 2024, nothing to re-fetch there).

IDW-interpolates the grid onto every pincode with a real boundary centroid
(reads data/boundaries.geojson directly, same reference list
build_broad_coverage.py uses) via a k-nearest-neighbour, inverse-distance
weighted average, same approach as enrich_single.py's estimate_via_idw().
Distances use a Cartesian (unit-sphere chord) projection rather than raw
lat/lng degrees, so results aren't distorted at India's latitude range
(8-35N) the way a naive degrees-KDTree would be.

Output: data/raw/upi_txn_density_grid.csv — pincode, upi_txn_count_nearby,
quarter_label

Usage:
  cd paisamap-etl && python3 etl/fetch_phonepe_grid.py
"""

from pathlib import Path
import datetime
import json

import numpy as np
import pandas as pd
import requests
from scipy.spatial import cKDTree

ROOT = Path(__file__).resolve().parents[1]
RAW  = ROOT / "data" / "raw"
APP_BOUNDARIES = ROOT.parent / "data" / "boundaries.geojson"

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PaisaMap-ETL/1.0; +https://paisamap.cooterlabs.com)"}
EARTH_R_KM = 6371.0


def grid_url(year: int, q: int) -> str:
    return (f"https://www.phonepe.com/pulsestatic/get-production/map/transaction/"
            f"country/india/{year}/{q}.json")


def find_latest_quarter(max_lookback: int = 8) -> tuple[int, int]:
    """Try the most recent few (year, quarter) combos, newest first, until one 200s."""
    today = datetime.date.today()
    year, q = today.year, (today.month - 1) // 3 + 1
    for _ in range(max_lookback):
        r = requests.head(grid_url(year, q), headers=HEADERS, timeout=10)
        if r.status_code == 200:
            return year, q
        q -= 1
        if q == 0:
            q, year = 4, year - 1
    raise SystemExit("Could not find any available PhonePe Pulse grid quarter")


def fetch_grid(year: int, q: int) -> tuple[np.ndarray, np.ndarray]:
    r = requests.get(grid_url(year, q), headers=HEADERS, timeout=30)
    r.raise_for_status()
    rows = r.json()["data"]["data"]["data"]  # [[lat, lng, metric, label], ...]
    latlng = np.array([[row[0], row[1]] for row in rows])
    metric = np.array([row[2] for row in rows], dtype=float)
    return latlng, metric


def load_boundary_centroids() -> tuple[list[str], np.ndarray]:
    with open(APP_BOUNDARIES) as f:
        gj = json.load(f)
    pincodes, coords = [], []
    for feat in gj["features"]:
        pc = feat.get("properties", {}).get("pincode")
        geom = feat.get("geometry") or {}
        c = geom.get("coordinates")
        if not pc or not c:
            continue
        pts = []
        def _walk(x):
            if isinstance(x[0], (int, float)):
                pts.append(x)
            else:
                for s in x:
                    _walk(s)
        _walk(c)
        if not pts:
            continue
        lngs = [p[0] for p in pts]
        lats = [p[1] for p in pts]
        pincodes.append(pc)
        coords.append(((min(lats) + max(lats)) / 2, (min(lngs) + max(lngs)) / 2))
    return pincodes, np.array(coords)


def to_cartesian(latlng: np.ndarray) -> np.ndarray:
    """lat/lng (degrees) -> 3D Cartesian km on Earth's surface. Euclidean chord
    distance between two such points is a distortion-free proxy for great-circle
    distance at the scales this script cares about (a few tens of km), unlike a
    KDTree built directly on raw lat/lng degrees (which distorts badly away from
    the equator since 1deg lng shrinks by cos(lat))."""
    lat_r = np.radians(latlng[:, 0])
    lng_r = np.radians(latlng[:, 1])
    x = EARTH_R_KM * np.cos(lat_r) * np.cos(lng_r)
    y = EARTH_R_KM * np.cos(lat_r) * np.sin(lng_r)
    z = EARTH_R_KM * np.sin(lat_r)
    return np.stack([x, y, z], axis=-1)


def idw_interpolate(pin_coords: np.ndarray, grid_coords: np.ndarray, grid_vals: np.ndarray,
                     k: int = 8, max_dist_km: float = 30.0) -> np.ndarray:
    tree = cKDTree(to_cartesian(grid_coords))
    dists_km, idxs = tree.query(to_cartesian(pin_coords), k=k)
    out = np.full(len(pin_coords), np.nan)
    for i in range(len(pin_coords)):
        d, idx = dists_km[i], idxs[i]
        mask = d <= max_dist_km
        if not mask.any():
            continue
        w = 1.0 / np.maximum(d[mask], 0.5)
        out[i] = np.average(grid_vals[idx][mask], weights=w)
    return out


def main():
    print("Finding latest available PhonePe Pulse grid quarter...")
    year, q = find_latest_quarter()
    print(f"  Using {year} Q{q}")

    print("Fetching transaction-count grid...")
    grid_coords, grid_vals = fetch_grid(year, q)
    print(f"  {len(grid_coords)} grid points")

    print("Loading boundary centroids...")
    pincodes, pin_coords = load_boundary_centroids()
    print(f"  {len(pincodes)} pincodes")

    print("IDW-interpolating (k=8, max 30km)...")
    vals = idw_interpolate(pin_coords, grid_coords, grid_vals)

    out = pd.DataFrame({
        "pincode": pincodes,
        "upi_txn_count_nearby": vals,
        "quarter_label": f"{year} Q{q}",
    }).dropna(subset=["upi_txn_count_nearby"])
    out["upi_txn_count_nearby"] = out["upi_txn_count_nearby"].round(0).astype(int)
    out = out.sort_values("pincode")

    dest = RAW / "upi_txn_density_grid.csv"
    out.to_csv(dest, index=False)
    print(f"\nWrote {len(out)} rows -> {dest}")
    print(out["upi_txn_count_nearby"].describe())


if __name__ == "__main__":
    main()

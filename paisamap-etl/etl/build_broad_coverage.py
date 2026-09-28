#!/usr/bin/env python3
"""
build_broad_coverage.py — pan-India "broad coverage" signals, independent of
full PPI/deep enrichment.

The full ML-refined PPI (ppi_map_data.csv) only covers ~600 pincodes — the
ones that have been through deep enrichment (property-rate scraping, POI
density, nightlights, etc. via enrich_single.py / batch_enrich_hces.py).
That's a slow, targeted process (batch cron capped at 30 districts/day).

But a real, pan-India dataset already sits past that boundary:
  - mpce_district.csv             — 15,443 pincodes, real HCES 2023-24 district
                                     spend data (mpce_combined, hces_ppi)

(The RBI branch count and the PhonePe live-site grid used to ride along here;
both were removed — this file is public, see PUBLIC_COLUMNS below.)

It has no lat/lng of its own. This script derives one from data/boundaries.geojson
(19,444 real government PIN-code polygons, computed via centroid of each
polygon's bounding box) — the same source the frontend already uses for
choropleth shapes — so no new geocoding is needed.

Output: data/output/broad_coverage.csv (served statically, same as
ppi_map_data.csv), columns: PUBLIC_COLUMNS

Usage:
  cd paisamap-etl && python3 etl/build_broad_coverage.py
"""

from pathlib import Path
import json
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW  = ROOT / "data" / "raw"
OUT  = ROOT / "data" / "output"
APP_BOUNDARIES = ROOT.parent / "data" / "boundaries.geojson"
APP_OUT        = ROOT.parent / "data" / "output" / "broad_coverage.csv"

PUBLIC_COLUMNS = ["pincode", "name", "lat", "lng", "mpce_combined", "hces_ppi", "state", "district"]


def load_boundary_centroids() -> dict[str, tuple[float, float]]:
    """pincode -> (lat, lng), computed as each polygon's bounding-box centre.
    Matches the frontend's boundaryCentroid() (index.html) so a broad-coverage
    point lands in the same place the choropleth shape for that pincode would."""
    with open(APP_BOUNDARIES) as f:
        gj = json.load(f)

    centroids = {}
    for feat in gj["features"]:
        pc = feat.get("properties", {}).get("pincode")
        if not pc:
            continue
        geom = feat.get("geometry") or {}
        coords = geom.get("coordinates")
        if not coords:
            continue
        # Flatten Polygon/MultiPolygon ring coordinates to a flat list of [lng, lat] pairs
        pts = []
        def _walk(c):
            if isinstance(c[0], (int, float)):
                pts.append(c)
            else:
                for sub in c:
                    _walk(sub)
        _walk(coords)
        if not pts:
            continue
        lngs = [p[0] for p in pts]
        lats = [p[1] for p in pts]
        lat = (min(lats) + max(lats)) / 2
        lng = (min(lngs) + max(lngs)) / 2
        centroids[pc] = (lat, lng)
    return centroids


def build() -> pd.DataFrame:
    print("Loading boundary centroids…")
    centroids = load_boundary_centroids()
    print(f"  {len(centroids)} pincodes with a real boundary")

    mpce = pd.read_csv(RAW / "mpce_district.csv", dtype={"pincode": str})
    print(f"  MPCE data: {len(mpce)} pincodes")

    # This file is served statically to anonymous visitors, so it carries ONLY
    # redistributable columns. The RBI branch count (psu_branch_count) is
    # internal-only until the RBI terms question is answered, and the PhonePe
    # live-site grid was dropped entirely (site terms ban commercial use) —
    # see docs/DATA_LICENSING.md. PUBLIC_COLUMNS is enforced by
    # tests/test_public_data_licensing.py.
    merged = mpce[["pincode", "mpce_combined", "hces_ppi", "hces_state", "hces_district"]].rename(
        columns={"hces_state": "state", "hces_district": "district"})

    merged["lat"] = merged["pincode"].map(lambda pc: centroids.get(pc, (None, None))[0])
    merged["lng"] = merged["pincode"].map(lambda pc: centroids.get(pc, (None, None))[1])

    before = len(merged)
    merged = merged.dropna(subset=["lat", "lng"])
    print(f"  {len(merged)}/{before} rows have a real boundary centroid (rest dropped — "
          f"no way to plot them without one)")

    merged["name"] = merged["pincode"]
    out = merged[PUBLIC_COLUMNS]
    out = out.sort_values("pincode")
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    df = build()
    dest = OUT / "broad_coverage.csv"
    df.to_csv(dest, index=False)
    print(f"\nWrote {len(df)} rows -> {dest}")

    APP_OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(APP_OUT, index=False)
    print(f"Synced -> {APP_OUT}")

    print(f"\nCoverage: {df['mpce_combined'].notna().sum()} with MPCE data")


if __name__ == "__main__":
    main()

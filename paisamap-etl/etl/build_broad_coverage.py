#!/usr/bin/env python3
"""
build_broad_coverage.py — pan-India "broad coverage" signals, independent of
full PPI/deep enrichment.

The full ML-refined PPI (ppi_map_data.csv) only covers ~600 pincodes — the
ones that have been through deep enrichment (property-rate scraping, POI
density, nightlights, etc. via enrich_single.py / batch_enrich_hces.py).
That's a slow, targeted process (batch cron capped at 30 districts/day).

But two real, pan-India datasets already sit unused past that 600-pincode
boundary:
  - rbi_branch_counts_india.csv   — 18,599 pincodes, real RBI branch-master data
  - mpce_district.csv             — 15,443 pincodes, real HCES 2023-24 district
                                     spend data (mpce_combined, hces_ppi)

Neither has its own lat/lng. This script derives one from data/boundaries.geojson
(19,444 real government PIN-code polygons, computed via centroid of each
polygon's bounding box) — the same source the frontend already uses for
choropleth shapes — so no new geocoding is needed.

Output: data/output/broad_coverage.csv (served statically, same as
ppi_map_data.csv), columns: pincode,name,lat,lng,psu_branch_count,
mpce_combined,hces_ppi,state,district

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

    rbi = pd.read_csv(RAW / "rbi_branch_counts_india.csv", dtype={"pincode": str})
    print(f"  RBI branch data: {len(rbi)} pincodes")

    mpce = pd.read_csv(RAW / "mpce_district.csv", dtype={"pincode": str})
    print(f"  MPCE data: {len(mpce)} pincodes")

    # Outer-join on pincode — a pincode can have RBI data, MPCE data, or both.
    merged = rbi[["pincode", "state", "district", "psu_branch_count"]].merge(
        mpce[["pincode", "mpce_combined", "hces_ppi", "hces_state", "hces_district"]],
        on="pincode", how="outer",
    )
    # Prefer the RBI-sourced state/district name; fall back to the HCES one.
    merged["state"]    = merged["state"].fillna(merged["hces_state"])
    merged["district"] = merged["district"].fillna(merged["hces_district"])
    merged = merged.drop(columns=["hces_state", "hces_district"])

    merged["lat"] = merged["pincode"].map(lambda pc: centroids.get(pc, (None, None))[0])
    merged["lng"] = merged["pincode"].map(lambda pc: centroids.get(pc, (None, None))[1])

    before = len(merged)
    merged = merged.dropna(subset=["lat", "lng"])
    print(f"  {len(merged)}/{before} rows have a real boundary centroid (rest dropped — "
          f"no way to plot them without one)")

    merged["name"] = merged["pincode"]
    out = merged[["pincode", "name", "lat", "lng", "psu_branch_count",
                  "mpce_combined", "hces_ppi", "state", "district"]]
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

    print(f"\nCoverage: {df['psu_branch_count'].notna().sum()} with branch data, "
          f"{df['mpce_combined'].notna().sum()} with MPCE data, "
          f"{((df['psu_branch_count'].notna()) & (df['mpce_combined'].notna())).sum()} with both")


if __name__ == "__main__":
    main()

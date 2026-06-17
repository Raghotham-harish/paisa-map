"""
fetch_boundaries.py — Pre-fetch boundary polygons for all pincodes.

Uses Nominatim REVERSE geocode (one call per pincode, not search) at
zoom=12 to return the OSM area boundary at neighbourhood/suburb level.
This matches what fetchLocalityBoundary() does in the browser.

Output: paisa-map/data/boundaries.geojson

Run once after any significant dataset expansion:
  cd paisamap-etl
  python3 etl/fetch_boundaries.py

~6 minutes for 73 pincodes (5s between calls, Nominatim ToS).
Pass --resume to skip pincodes already in existing boundaries.geojson.
"""

import argparse
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

ROOT    = Path(__file__).resolve().parents[1]
APP     = ROOT.parent
OUT_CSV = APP / "data" / "output" / "ppi_map_data.csv"
OUT_GEO = APP / "data" / "boundaries.geojson"

NOMINATIM_REVERSE = "https://nominatim.openstreetmap.org/reverse"
HEADERS = {"User-Agent": "PaisaMap-Boundaries/1.0 (one-time batch)", "Accept-Language": "en"}
DELAY   = 5.0    # seconds — conservative for batch use of public Nominatim


def reverse_geocode(lat: float, lng: float, zoom: int = 12):
    """
    Reverse geocode at zoom=12 → returns the OSM area polygon at suburb/neighbourhood level.
    This is the same call fetchLocalityBoundary() makes in the browser.
    """
    params = urllib.parse.urlencode({
        "lat":            lat,
        "lon":            lng,
        "zoom":           zoom,
        "format":         "geojson",
        "polygon_geojson": 1,
    })
    url = f"{NOMINATIM_REVERSE}?{params}"
    req = urllib.request.Request(url, headers=HEADERS)

    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read())
            geom = data.get("geometry")
            if geom and geom.get("type") in ("Polygon", "MultiPolygon"):
                return geom, data.get("display_name", "")
            # Point result — try a coarser zoom
            if zoom > 10:
                time.sleep(DELAY)
                return reverse_geocode(lat, lng, zoom - 1)
            return None, ""
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 30 * (attempt + 1)
                print(f"\n    429 — waiting {wait}s…", end="", flush=True)
                time.sleep(wait)
            else:
                print(f"\n    HTTP {e.code}", end="", flush=True)
                return None, ""
        except Exception as e:
            print(f"\n    ERR: {e}", end="", flush=True)
            return None, ""
    return None, ""


def _circle_coords(lat: float, lng: float, deg: float = 0.012):
    """12-point circle as GeoJSON fallback polygon."""
    import math
    pts = []
    for i in range(13):
        a = 2 * math.pi * i / 12
        pts.append([round(lng + deg * math.cos(a), 6),
                    round(lat + deg * math.sin(a), 6)])
    return pts


def fetch_all(resume: bool) -> dict:
    df = pd.read_csv(OUT_CSV, dtype={"pincode": str})
    total = len(df)

    existing: dict[str, dict] = {}
    if resume and OUT_GEO.exists():
        with open(OUT_GEO) as f:
            old = json.load(f)
        for feat in old.get("features", []):
            pc = feat.get("properties", {}).get("pincode", "")
            if pc:
                existing[pc] = feat
        print(f"Resuming: {len(existing)}/{total} already done")

    features = []
    hits, misses = 0, 0

    for i, row in df.iterrows():
        pc     = str(row["pincode"])
        name   = str(row["name"])
        lat    = float(row["lat"])
        lng    = float(row["lng"])
        ppi    = int(row["ppi"])
        income = int(row["income"])
        n      = i + 1

        # Reuse cached
        if pc in existing:
            feat = existing[pc]
            feat["properties"].update(ppi=ppi, income=income, name=name)
            features.append(feat)
            print(f"  [{n:02d}/{total}] ↩  {pc} {name} (cached)")
            continue

        print(f"  [{n:02d}/{total}] ↓  {pc} {name:<32}", end="", flush=True)
        time.sleep(DELAY)

        geom, osm_name = reverse_geocode(lat, lng)

        if geom:
            features.append({
                "type": "Feature",
                "geometry": geom,
                "properties": {
                    "pincode": pc, "name": name,
                    "ppi": ppi, "income": income,
                },
            })
            hits += 1
            print(f" ✓  ({geom['type']})")
        else:
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [_circle_coords(lat, lng)],
                },
                "properties": {
                    "pincode": pc, "name": name,
                    "ppi": ppi, "income": income,
                    "_synthetic": True,
                },
            })
            misses += 1
            print(" ✗ (fallback circle)")

    print(f"\nDone: {hits} real polygons, {misses} fallback circles / {total} pincodes")
    return {"type": "FeatureCollection", "features": features}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--resume",  action="store_true", help="Skip already-fetched pincodes")
    ap.add_argument("--dry-run", action="store_true", help="Fetch but don't write file")
    args = ap.parse_args()

    print(f"Source: {OUT_CSV}")
    print(f"Output: {OUT_GEO}")
    print(f"Delay:  {DELAY}s between requests\n")

    geojson = fetch_all(resume=args.resume)

    if args.dry_run:
        print(f"[DRY RUN] would write {len(geojson['features'])} features")
        return

    OUT_GEO.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_GEO, "w") as f:
        json.dump(geojson, f, separators=(",", ":"))

    kb = OUT_GEO.stat().st_size // 1024
    print(f"\nWritten: {OUT_GEO}  ({kb} KB)")
    print("\nNext steps:")
    print("  git add data/boundaries.geojson")
    print("  git commit -m 'Add pre-fetched pincode boundary polygons'")
    print("  git push")


if __name__ == "__main__":
    main()

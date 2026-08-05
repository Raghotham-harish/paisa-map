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


def reverse_geocode(lat: float, lng: float, zoom: int = 14):
    """
    Reverse geocode at zoom=14 → returns the OSM area polygon at suburb level. Starts at
    the same zoom fetchLocalityBoundary() uses in the browser (previously this batch script
    started at zoom=12 — one level coarser than the live path, which is how oversized
    town/borough-scale polygons ended up cached as if they were tight locality boundaries).
    Falls back at most to zoom=12 on a miss; never goes coarser than that here — anything
    coarser is city/county-scale and gets rejected by the caller's size check regardless.
    """
    params = urllib.parse.urlencode({
        "lat":            lat,
        "lon":            lng,
        "zoom":           zoom,
        "format":         "json",        # must be json, not geojson
        "polygon_geojson": 1,            # polygon lands in data["geojson"], not data["geometry"]
        "addressdetails":  1,
    })
    url = f"{NOMINATIM_REVERSE}?{params}"
    req = urllib.request.Request(url, headers=HEADERS)

    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read())
            geom = data.get("geojson")   # polygon_geojson=1 puts boundary here
            if geom and geom.get("type") in ("Polygon", "MultiPolygon"):
                return geom, data.get("display_name", "")
            # No polygon at this zoom — try one level coarser
            if zoom > 12:
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


def _haversine_km(lat1, lng1, lat2, lng2):
    import math
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1, math.sqrt(a)))


def _bbox_diag_km(geom) -> float:
    """Bounding-box diagonal of a Polygon/MultiPolygon, in km — a cheap proxy for
    'how big an area does this polygon cover' without needing a geometry library."""
    def flatten(coords):
        if isinstance(coords[0], (int, float)):
            yield coords
        else:
            for c in coords:
                yield from flatten(c)
    pts = list(flatten(geom["coordinates"]))
    lngs = [p[0] for p in pts]
    lats = [p[1] for p in pts]
    return _haversine_km(min(lats), min(lngs), max(lats), max(lngs))


def nearest_neighbor_km(df) -> "list[float]":
    """For each row, the distance in km to its closest *other* pincode in the dataset —
    used as the local 'how tight should a locality boundary be here' scale, since pincode
    density varies wildly between dense NCR and sparse rural areas."""
    lats = df["lat"].to_numpy(dtype=float)
    lngs = df["lng"].to_numpy(dtype=float)
    n = len(lats)
    out = [float("inf")] * n
    for i in range(n):
        best = float("inf")
        for j in range(n):
            if i == j:
                continue
            d = _haversine_km(lats[i], lngs[i], lats[j], lngs[j])
            if d < best:
                best = d
        out[i] = best if best != float("inf") else 5.0
    return out


def is_reasonable_size(geom, nn_km: float) -> bool:
    """Reject polygons that don't look like a single postal locality. Two conditions,
    whichever is tighter:
    - relative: shouldn't reach much past halfway to the nearest neighbouring pincode
      (catches dense-city mismatches, e.g. a 'suburb' polygon that's really a whole ward)
    - absolute: capped at 15km regardless of how far away the nearest neighbour is —
      without this, sparse rural pincodes let *any* size through (nn_km itself can be
      100km+), which is how city/district/state-scale Nominatim matches (e.g. an entire
      Rajasthan district returned for one rural pincode) were silently accepted as if
      they were tight locality shapes. Floor of 2.5km so isolated/rural pincodes still
      get a sensibly-sized real shape rather than being floored out by the relative term."""
    max_km = min(15.0, max(nn_km * 2, 2.5))
    return _bbox_diag_km(geom) <= max_km


def _circle_coords(lat: float, lng: float, deg: float = 0.012):
    """12-point circle as GeoJSON fallback polygon."""
    import math
    pts = []
    for i in range(13):
        a = 2 * math.pi * i / 12
        pts.append([round(lng + deg * math.cos(a), 6),
                    round(lat + deg * math.sin(a), 6)])
    return pts


def fetch_all(resume: bool, limit=None, revalidate=False) -> dict:
    df = pd.read_csv(OUT_CSV, dtype={"pincode": str})
    total = len(df)
    nn_km = nearest_neighbor_km(df)
    nn_by_pincode = {str(df.iloc[i]["pincode"]): nn_km[i] for i in range(total)}

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
    hits, misses, downgraded = 0, 0, 0
    fetched_this_run = 0

    for i, row in df.iterrows():
        pc     = str(row["pincode"])
        name   = str(row["name"])
        lat    = float(row["lat"])
        lng    = float(row["lng"])
        ppi    = int(row["ppi"])
        income = int(row["income"])
        n      = i + 1

        # Reuse cached — but if --revalidate, re-check its size against today's pincode
        # density first, since a polygon fetched when neighbours were sparser may now
        # engulf pincodes that have since been added nearby.
        if pc in existing:
            feat = existing[pc]
            feat["properties"].update(ppi=ppi, income=income, name=name)
            geom = feat.get("geometry")
            if (revalidate and not feat["properties"].get("_synthetic")
                    and geom and not is_reasonable_size(geom, nn_by_pincode[pc])):
                feat["geometry"] = {
                    "type": "Polygon",
                    "coordinates": [_circle_coords(lat, lng)],
                }
                feat["properties"]["_synthetic"] = True
                downgraded += 1
                print(f"  [{n:02d}/{total}] ▼  {pc} {name} (downgraded — oversized)")
            else:
                print(f"  [{n:02d}/{total}] ↩  {pc} {name} (cached)")
            features.append(feat)
            continue

        # --limit caps *new* fetches per run (e.g. a nightly cron budget) — pincodes
        # already cached above still get carried through untouched, so a capped run
        # never regresses coverage, it just backfills gradually across several runs.
        if limit is not None and fetched_this_run >= limit:
            # Keep the rest of the dataset's pincodes out of the output entirely
            # would silently drop their existing circle-fallback entries on a
            # later merge — instead, just stop fetching and leave them for next run
            # by not adding a feature at all (fetch_all is always run with --resume
            # in the capped/cron path, so next run picks up where this left off).
            continue

        print(f"  [{n:02d}/{total}] ↓  {pc} {name:<32}", end="", flush=True)
        time.sleep(DELAY)
        fetched_this_run += 1

        geom, osm_name = reverse_geocode(lat, lng)
        if geom and not is_reasonable_size(geom, nn_by_pincode[pc]):
            print(f" ⚠ oversized ({_bbox_diag_km(geom):.1f}km, nn={nn_by_pincode[pc]:.1f}km)", end="")
            geom = None

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

    print(f"\nDone: {hits} real polygons, {misses} fallback circles, "
          f"{downgraded} downgraded on revalidation / {total} pincodes")
    return {"type": "FeatureCollection", "features": features}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--resume",  action="store_true", help="Skip already-fetched pincodes")
    ap.add_argument("--dry-run", action="store_true", help="Fetch but don't write file")
    ap.add_argument("--limit", type=int, default=None,
                    help="Cap on *new* fetches this run (for a daily cron budget) — "
                         "always combine with --resume so later runs keep backfilling")
    ap.add_argument("--revalidate", action="store_true",
                    help="Re-check cached polygons' size against current pincode density "
                         "and downgrade any that are now oversized to a fallback circle — "
                         "no network calls, safe to run any time with --resume")
    args = ap.parse_args()

    print(f"Source: {OUT_CSV}")
    print(f"Output: {OUT_GEO}")
    print(f"Delay:  {DELAY}s between requests")
    if args.limit is not None:
        print(f"Limit:  {args.limit} new fetches this run")
    print()

    geojson = fetch_all(resume=args.resume, limit=args.limit, revalidate=args.revalidate)

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

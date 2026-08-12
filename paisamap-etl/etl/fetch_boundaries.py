"""
fetch_boundaries.py — Build data/boundaries.geojson: a real polygon for
every one of India's ~19,312 PIN codes, plus this app's own dataset.

Output is the union of two layers:

1. National base layer (ALL of India, not just pincodes this app has
   enriched) — committed, pre-simplified copy of the Dept. of Posts'
   official all-India PIN code boundary dataset (one authoritative polygon
   per PIN code office area), published on data.gov.in under NDSAP.
   Lives at
     data/reference/pincode_boundary_master/india_pincode_boundaries_simplified.geojson
   and is read on every run — no network call, no Nominatim, instant.
   These features skip is_reasonable_size() entirely: they're real
   government-drawn boundaries, not same-named-place guesses, so a
   genuinely large rural PIN code area is correct, not a mismatch bug.
   Shipping full national coverage (not just this app's own pincodes)
   means ANY pincode a user later enriches on-demand (see
   enrichPincodeFlow() in index.html) already has its real boundary
   ready client-side the instant it's enriched — no separate fetch, no
   fallback circle.

   Regenerating this base layer (source updates ~twice a year): download
   the raw 90MB master from
     https://drive.google.com/drive/folders/1IEZgX6wf1pwjRxPIDHdEtdw1496-QoAs
     → Pincodes/All_India_pincode_Boundary-19312.geojson (file id
     1GG8HbFrO3pgGEB_1KvKbPN3IZmQAlra4 as of 2026-08 — "anyone with link"
     folder, plain curl works, no confirm-token dance despite the size)
   into data/reference/pincode_boundary_master/ (gitignored — NOT the
   simplified sibling file, which IS committed), then simplify it:
     npx mapshaper All_India_pincode_Boundary-19312.geojson \\
       -simplify 10% -filter-fields Pincode \\
       -o precision=0.00001 format=geojson out.geojson
   then rename the Pincode property to lowercase pincode and add
   {"_source": "govt"} to each feature's properties before overwriting
   india_pincode_boundaries_simplified.geojson.

   NOT Esri's re-host of the same underlying data (arcgis.com item
   7fe4eec592004f5f992ed7492a50b18d) — that one requires an ArcGIS org/dev
   login AND its license explicitly forbids exporting for offline use,
   which is exactly what shipping a static file does, so it's off-limits
   regardless of credentials, even a paid one.

2. This app's own dataset, for whatever the national layer above doesn't
   cover (~9% of real pincodes, plus this app's synthetic "D..." demo
   pincodes which will never appear in a real geo dataset): Nominatim
   REVERSE geocode (one call per pincode) at zoom=14, falling back to
   zoom=12, matching what fetchLocalityBoundary() does in the browser —
   DOES go through is_reasonable_size(), since it's an approximate
   named-place match rather than an authoritative polygon. Falls back
   further to a plain circle if even that misses.

Run once after any significant dataset expansion:
  cd paisamap-etl
  python3 etl/fetch_boundaries.py

~6 minutes for ~40 pincodes needing the Nominatim fallback (5s between
calls, Nominatim ToS) — the national base layer and any already-cached
fallback entries are instant, no network call.
Pass --resume to skip pincodes already in existing boundaries.geojson.
"""

import argparse
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

ROOT       = Path(__file__).resolve().parents[1]
APP        = ROOT.parent
OUT_CSV    = APP / "data" / "output" / "ppi_map_data.csv"
OUT_GEO    = APP / "data" / "boundaries.geojson"
NATIONAL_GEO = ROOT / "data" / "reference" / "pincode_boundary_master" / "india_pincode_boundaries_simplified.geojson"

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


def _geom_centroid(geom):
    """Plain average of every vertex in a Polygon/MultiPolygon — (lng, lat). Not
    area-weighted, but good enough to sanity-check 'is this polygon anywhere near where
    it's supposed to be', which only needs the right ballpark, not a precise centroid."""
    def flatten(coords):
        if isinstance(coords[0], (int, float)):
            yield coords
        else:
            for c in coords:
                yield from flatten(c)
    pts = list(flatten(geom["coordinates"]))
    lngs = [p[0] for p in pts]
    lats = [p[1] for p in pts]
    return sum(lngs) / len(lngs), sum(lats) / len(lats)


def _valid_latlng_pair(latlng) -> bool:
    lat, lng = latlng
    return (lat == lat) and (lng == lng)  # NaN != NaN is the cheap isnan check


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


def _ring_centroid_and_area(ring):
    """Planar shoelace centroid + area for a single ring — good enough for relative
    part-size/distance comparisons at this scale, no geometry library needed."""
    n = len(ring)
    if n < 3:
        xs = [p[0] for p in ring]; ys = [p[1] for p in ring]
        return (sum(xs) / n, sum(ys) / n), 0.0
    a = cx = cy = 0.0
    for i in range(n - 1):
        x0, y0 = ring[i]; x1, y1 = ring[i + 1]
        cross = x0 * y1 - x1 * y0
        a += cross; cx += (x0 + x1) * cross; cy += (y0 + y1) * cross
    a *= 0.5
    if abs(a) < 1e-12:
        xs = [p[0] for p in ring]; ys = [p[1] for p in ring]
        return (sum(xs) / len(xs), sum(ys) / len(ys)), 0.0
    return (cx / (6 * a), cy / (6 * a)), abs(a)


def drop_disconnected_multipolygon_parts(geom: dict, anchor=None) -> dict:
    """A single postal pincode's official boundary should be one contiguous shape (or a
    few genuinely-adjacent parts) — not a part sitting many km away, sometimes in open
    water. Found live 2026-08-12 (a user spotted choropleth shapes bleeding into the
    Arabian Sea off Mumbai): 304/342 MultiPolygon features in the national layer have a
    part whose centroid is >5km from another part's, one case 168km apart. Cuffe Parade
    (400021) is the concrete example that surfaced this — its third ring sits in the
    middle of Mumbai Harbour/Thane Creek, nowhere near the peninsula tip its other two
    parts describe. This is a defect in the source national layer (likely from how the
    original shapefile's multi-ring features got joined to pincodes), not something the
    mapshaper simplification step introduced.

    First cut of this picked the largest-by-area part as "the real one" and dropped
    anything far from it — wrong: Cuffe Parade's stray harbour fragment is a ~13km-wide
    kite shape, bigger in raw area than its two legitimate tiny peninsula-tip parts
    combined, so that heuristic kept the bad part and threw away the real locality
    entirely. Area is not a reliable signal for which part is correct; proximity to a
    trusted reference point is.

    `anchor` is this pincode's own known (lat, lng) — e.g. wherever it was geocoded/
    enriched — when available (this app's own ~15,551-pincode dataset has one for every
    entry). Parts are kept if within the more generous of 8km or 4x the closest part's
    own bounding diagonal from that anchor. Without a trusted anchor (national-layer-only
    pincodes outside this app's active dataset), falls back to using the *smallest*
    part's own centroid as the reference instead of the largest — a legitimate dense
    locality polygon is reliably tighter than the open-water/long-distance stray
    fragments actually observed, so the smallest part is the safer bet for "real shape,"
    even though it's not as reliable as a true anchor."""
    if geom.get("type") != "MultiPolygon" or len(geom.get("coordinates", [])) < 2:
        return geom
    parts = geom["coordinates"]
    infos = []
    for poly in parts:
        centroid, _area = _ring_centroid_and_area(poly[0])
        diag = _bbox_diag_km({"type": "Polygon", "coordinates": [poly[0]]})
        infos.append({"centroid": centroid, "diag": diag, "poly": poly})

    # A malformed anchor (NaN lat/lng from a bad source row) must never propagate — it
    # would poison every distance below (NaN comparisons are always False, so the
    # keep-filter would silently drop every single part, including the correct one).
    if anchor is not None and _valid_latlng_pair(anchor):
        ref_lat, ref_lng = anchor
    else:
        smallest = min(infos, key=lambda x: x["diag"])
        ref_lng, ref_lat = smallest["centroid"]

    dists = sorted(
        (_haversine_km(ref_lat, ref_lng, info["centroid"][1], info["centroid"][0]), info)
        for info in infos
    )
    closest_diag = dists[0][1]["diag"]
    max_km = max(8.0, closest_diag * 4)
    kept = [info["poly"] for d, info in dists if d <= max_km]

    # Belt-and-braces: never return a geometry with zero parts. Should be unreachable
    # (the closest part is always within max_km of itself), but a bad shape here would
    # otherwise crash the whole run over a single pincode.
    if not kept:
        return geom
    if len(kept) == len(parts):
        return geom
    if len(kept) > 1:
        return {"type": "MultiPolygon", "coordinates": kept}
    return {"type": "Polygon", "coordinates": kept[0]}


def _circle_coords(lat: float, lng: float, deg: float = 0.012):
    """12-point circle as GeoJSON fallback polygon."""
    import math
    pts = []
    for i in range(13):
        a = 2 * math.pi * i / 12
        pts.append([round(lng + deg * math.cos(a), 6),
                    round(lat + deg * math.sin(a), 6)])
    return pts


def load_national_layer() -> dict:
    """Pincode -> ready-to-use Feature, from the committed national base layer.
    Returns {} (not an error, just no national coverage) if that file is somehow
    missing — callers fall through to the existing Nominatim/circle path."""
    if not NATIONAL_GEO.exists():
        print(f"(national boundary layer not found at {NATIONAL_GEO} — skipping, "
              f"see this script's docstring to regenerate it)")
        return {}
    print(f"Loading national boundary layer ({NATIONAL_GEO.stat().st_size // 1_000_000}MB)…", end="", flush=True)
    with open(NATIONAL_GEO) as f:
        national = json.load(f)
    by_pincode = {}
    for feat in national["features"]:
        pc = str(feat.get("properties", {}).get("pincode", "")).strip()
        if pc:
            by_pincode[pc] = feat
    print(f" {len(by_pincode)} pincodes")
    return by_pincode


def fetch_all(resume: bool, limit=None, revalidate=False) -> dict:
    df = pd.read_csv(OUT_CSV, dtype={"pincode": str})
    total = len(df)
    nn_km = nearest_neighbor_km(df)
    nn_by_pincode = {str(df.iloc[i]["pincode"]): nn_km[i] for i in range(total)}
    anchor_by_pincode = {str(df.iloc[i]["pincode"]): (float(df.iloc[i]["lat"]), float(df.iloc[i]["lng"]))
                          for i in range(total)}
    national = load_national_layer()

    existing: dict[str, dict] = {}
    if resume and OUT_GEO.exists():
        with open(OUT_GEO) as f:
            old = json.load(f)
        for feat in old.get("features", []):
            pc = feat.get("properties", {}).get("pincode", "")
            if pc:
                existing[pc] = feat
        print(f"Resuming: {len(existing)}/{total} already done")

    # Gap-fill features for this app's own dataset only — anything the national
    # layer already covers is skipped here and pulled in from `national` at the
    # end instead, so every run always ships full national coverage regardless
    # of --resume/--limit state (a capped or resumed run can never regress it).
    features = []
    hits, misses, downgraded, from_national = 0, 0, 0, 0
    fetched_this_run = 0

    for i, row in df.iterrows():
        pc     = str(row["pincode"])
        name   = str(row["name"])
        lat    = float(row["lat"])
        lng    = float(row["lng"])
        ppi    = int(row["ppi"])
        income = int(row["income"])
        n      = i + 1

        if pc in national:
            from_national += 1
            print(f"  [{n:02d}/{total}] ★  {pc} {name} (national layer)")
            continue

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

    print(f"\nDone: {from_national} from national layer, {hits} real Nominatim polygons, "
          f"{misses} fallback circles, {downgraded} downgraded on revalidation / {total} pincodes")

    # Union: full national layer (all ~19,312 pincodes, not just this app's own
    # dataset) + this run's gap-fill features for anything national doesn't cover.
    # National entries always win on key collision — see docstring on why an
    # authoritative govt polygon is never second-guessed by a cached Nominatim one.
    all_features = list(national.values())
    covered = set(national.keys())
    for feat in features:
        pc = feat.get("properties", {}).get("pincode", "")
        if pc not in covered:
            all_features.append(feat)

    n_cleaned = 0
    n_rejected = 0
    for feat in all_features:
        geom = feat.get("geometry")
        if geom is None:
            continue
        pc = feat.get("properties", {}).get("pincode", "")
        anchor = anchor_by_pincode.get(pc)
        cleaned = drop_disconnected_multipolygon_parts(geom, anchor)
        if cleaned is not geom:
            feat["geometry"] = geom = cleaned
            n_cleaned += 1

        # A rarer, harder-to-fix defect than the disconnected-fragment case above: the
        # whole polygon (every remaining part) is assigned to the wrong pincode entirely,
        # not just missing one stray piece. Found live 2026-08-12: 832303 "Ghatsila"
        # (Jharkhand, anchor 23.51,85.37) resolves to a govt polygon whose *closest* part
        # is still ~129km away — dropping-the-far-part logic can't help when there's no
        # part that's actually right. Only checkable where a trusted anchor exists (this
        # app's own dataset); reject the polygon entirely rather than ship a confidently-
        # wrong shape — geometry:null here makes the frontend fall through to its existing
        # circle-fallback/live-lookup path (same as any other boundary miss), same safe
        # default this codebase already uses for a bad Nominatim match.
        if anchor is not None and _valid_latlng_pair(anchor):
            clng, clat = _geom_centroid(geom)
            if _haversine_km(anchor[0], anchor[1], clat, clng) > 30:
                feat["geometry"] = None
                n_rejected += 1
    if n_cleaned:
        print(f"Cleaned {n_cleaned} feature(s) with a disconnected outlier MultiPolygon part")
    if n_rejected:
        print(f"Rejected {n_rejected} feature(s) entirely mismatched to their pincode (no part near the anchor)")

    print(f"Total output: {len(national)} national + {len(all_features) - len(national)} "
          f"dataset-specific fallback = {len(all_features)} features")
    return {"type": "FeatureCollection", "features": all_features}


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

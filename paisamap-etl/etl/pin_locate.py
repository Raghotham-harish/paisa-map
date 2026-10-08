"""
pin_locate.py — lat/lng → Indian PIN code from Dept. of Posts polygons.

Reverse geocoding used to call public Nominatim. That breached the Nominatim
usage policy, pulled in ODbL data, and failed often. This module answers the
same question locally: point-in-polygon against the DoP PIN boundaries
published on data.gov.in under NDSAP (GODL-India). The file is the committed
simplified copy at

    data/reference/pincode_boundary_master/india_pincode_boundaries_simplified.geojson

District, state, and office name come from the India Post reference CSVs
(`pincode_district_state_india.csv`, `pincode_office_names.csv`), the same
files `pincode_master` already treats as source of truth.

The spatial index is built once per process. A point in a gap or just
offshore snaps to the nearest polygon within SNAP_TOLERANCE_M; anything
farther (open sea, outside India) is a miss. A miss is None — this module
never mints a pincode.
"""

from __future__ import annotations

import csv
import json
import math
import threading
import time
from typing import Iterator

from shapely import STRtree, make_valid
from shapely.geometry import Point, shape
from shapely.ops import nearest_points

from pincode_master import REFERENCE_CSV, ROOT, real_pincode_id

GEOJSON = (
    ROOT / "data" / "reference" / "pincode_boundary_master"
    / "india_pincode_boundaries_simplified.geojson"
)
OFFICE_CSV = ROOT / "data" / "reference" / "pincode_office_names.csv"

# 10% mapshaper simplification leaves slivers between neighbours and along
# the coast. 1 km is enough to close those gaps and still reject a point
# that is genuinely at sea (those are tens of kilometres from any polygon).
SNAP_TOLERANCE_M = 1000.0
_M_PER_DEG = 111_320.0

_lock = threading.Lock()
_ready = False
_geoms: list = []
_pincodes: list[str] = []
_tree: STRtree | None = None
# pincode → (district, state_name, state_code)
_admin: dict[str, tuple[str, str, str]] = {}
# pincode → office name
_office: dict[str, str] = {}


def _as_polygonal(geom):
    """Drop lines and points that make_valid sometimes emits."""
    if geom is None or geom.is_empty:
        return None
    kind = geom.geom_type
    if kind in ("Polygon", "MultiPolygon"):
        return geom
    if kind == "GeometryCollection":
        parts = [g for g in geom.geoms if g.geom_type in ("Polygon", "MultiPolygon") and not g.is_empty]
        if not parts:
            return None
        if len(parts) == 1:
            return parts[0]
        from shapely import unary_union
        return unary_union(parts)
    return None


def _load_admin() -> None:
    _admin.clear()
    with open(REFERENCE_CSV, newline="") as f:
        for row in csv.DictReader(f):
            pc = real_pincode_id(row.get("pincode"))
            if not pc:
                continue
            _admin[pc] = (
                (row.get("district") or "").strip(),
                (row.get("state_name") or "").strip(),
                (row.get("state_code") or "").strip(),
            )
    _office.clear()
    with open(OFFICE_CSV, newline="") as f:
        for row in csv.DictReader(f):
            pc = real_pincode_id(row.get("pincode"))
            if not pc:
                continue
            name = (row.get("name") or "").strip()
            if name and pc not in _office:
                _office[pc] = name
            # Office file also carries a district. Use it only when the
            # India Post district file has no row for this pincode.
            if pc not in _admin:
                _admin[pc] = (
                    (row.get("district") or "").strip(),
                    (row.get("state") or "").strip().title(),
                    "",
                )


def _load() -> None:
    global _tree
    _load_admin()
    t0 = time.perf_counter()
    with open(GEOJSON) as f:
        collection = json.load(f)
    geoms = []
    pins = []
    for feature in collection.get("features") or []:
        raw = feature.get("geometry")
        props = feature.get("properties") or {}
        pc = real_pincode_id(props.get("pincode"))
        if not raw or not pc:
            continue
        geom = _as_polygonal(shape(raw))
        if geom is None:
            continue
        if not geom.is_valid:
            geom = _as_polygonal(make_valid(geom))
        if geom is None or geom.is_empty:
            continue
        geoms.append(geom)
        pins.append(pc)
    if not geoms:
        raise RuntimeError(f"no PIN polygons loaded from {GEOJSON}")
    _geoms[:] = geoms
    _pincodes[:] = pins
    _tree = STRtree(geoms)
    elapsed = time.perf_counter() - t0
    print(f"[pin_locate] loaded {len(geoms)} DoP PIN polygons in {elapsed:.2f}s", flush=True)


def warm() -> None:
    """Build the spatial index. Safe to call more than once."""
    global _ready
    if _ready:
        return
    with _lock:
        if _ready:
            return
        _load()
        _ready = True


def _distance_m(geom, point: Point) -> float:
    """Metres from point to geom. 0 when the point is inside or on the boundary.

    Degree-space Euclidean distance overstates east-west gaps, so the snap
    decision uses the nearest boundary vertex pair in a local metres frame.
    """
    if geom.covers(point):
        return 0.0
    here, there = nearest_points(point, geom)
    lat = point.y
    m_lat = (there.y - here.y) * _M_PER_DEG
    m_lng = (there.x - here.x) * _M_PER_DEG * math.cos(math.radians(lat))
    return math.hypot(m_lat, m_lng)


def _record(index: int, *, snapped: bool, distance_m: float) -> dict:
    pc = _pincodes[index]
    district, state_name, state_code = _admin.get(pc, ("", "", ""))
    return {
        "pincode": pc,
        "office_name": _office.get(pc, ""),
        "district": district,
        "state_name": state_name,
        "state_code": state_code,
        "snapped": snapped,
        "distance_m": round(distance_m, 1),
    }


def locate(lat: float, lng: float) -> dict | None:
    """Return the PIN under this point, or the nearest one within the snap
    tolerance. None when nothing is close enough. Never invents an id."""
    warm()
    point = Point(float(lng), float(lat))
    assert _tree is not None
    covered = _tree.query(point, predicate="covered_by")
    if len(covered):
        # Overlapping simplified polygons are rare. The smaller area is the
        # tighter office boundary when two do contain the same point.
        index = min(covered, key=lambda i: _geoms[int(i)].area)
        return _record(int(index), snapped=False, distance_m=0.0)
    nearest = int(_tree.nearest(point))
    distance_m = _distance_m(_geoms[nearest], point)
    if distance_m <= SNAP_TOLERANCE_M:
        return _record(nearest, snapped=True, distance_m=distance_m)
    return None


def reverse_address(lat: float, lng: float) -> dict:
    """Payload shaped like a Nominatim reverse result.

    `index.html` reads `address.postcode` and the first of
    `suburb / neighbourhood / village / city_district / town`. The office
    name goes in `suburb` so those call sites stay as they are.
    """
    hit = locate(lat, lng)
    body = {"lat": str(lat), "lon": str(lng), "address": {}}
    if not hit:
        return body
    name = hit["office_name"] or hit["district"] or hit["pincode"]
    parts = [name, hit["district"], hit["state_name"], hit["pincode"], "India"]
    body["display_name"] = ", ".join(p for p in parts if p)
    body["address"] = {
        "postcode": hit["pincode"],
        "suburb": name,
        "state_district": hit["district"],
        "state": hit["state_name"],
        "country": "India",
        "country_code": "in",
    }
    return body


def iter_reference_points() -> Iterator[tuple[str, float, float]]:
    """(pincode, lat, lng) for a point guaranteed to lie in each loaded polygon."""
    warm()
    for pc, geom in zip(_pincodes, _geoms):
        rp = geom.representative_point()
        yield pc, float(rp.y), float(rp.x)

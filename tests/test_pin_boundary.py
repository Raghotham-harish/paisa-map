"""
test_pin_boundary.py — PR #10 follow-up. A PIN's boundary comes from its own DoP polygon via
/api/boundary, and the map page makes no direct Nominatim / Overpass calls.

    python3 tests/test_pin_boundary.py

1. Every scored PIN with no DoP polygon (74 on 5 Oct, 72 on 15 Oct) gets a blank boundary
   (404, geometry null) — never a neighbour's shape.
2. A PIN with a polygon gets its own shape, a point inside it, and its office name.
3. index.html contains no "nominatim" / "overpass" (case-insensitive) and no fetch to an
   OSM geocoding host; the made-up "live area data" and the Overpass "nearby hubs" are gone.
No network: outbound connections raise.
"""

import csv
import json
import os
import re
import socket
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "paisamap-etl" / "etl"))

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/pin_boundary.sqlite")
if "sqlite" not in os.environ["DATABASE_URL"]:
    raise SystemExit("Refusing to run: DATABASE_URL doesn't look like a throwaway sqlite file.")
if not os.environ.get("CUSTOMER_DATA_KEY"):
    from cryptography.fernet import Fernet
    os.environ["CUSTOMER_DATA_KEY"] = Fernet.generate_key().decode()

requested = []


def _blocked(*a, **k):
    requested.append(str(a[0]) if a else "?")
    raise OSError("network blocked in test")


urllib.request.urlopen = _blocked
socket.create_connection = _blocked

from shapely.geometry import Point, shape  # noqa: E402
import pin_locate  # noqa: E402
import server  # noqa: E402

passed = 0


def check(cond, msg):
    global passed
    assert cond, f"FAIL: {msg}"
    passed += 1


client = server.app.test_client()

# ── 1. scored PINs without a DoP polygon get a blank boundary ────────────────
with open(pin_locate.GEOJSON) as f:
    dop = {str((ft.get("properties") or {}).get("pincode")) for ft in json.load(f)["features"]
           if ft.get("geometry")}
with open(REPO / "paisamap-etl" / "data" / "output" / "ppi_ml_refined.csv", newline="") as f:
    scored = [r for r in csv.DictReader(f) if re.fullmatch(r"[1-9]\d{5}", r["pincode"] or "")]
no_poly = [r for r in scored if r["pincode"] not in dop]
check(0 < len(no_poly) <= 74, f"no-polygon scored PINs: {len(no_poly)} (74 on 5 Oct)")
for r in no_poly:
    pc = r["pincode"]
    check(pin_locate.boundary(pc) is None, f"{pc}: boundary() is None")
    resp = client.get(f"/api/boundary?pincode={pc}")
    body = resp.get_json()
    check(resp.status_code == 404 and body["geometry"] is None and body["pincode"] == pc,
          f"{pc}: /api/boundary is blank (404, geometry null), not a neighbour's polygon")
# Sanity on why "blank" matters: the point lookup at these PINs lands in a neighbour.
pc0 = next(r for r in no_poly if r["pincode"] == "560052") if any(r["pincode"] == "560052" for r in no_poly) else no_poly[0]
hit = pin_locate.locate(float(pc0["lat"]), float(pc0["lng"]))
check(hit is None or hit["pincode"] != pc0["pincode"],
      "(reverse at such a PIN's point returns a different PIN, so the outline must not come from it)")

# ── 2. a PIN with a polygon gets its own shape ───────────────────────────────
for pc, office_word in [("560038", "Indiranagar"), ("400006", "Malabar"), ("110003", "Lodi")]:
    resp = client.get(f"/api/boundary?pincode={pc}")
    b = resp.get_json()
    check(resp.status_code == 200 and b["pincode"] == pc, f"{pc}: 200")
    geom = shape(b["geometry"])
    check(b["geometry"]["type"] in ("Polygon", "MultiPolygon"), f"{pc}: polygonal")
    check(geom.covers(Point(b["lng"], b["lat"])), f"{pc}: returned point lies inside the polygon")
    check(pin_locate.locate(b["lat"], b["lng"])["pincode"] == pc, f"{pc}: reverse at that point is {pc}")
    check(office_word.lower() in b["office_name"].lower(), f"{pc}: office name {b['office_name']!r}")
    check("GODL" in b["source"], f"{pc}: licence named in source")
for bad in ("", "abc", "05600", "0560038"):
    check(client.get(f"/api/boundary?pincode={bad}").status_code == 400, f"bad pincode {bad!r} -> 400")
check(client.get("/api/boundary?pincode=999999").status_code == 404, "unknown PIN -> 404")

# ── 3. the map page makes no direct OSM geocoding / Overpass calls ───────────
html = (REPO / "index.html").read_text()
hits = [ln for ln in html.splitlines() if re.search(r"nominatim|overpass", ln, re.I)]
check(not hits, f"index.html mentions nominatim/overpass on {len(hits)} line(s): {hits[:3]}")
check("nominatim.openstreetmap.org" not in html and "overpass-api" not in html, "no OSM geocoder hosts")
check("loadLiveAreaData" not in html and "liveData" not in html,
      "made-up 'live area data' (random incomes around a POI count) is gone")
check('id="nearbyBtn"' not in html, "Overpass 'nearby hubs' button is gone")
check("/api/boundary?pincode=" in html, "map asks the server for a PIN's own boundary")

check(not requested, f"no outbound connection attempted: {requested}")
print(f"test_pin_boundary: {passed} checks passed")

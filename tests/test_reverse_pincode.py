"""
test_reverse_pincode.py — issue #9. lat/lng → PIN comes from the DoP
polygons, not Nominatim.

    python3 tests/test_reverse_pincode.py

Checks the response fields index.html already reads (address.postcode and
address.suburb), that /api/reverse makes no outbound connection, and that
the batch reverse step does not sleep or call the network.
"""

import csv
import os
import socket
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "paisamap-etl" / "etl"))

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/reverse_pincode.sqlite")
if "sqlite" not in os.environ["DATABASE_URL"]:
    raise SystemExit("Refusing to run: DATABASE_URL doesn't look like a throwaway sqlite file.")
if not os.environ.get("CUSTOMER_DATA_KEY"):
    from cryptography.fernet import Fernet
    os.environ["CUSTOMER_DATA_KEY"] = Fernet.generate_key().decode()

# Block outbound HTTP before the app imports. Polygon load is local.
requested = []


def _blocked_urlopen(req, *a, **k):
    requested.append(getattr(req, "full_url", str(req)))
    raise OSError("network blocked in test")


urllib.request.urlopen = _blocked_urlopen
_real_create_connection = socket.create_connection


def _blocked_create_connection(*a, **k):
    requested.append(f"connect:{a[0] if a else a}")
    raise OSError("network blocked in test")


socket.create_connection = _blocked_create_connection

import pin_locate  # noqa: E402
import enrich_single  # noqa: E402
import batch_enrich_hces as batch  # noqa: E402
import server  # noqa: E402

passed = 0


def check(cond, msg):
    global passed
    assert cond, f"FAIL: {msg}"
    passed += 1
    print(f"  ok  {msg}")


# Click-like coordinates checked against the polygons before this test was
# written. NE is Shillong; the island is Androth (Lakshadweep). A second
# island point, the Port Blair office polygon, is included so a mainland
# coast can't satisfy the island case by accident.
FIXTURES = [
    (12.9784, 77.6408, "560038", "Indiranagar", "Bangalore", "Karnataka"),
    (18.9550, 72.7980, "400006", "Malabar Hill", "Mumbai", "Maharashtra"),
    (28.5910, 77.2190, "110003", "Lodi Road", "South Delhi", "Delhi"),
    (25.5788, 91.8933, "793001", "Shillong G.P.O.", "East Khasi Hills", "Meghalaya"),
    (10.8167, 73.6833, "682551", "Androth", "Lakshadweep", "Lakshadweep"),
    (11.66873, 92.74036, "744101", "Port Blair", "South Andaman", "Andaman & Nicobar Islands"),
]


def _ui_name(address, pc):
    """Same fallback chain the four /api/reverse call sites in index.html use.
    They differ by a field or two; suburb is first in every one of them."""
    address = address or {}
    return (address.get("suburb") or address.get("neighbourhood") or address.get("village")
            or address.get("city_district") or address.get("town") or address.get("city") or pc)


app = server.app
app.testing = True
client = app.test_client()

for lat, lng, pc, office, district, state in FIXTURES:
    hit = pin_locate.locate(lat, lng)
    check(hit is not None and hit["pincode"] == pc, f"locate {lat},{lng} → {pc} (got {hit})")
    check(hit["office_name"] == office and hit["district"] == district and hit["state_name"] == state,
          f"{pc} office/district/state from the reference CSVs")
    check(hit["pincode"].isdigit() and not hit["pincode"].startswith("D"), f"{pc} is not a synthetic id")

    asked = len(requested)
    res = client.get("/api/reverse", query_string={"lat": lat, "lng": lng})
    check(res.status_code == 200, f"/api/reverse {pc} status {res.status_code}")
    body = res.get_json()
    address = body.get("address") or {}
    check(address.get("postcode") == pc, f"/api/reverse postcode {pc}")
    check(address.get("suburb") == office, f"/api/reverse suburb {office}")
    check(_ui_name(address, "") == office, f"index.html name chain yields {office}")
    check(res.headers.get("Access-Control-Allow-Origin") == "*", "reverse keeps the CORS header")
    check(len(requested) == asked, f"/api/reverse {pc} made no outbound request ({requested[asked:]})")

# Sea, a gap farther than the snap tolerance, and a nonsense ocean point.
for lat, lng, label in [(12.0, 70.0, "Arabian Sea"), (15.0, 88.5, "Bay of Bengal"), (0.0, 0.0, "null island")]:
    hit = pin_locate.locate(lat, lng)
    check(hit is None, f"{label} is not given a pincode")
    res = client.get("/api/reverse", query_string={"lat": str(lat), "lng": str(lng)})
    body = res.get_json()
    postcode = (body.get("address") or {}).get("postcode") or ""
    check(res.status_code == 200 and postcode == "", f"/api/reverse {label} returns an empty postcode")
    check(not str(postcode).startswith("D"), f"{label} did not mint a synthetic id")

res = client.get("/api/reverse")
check(res.status_code == 400, "missing lat/lng is 400")
res = client.get("/api/reverse", query_string={"lat": "nope", "lng": "77"})
check(res.status_code == 400, "non-numeric lat/lng is 400")
check(not requested, f"no outbound request across the reverse checks ({requested})")

# Points just outside a polygon, still within 1 km, snap back to it.
# Neighbours in a city share edges, so the snap is easiest to see on a
# coastal / rural boundary (measured once against this polygon file).
for lat, lng, pc, label in (
    (11.67749, 92.74271, "744101", "Port Blair, just north of the polygon"),
    (27.48148, 83.44678, "273164", "Nautanwa Bazar, just outside the polygon"),
):
    hit = pin_locate.locate(lat, lng)
    check(hit is not None and hit["snapped"] and hit["pincode"] == pc
          and 0 < hit["distance_m"] <= pin_locate.SNAP_TOLERANCE_M,
          f"{label} snaps to {pc} ({hit})")

# ~13 km east of Androth is open water: outside the snap tolerance.
far_island = pin_locate.locate(10.8167, 73.80)
check(far_island is None, f"open water east of Androth is not given a pincode ({far_island})")

# Every loaded polygon's own interior point resolves to that pincode.
mismatches = []
checked_polys = 0
interior = {}
for pc, lat, lng in pin_locate.iter_reference_points():
    checked_polys += 1
    interior[pc] = (lat, lng)
    hit = pin_locate.locate(lat, lng)
    if not hit or hit["pincode"] != pc:
        mismatches.append((pc, None if not hit else hit["pincode"]))
        if len(mismatches) >= 5:
            break
check(not mismatches, f"all {checked_polys} polygon interiors resolve to themselves (e.g. {mismatches})")

# ETL reverse step: same helper, no network, no sleep.
slept = []
batch.time.sleep = lambda seconds: slept.append(seconds)
batch.urllib.request.urlopen = _blocked_urlopen
before = len(requested)
pc = batch.reverse_pincode(12.9784, 77.6408)
check(pc == "560038", f"batch reverse_pincode → 560038 (got {pc})")
check(slept == [], "batch reverse_pincode does not sleep")
check(len(requested) == before, "batch reverse_pincode makes no outbound request")
check(batch.reverse_pincode(12.0, 70.0) == "", "batch reverse of open sea is empty, not a synthetic id")
district, locality = enrich_single.detect_district(12.9784, 77.6408)
check((district, locality) == ("Bangalore", "Indiranagar"),
      f"detect_district → Bangalore / Indiranagar (got {district!r} / {locality!r})")
check(enrich_single.detect_district(12.0, 70.0) == ("", ""), "detect_district misses the sea")

# p95 of /api/reverse, in-process, after the index is warm.
samples = []
for pc, lat, lng in pin_locate.iter_reference_points():
    samples.append((lat, lng))
    if len(samples) >= 80:
        break
for lat, lng in samples[:15]:
    client.get("/api/reverse", query_string={"lat": lat, "lng": lng})
times = []
for lat, lng in samples:
    t0 = time.perf_counter()
    res = client.get("/api/reverse", query_string={"lat": f"{lat:.6f}", "lng": f"{lng:.6f}"})
    times.append((time.perf_counter() - t0) * 1000)
    if res.status_code != 200:
        check(False, f"latency sample status {res.status_code}")
        break
times.sort()
p95 = times[int(len(times) * 0.95)]
print(f"  /api/reverse test-client p95 {p95:.2f} ms over {len(times)} calls "
      f"(p50 {times[len(times)//2]:.2f} ms, max {times[-1]:.2f} ms)")
check(p95 < 50, f"p95 latency under 50 ms (got {p95:.2f} ms)")

# Re-measure the batch log. geocode_fail rows have no lat/lng: that status is
# the Nominatim forward search, which this change does not replace. The
# reverse step is what we can re-score, on every coordinate the log does have.
log_path = REPO / "paisamap-etl" / "data" / "output" / "batch_enrich_log.csv"
rows = list(csv.DictReader(open(log_path)))
n = len(rows)
n_fail = sum(1 for r in rows if r["status"] == "geocode_fail")
n_ok = sum(1 for r in rows if r["status"] == "ok")
ok_rows = [r for r in rows if r["status"] == "ok" and r.get("lat") and r.get("lng")]
reverse_miss = 0
reverse_disagree = 0
for r in ok_rows:
    hit = pin_locate.locate(float(r["lat"]), float(r["lng"]))
    got = hit["pincode"] if hit else ""
    if not got:
        reverse_miss += 1
    elif r.get("pincode") and got != r["pincode"]:
        reverse_disagree += 1
synthetic = sum(1 for r in ok_rows if str(r.get("pincode") or "").startswith("D"))
miss_rate = (100.0 * reverse_miss / len(ok_rows)) if ok_rows else 0.0
fail_rate = 100.0 * n_fail / n if n else 0.0
print(f"  batch log geocode_fail rate: {n_fail}/{n} ({fail_rate:.1f}%), {n_ok} ok")
print(f"  reverse remeasure on {len(ok_rows)} logged coordinates: "
      f"{reverse_miss} miss ({miss_rate:.1f}%), "
      f"{reverse_disagree} differ from the logged forward postcode, "
      f"{synthetic} logged ok ids are synthetic D######")
check(miss_rate < 5, f"reverse miss rate on logged coordinates under 5% (got {miss_rate:.1f}%)")

# A fixed sample of previously failed districts. They have no coordinate, so
# the reverse step is not what failed them. Place each one on a reference
# pincode in the postal→HCES crosswalk (exact / verified_manual only) and
# score the reverse step there.
cw_path = REPO / "paisamap-etl" / "data" / "reference" / "postal_to_hces_district_crosswalk.csv"
by_hces = {}
for r in csv.DictReader(open(cw_path)):
    if r["match_method"] not in ("exact", "verified_manual"):
        continue
    by_hces.setdefault((r["state"].upper(), r["hces_district"].upper()), r["postal_district"])
admin_by_district = {}
for r in csv.DictReader(open(pin_locate.REFERENCE_CSV)):
    admin_by_district.setdefault((r["state_name"].upper(), r["district"].upper()), r["pincode"])

STATE_ALIAS = {
    "CHHATTISGARH": "CHHATTISGARH",
    "ANDAMAN &NICOBAR": "ANDAMAN & NICOBAR ISLANDS",
    "NCT DELHI": "DELHI",
}


def _norm_state(state):
    state = " ".join(state.upper().split())
    return STATE_ALIAS.get(state, state)


failed = []
seen = set()
for r in rows:
    if r["status"] != "geocode_fail":
        continue
    key = (r["state"], r["district"])
    if key in seen:
        continue
    seen.add(key)
    failed.append(r)
# 20 distinct districts account for every geocode_fail row (the cron retried
# them). Score the whole set, not a draw.
sample = failed
placed = resolved = 0
for r in sample:
    postal = by_hces.get((_norm_state(r["state"]), r["district"].upper().replace(" DISTRICT", "").strip()))
    if not postal:
        postal = by_hces.get((_norm_state(r["state"]), r["district"].upper().strip()))
    if not postal:
        continue
    pc = admin_by_district.get((_norm_state(r["state"]), postal.upper()))
    # state names in the district file are title case; the crosswalk state is upper.
    if not pc:
        for (st, dist), pin in admin_by_district.items():
            if dist == postal.upper() and st.replace("CHATTISGARH", "CHHATTISGARH") == _norm_state(r["state"]):
                pc = pin
                break
    if not pc or pc not in interior:
        continue
    placed += 1
    lat, lng = interior[pc]
    hit = pin_locate.locate(lat, lng)
    if hit and hit["pincode"] == pc:
        resolved += 1
print(f"  geocode_fail districts: {len(sample)} distinct "
      f"({n_fail} log rows, none stored a lat/lng). "
      f"{placed} match an exact postal crosswalk point; reverse resolves {resolved}/{placed}. "
      f"The other {len(sample) - placed} still fail upstream, in forward search, before reverse runs.")
check(placed > 0 and resolved == placed,
      f"every crosswalk-placed geocode_fail district reverses to a real PIN ({resolved}/{placed})")

import _ops
check(_ops._group_for("/api/reverse") == "default", "/api/reverse is not on the Nominatim rate-limit group")
check(_ops._group_for("/api/search") == "geo", "/api/search stays on the Nominatim rate-limit group")

print(f"test_reverse_pincode: {passed} checks passed")

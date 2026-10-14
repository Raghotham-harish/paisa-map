"""
test_batch_dop_resolution.py — batch_enrich_hces places HCES districts through the India Post
directory before Nominatim, and stops retrying a district after MAX_GEOCODE_FAILS failures.
14 districts failed geocoding every day from 7 Aug to 13 Oct 2026 (1,056 geocode_fail rows).

    python3 tests/test_batch_dop_resolution.py

Plain script, no network, no DATABASE_URL: uses the committed reference CSVs and boundaries.geojson.
"""
import csv
import os
import sys
import tempfile
from pathlib import Path

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ETL = os.path.join(REPO, "paisamap-etl", "etl")
sys.path.insert(0, ETL)
os.chdir(ETL)
import batch_enrich_hces as B  # noqa: E402

passed = 0


def check(cond, msg):
    global passed
    assert cond, f"FAIL: {msg}"
    passed += 1


# ── India Post resolution ────────────────────────────────────────────────────
geo = B.dop_district_point("NICOBARS", "ANDAMAN &NICOBAR", set())
check(geo is not None and geo["pincode"].startswith("744"), f"NICOBARS -> a Nicobar PIN (got {geo})")
check(6 <= geo["lat"] <= 14 and 92 <= geo["lng"] <= 94, "placed in the Nicobar islands, not a guess")

geo2 = B.dop_district_point("NICOBARS", "ANDAMAN &NICOBAR", {geo["pincode"]})
check(geo2 is None or geo2["pincode"] != geo["pincode"], "an already-scored PIN is never picked")

all_nicobar = set(B._dop_tables()["dist"].query("district == 'Nicobar'")["pincode"])
check(B.dop_district_point("NICOBARS", "ANDAMAN &NICOBAR", all_nicobar) is None,
      "a fully scored district returns None (no nearest-polygon guess)")

for hces, postal in [("KOCHBIHAR", "Cooch Behar"), ("NORTHTWENTYFOURPARGANAS", "North 24 Parganas"),
                     ("DOHAD", "Dahod"), ("KABEERDHAM", "Kawardha")]:
    g = B.dop_district_point(hces, "X", set())
    pcs = set(B._dop_tables()["dist"].query("district == @postal")["pincode"])
    check(g is not None and g["pincode"] in pcs, f"{hces} -> a {postal} PIN")

check(B.dop_district_point("KANSHIRAM NAGAR", "UTTAR PRADESH", set()) is None,
      "an unmatched district returns None (falls through to Nominatim)")

# ── give up after MAX_GEOCODE_FAILS ──────────────────────────────────────────
tmp = Path(tempfile.mkdtemp())
B.BATCH_LOG = tmp / "batch_enrich_log.csv"
B.OUT = tmp                                           # no ppi_ml_refined.csv here
with open(B.BATCH_LOG, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=B.BATCH_LOG_FIELDS)
    w.writeheader()
    for _ in range(B.MAX_GEOCODE_FAILS):
        w.writerow({"district": "KANSHIRAM NAGAR", "state": "UTTAR PRADESH", "status": "geocode_fail"})
    for _ in range(B.MAX_GEOCODE_FAILS - 1):
        w.writerow({"district": "DOHAD", "state": "GUJARAT", "status": "geocode_fail"})
    w.writerow({"district": "ROHTAK", "state": "HARYANA", "pincode": "124001", "status": "covered"})
_, done, given_up = B.load_already_done()
check(("UTTAR PRADESH", "KANSHIRAM NAGAR") in given_up, "3 failures -> given up")
check(("GUJARAT", "DOHAD") not in given_up, "2 failures -> still retried")
check(("HARYANA", "ROHTAK") in done, "a 'covered' district counts as done (no daily re-geocode)")

print(f"test_batch_dop_resolution: {passed} checks passed")

"""
test_no_osm_lookup.py — issue #8: enriching a new pincode makes no OpenStreetMap request and
writes no OSM-derived value.

    python3 tests/test_no_osm_lookup.py

Runs the real enrich_single.main() (the anonymous /api/enrich pin-drop path) and
batch_enrich_hces.backfill_raw_proxies() (cron step 3) against a throwaway copy of the raw/output
CSVs with the network blocked: every urlopen/requests call is recorded and refused. Plain script.
"""
import os
import shutil
import sys
import tempfile
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ETL = os.path.join(REPO, "paisamap-etl")
sys.path.insert(0, os.path.join(ETL, "etl"))
os.environ["DATABASE_URL"] = ""
passed = 0


def check(cond, msg):
    global passed
    assert cond, f"FAIL: {msg}"
    passed += 1


# ── network blocked + recorded ────────────────────────────────────────────────
requested = []


def _blocked_urlopen(req, *a, **kw):
    requested.append(req.full_url if hasattr(req, "full_url") else str(req))
    raise OSError("network blocked in test")


urllib.request.urlopen = _blocked_urlopen
try:
    import requests

    def _blocked_request(self, method, url, *a, **kw):
        requested.append(url)
        raise requests.ConnectionError("network blocked in test")
    requests.Session.request = _blocked_request
except ImportError:
    pass

# ── throwaway copy of the files enrich writes ─────────────────────────────────
tmp = tempfile.mkdtemp()
root = os.path.join(tmp, "paisamap-etl")
shutil.copytree(os.path.join(ETL, "data", "raw"), os.path.join(root, "data", "raw"))
shutil.copytree(os.path.join(ETL, "data", "output"), os.path.join(root, "data", "output"))
os.makedirs(os.path.join(root, "data", "reference"))
shutil.copy(os.path.join(ETL, "data", "reference", "pincode_district_map.csv"),
            os.path.join(root, "data", "reference"))
os.makedirs(os.path.join(tmp, "data", "output"))

from pathlib import Path
import pandas as pd
import _db
_db.enabled = lambda: False
import enrich_single as E
import batch_enrich_hces as B
E.ROOT, E.RAW, E.OUT, E.REF, E.APP = (Path(root), Path(root, "data", "raw"), Path(root, "data", "output"),
                                      Path(root, "data", "reference"), Path(tmp))
B.RAW, B.OUT = E.RAW, E.OUT
if hasattr(B, "APP"):
    B.APP = Path(tmp, "data", "output")

check(not hasattr(E, "fetch_poi") and not hasattr(E, "OVERPASS"), "enrich_single has no Overpass client")
check(not hasattr(E, "scale_from_poi"), "enrich_single no longer scales by POI density")


def snapshot(fname):
    return (E.RAW / fname).read_bytes()


osm_files = ("poi_density.csv", "financial_inclusion.csv")
before = {f: snapshot(f) for f in osm_files}

# a real Bengaluru pincode that isn't in the dataset yet
coords = pd.read_csv(E.RAW / "pincode_coords.csv", dtype={"pincode": str})
pc = next(p for p in ("560300", "560299", "560098", "560116", "562106") if p not in set(coords["pincode"]))
sys.argv = ["enrich_single.py", pc, "12.97", "77.59", "Test Locality"]
try:
    E.main()
except SystemExit as e:
    check(e.code in (0, None), f"enrich_single.main() exits cleanly (code {e.code})")

check(not [u for u in requested if "overpass" in u.lower()], f"no Overpass request ({requested})")
for f in osm_files:
    check(snapshot(f) == before[f], f"{f} untouched by a pin-drop enrich")
rates = pd.read_csv(E.RAW / "property_rates.csv", dtype={"pincode": str}).set_index("pincode")
check(pc in rates.index, "the new pincode still gets a property_rates row (so the refit keeps it)")
prior = E.CITY_PRIORS.get(E.state_for_pincode(pc), E._DEFAULT_PRIOR)
check(float(rates.at[pc, "rate_per_sqft"]) == round(prior["rate"]), "its rate is the plain state prior (ratio 1)")
app_csv = Path(tmp, "data", "output", "ppi_map_data.csv")
check(app_csv.exists(), "public map CSV written")
check(open(app_csv).readline().strip() == "pincode,name,lat,lng,ppi,income", "public map CSV has no poi column")

# batch path (cron step 3)
requested.clear()
pc2 = next(p for p in ("110097", "110098", "110099", "560301") if p not in set(rates.index))
B.backfill_raw_proxies(pc2, E.state_for_pincode(pc2), 28.6, 77.2)
check(not requested or not [u for u in requested if "overpass" in u.lower()], f"batch backfill: no Overpass request ({requested})")
for f in osm_files:
    check(snapshot(f) == before[f], f"{f} untouched by the batch backfill")
rates = pd.read_csv(E.RAW / "property_rates.csv", dtype={"pincode": str}).set_index("pincode")
check(pc2 in rates.index, "batch backfill still writes the prior proxy row")

pipeline = open(os.path.join(ETL, "run_pipeline.py")).read()
check('"fetch_financial.py"' not in pipeline, "run_pipeline.py doesn't run the Overpass branch fetcher")
for script in ("fetch_poi.py", "fetch_financial.py", "expand_pincodes.py", "expand_pincodes_mh_tier2.py"):
    check(not os.path.exists(os.path.join(ETL, "etl", script)), f"{script} (Overpass fetcher) is gone")
cron = open(os.path.join(REPO, "cron_enrich.sh")).read()
check("fetch_poi" not in cron and "fetch_financial" not in cron, "cron runs no OSM fetcher")

shutil.rmtree(tmp)
print(f"test_no_osm_lookup: {passed} checks passed")

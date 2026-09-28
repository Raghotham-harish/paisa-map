"""
test_osm_out_of_paid.py — issue #8: OpenStreetMap-derived signals are in no paid surface.

    python3 tests/test_osm_out_of_paid.py

OSM data is ODbL (share-alike); docs/DATA_LICENSING.md says do not sell it. The columns stay in the
raw CSVs (the current PPI model still trains on two of them until the retrain), but must not reach
a tier, the export (at ANY plan), the signal catalog, or a served per-pincode row.
Plain script: Flask test client, no login needed (the export's plan lookup is patched).
"""
import csv
import io
import json
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "paisamap-etl", "etl"))
os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/osm.sqlite"
passed = 0


def check(cond, msg):
    global passed
    assert cond, f"FAIL: {msg}"
    passed += 1


import _pricing
import _signals_data
if _signals_data._db is not None:
    _signals_data._db.enabled = lambda: False   # read the committed CSVs, not an empty test DB

OSM = {"premium_poi_per_km2", "fin_density_per_km2", "sfb_branches", "coop_branches",
       "rrb_branches", "fin_branches_total"}
check(set(_signals_data.OSM_COLUMNS) == OSM, "OSM_COLUMNS names every Overpass-derived column")

for tier, spec in _pricing.SIGNAL_TIERS.items():
    extras = spec["extra_signals"]
    if extras != "all":
        check(not (set(extras) & OSM), f"Signals {tier} lists no OSM signal ({sorted(set(extras) & OSM)})")
check(not (set(_pricing.CORE_SIGNALS) & OSM), "core signals carry no OSM signal")
check(not (_signals_data.PRO_COLUMNS & OSM), "PRO_COLUMNS has no OSM column")
check(not (set(_signals_data.EXPORT_ALL_COLUMNS) & OSM), "EXPORT_ALL_COLUMNS has no OSM column")
check(not (set(_signals_data.SIGNAL_LABELS) & OSM), "SIGNAL_LABELS has no OSM column (nothing to label)")
exported_files = {f for f, _ in _signals_data.EXPORT_SIGNAL_FILES}
check(not ({"poi_density.csv", "financial_inclusion.csv"} & exported_files),
      "the two Overpass-sourced raw files aren't joined into served rows")

# served per-pincode rows (feeds export, forecast, intelligence, expansion) carry no OSM key
rows, _src = _signals_data.load_ppi_signals_rows()
check(len(rows) > 1000, f"signal rows load ({len(rows)})")
leaked = {k for r in rows.values() for k in r} & OSM
check(not leaked, f"load_ppi_signals_rows() rows carry no OSM key ({sorted(leaked)})")

import server
c = server.app.test_client()

cat = c.get("/api/signals/catalog").get_json()["signals"]
keys = {s["key"] for s in cat}
check(len(keys) >= 15, f"catalog still lists the non-OSM signals ({len(keys)})")
check(not (keys & OSM), f"/api/signals/catalog lists no OSM signal ({sorted(keys & OSM)})")

for plan in ("free", "pro", _pricing.V2_PREFIX + "growth"):
    server.get_effective_plan = lambda plan=plan: plan
    r = c.get("/api/export?format=csv&dataset=ppi")
    check(r.status_code == 200, f"/api/export ({plan}) is 200")
    header = set(next(csv.reader(io.StringIO(r.get_data(as_text=True)))))
    check(not (header & OSM), f"/api/export CSV ({plan}) has no OSM column ({sorted(header & OSM)})")
    # asking for them by name doesn't get them either
    r = c.get("/api/export?format=json&dataset=ppi&columns=" + ",".join(sorted(OSM)))
    body = json.loads(r.get_data(as_text=True))
    got = {k for row in body["rows"] for k in row} & OSM
    check(not got, f"/api/export JSON ({plan}) ignores explicitly requested OSM columns ({sorted(got)})")
    if plan != "free":
        check("radiance_mean" in header, f"/api/export ({plan}) still carries non-OSM Pro signals")

# the paid forecast reads no OSM column: density proxy, SWOT/factor inputs, lever allocator
import inspect
import _forecast_model as F
check(not (set(F.DENSITY_PROXY_COLS) & OSM), "forecast household density proxy uses no OSM column")
check(not (set(F.SWOT_PROXY_COLS) & OSM), "forecast SWOT percentile columns carry no OSM column")
for key, _label, cols, _basis in F.FACTOR_DEFS:
    check(not (set(cols) & OSM), f"forecast factor {key} uses no OSM column")
for fn in (F.location_swot, F.compute_lever_split, F.build_forecast):
    src = inspect.getsource(fn)
    hits = [k for k in OSM if k in src]
    check(not hits, f"_forecast_model.{fn.__name__} names no OSM column ({hits})")
check("footfall_percentile" not in inspect.getsource(F), "forecast no longer returns a POI-based footfall_percentile")

html = open(os.path.join(REPO, "index.html"), encoding="utf-8").read()
for key in sorted(OSM):
    check(f'id:"{key}"' not in html, f"the map's signal picker has no {key}")

print(f"test_osm_out_of_paid: {passed} checks passed")

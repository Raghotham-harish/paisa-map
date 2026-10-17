"""
test_gdp_signal.py — the modelled GDP pair (gdp_per_capita, gdp_per_km2) is served
and catalogued, free, attributed, never 0 for a blank, and never a scoring input.

    python3 tests/test_gdp_signal.py

Source: Kummu, Kosonen & Masoumzadeh Sayyar (2025), CC BY 4.0, summed on DoP PIN
polygons (paisamap-etl/data/raw/gdp_pincode.csv). 7 Lakshadweep pins have
in_gdp=0 and blank values; 74 PPI pins have no DoP polygon, so no row at all.
Both kinds must export as null (JSON) / empty (CSV) — never 0, never a default.
Plain script: Flask test client, the export's plan lookup is patched.
"""
import csv
import inspect
import io
import json
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "paisamap-etl", "etl"))
os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/gdp.sqlite"
os.environ.pop("SIGNAL_TIERS", None)
passed = 0


def check(cond, msg):
    global passed
    assert cond, f"FAIL: {msg}"
    passed += 1


import _pricing
import _signals_data as SD
if SD._db is not None:
    SD._db.enabled = lambda: False   # read the committed CSVs, not an empty test DB

GDP = {"gdp_per_capita", "gdp_per_km2"}
RAW = os.path.join(REPO, "paisamap-etl", "data", "raw")

# ── the committed file ──────────────────────────────────────────────────────
with open(os.path.join(RAW, "gdp_pincode.csv"), newline="") as f:
    gdp_rows = list(csv.DictReader(f))
check(len(gdp_rows) == 19_191, f"gdp_pincode.csv covers the 19,191 DoP pins ({len(gdp_rows)})")
outside = {r["pincode"] for r in gdp_rows if r["in_gdp"] == "0"}
check(len(outside) == 7, f"7 pins outside the GDP raster ({len(outside)})")
for r in gdp_rows:
    for c in GDP:
        v = r[c]
        if r["in_gdp"] == "0":
            assert v == "", f"FAIL: in_gdp=0 pin {r['pincode']} has a {c} value ({v!r})"
        else:
            assert v != "" and float(v) > 0, f"FAIL: pin {r['pincode']} {c} is blank or <= 0 ({v!r})"
passed += 1
check(True, "in_gdp=0 rows are blank, every other row is a positive value (no zeros)")

# ── catalogue / tiers ───────────────────────────────────────────────────────
check(("gdp_pincode.csv", ["gdp_per_capita", "gdp_per_km2"]) in SD.EXPORT_SIGNAL_FILES, "both GDP columns exported")
check(GDP <= set(_pricing.FREE_SIGNALS), "GDP pair is in FREE_SIGNALS")
check(not (GDP & SD.PRO_COLUMNS), "GDP is not a Pro column")
check(not (GDP & set(_pricing.SIGNAL_TIERS["lite"]["extra_signals"])), "GDP is not on the Lite list")
check(SD.MODELLED_COLUMNS == GDP, "GDP pair is flagged modelled")
for c in GDP:
    check("est., modelled" in SD.SIGNAL_LABELS[c], f"{c} label says 'est., modelled'")
    check("GDP of" not in SD.SIGNAL_LABELS[c], f"{c} is never labelled 'GDP of a pincode'")
check(SD.attributions_for(["pincode", "gdp_per_capita", "gdp_per_km2"]) == [SD.GDP_ATTRIBUTION],
      "one attribution line for the GDP pair")
check(SD.attributions_for(["pincode", "rate_per_sqft"]) == [], "no attribution without a GDP column")
check("CC BY 4.0" in SD.GDP_ATTRIBUTION and "Kummu" in SD.GDP_ATTRIBUTION, "attribution names the authors + licence")

# ── served rows: blanks stay empty, never 0 ────────────────────────────────
rows, _src = SD.load_ppi_signals_rows()
check(len(rows) > 1000, f"signal rows load ({len(rows)})")
check(all(GDP <= set(r) for r in rows.values()), "every served row carries both GDP keys (so JSON says null)")
in_file = {r["pincode"] for r in gdp_rows}
no_polygon = [pc for pc in rows if pc not in in_file]
blank_pcs = [pc for pc in rows if pc in outside]
for pc in no_polygon + blank_pcs:
    for c in GDP:
        assert rows[pc][c] == "", f"FAIL: {pc} {c} should be blank, got {rows[pc][c]!r}"
passed += 1
for r in rows.values():
    for c in GDP:
        assert r[c] == "" or float(r[c]) > 0, f"FAIL: {r['pincode']} {c} = {r[c]!r} (a zero leaked)"
passed += 1

# the reviewer's join: 74 PPI pins with no polygon + 7 outside the raster
with open(os.path.join(RAW, "ppi_signal_join_with_gdp.csv"), newline="") as f:
    join = list(csv.DictReader(f))
join_blank = {r["pincode"] for r in join if r["gdp_per_capita"] == ""}
check(len(join) == 15_331 and len(join_blank) == 81, f"join: 15,331 rows, 81 blank ({len(join_blank)})")

import server
c = server.app.test_client()
server.get_effective_plan = lambda: "free"

r = c.get("/api/export?format=json&dataset=ppi")
body = json.loads(r.get_data(as_text=True))
check(body["attribution"] == [SD.GDP_ATTRIBUTION], "JSON export carries the GDP attribution")
by_pc = {row["pincode"]: row for row in body["rows"]}
blank_set = set(no_polygon + blank_pcs)
check(blank_set <= {str(p) for p in by_pc}, f"the {len(blank_set)} blank pins are in the export")
nulls = 0
for row in body["rows"]:
    for col in GDP:
        assert col in row, f"FAIL: JSON row {row['pincode']} has no {col} key"
        v = row[col]
        assert v is None or v > 0, f"FAIL: JSON {row['pincode']} {col} = {v!r}"
    if str(row["pincode"]) in blank_set:
        assert row["gdp_per_capita"] is None and row["gdp_per_km2"] is None, f"FAIL: {row['pincode']} not null"
        nulls += 1
check(nulls == len(blank_set), f"every blank pin exports null ({nulls}: {len(no_polygon)} no polygon + {len(blank_pcs)} in_gdp=0)")

r = c.get("/api/export?format=csv&dataset=ppi")
check(r.headers.get("X-Data-Attribution") == SD.GDP_ATTRIBUTION, "CSV export sends X-Data-Attribution")
reader = csv.DictReader(io.StringIO(r.get_data(as_text=True)))
check(GDP <= set(reader.fieldnames), "free CSV export has both GDP columns")
for row in reader:
    if row["pincode"] in blank_set:
        assert row["gdp_per_capita"] == "" and row["gdp_per_km2"] == "", f"FAIL: CSV {row['pincode']} not empty"
passed += 1

r = c.get("/api/export?format=csv&dataset=ppi&columns=rate_per_sqft")
check("X-Data-Attribution" not in r.headers, "no attribution header when no GDP column is exported")

try:
    import openpyxl
    r = c.get("/api/export?format=xlsx&dataset=ppi")
    wb = openpyxl.load_workbook(io.BytesIO(r.data))
    meta = {row[0].value: row[1].value for row in wb["Metadata"].iter_rows()}
    check(meta.get("Attribution") == SD.GDP_ATTRIBUTION, "xlsx Metadata sheet carries the attribution")
except ImportError:
    print("  (openpyxl missing — xlsx attribution not checked)")

# catalog: free, unlocked, with note + attribution, also when tiers are enforced
for flag in (None, "1"):
    if flag:
        os.environ["SIGNAL_TIERS"] = flag
    else:
        os.environ.pop("SIGNAL_TIERS", None)
    cat = c.get("/api/signals/catalog").get_json()["signals"]
    by_key = {s["key"]: s for s in cat}
    check(len(cat) == 20, f"catalog lists 20 entries ({len(cat)}, flag={flag})")
    for col in GDP:
        s = by_key[col]
        check(s["tier"] == "free" and s["locked"] is False and s["pro"] is False,
              f"catalog {col}: free + unlocked for anonymous (flag={flag})")
        check(s["group"] == "Economy (modelled)" and s["modelled"] is True, f"catalog {col}: group + modelled")
        check(s["note"] == SD.GDP_NOTE and s["attribution"] == SD.GDP_ATTRIBUTION, f"catalog {col}: note + attribution")
    check("note" not in by_key["rate_per_sqft"], "non-modelled signals carry no GDP note")
os.environ.pop("SIGNAL_TIERS", None)

# ── never a scoring input ───────────────────────────────────────────────────
from blueprints import expansion as E
# A store set whose revenue tracks GDP per capita perfectly: GDP still must not be a driver.
pcs = [pc for pc, r in rows.items() if r["gdp_per_capita"] not in ("", None)][:40]
locs = [{"pincode": pc, "revenue": float(rows[pc]["gdp_per_capita"])} for pc in pcs]
drivers = E._compute_drivers(locs, rows)
check(drivers["sufficient_data"], "driver analysis ran on the synthetic store set")
check(not ({d["signal"] for d in drivers["drivers"]} & GDP), "GDP is never an expansion driver")

etl = os.path.join(REPO, "paisamap-etl", "etl")
for name in ("ml_refinement.py", "build_household_estimates.py", "_forecast_model.py"):
    path = os.path.join(etl, name)
    if os.path.exists(path):
        check("gdp_per" not in open(path, encoding="utf-8").read(), f"{name} never reads a GDP column")
import _forecast_model as F
check(not (set(F.DENSITY_PROXY_COLS) | set(F.SWOT_PROXY_COLS)) & GDP, "forecast proxies carry no GDP column")

# ── the map ─────────────────────────────────────────────────────────────────
html = open(os.path.join(REPO, "index.html"), encoding="utf-8").read()
for col in GDP:
    check(f'id:"{col}"' in html, f"map picker lists {col}")
check('"Economy (modelled)":"ti-chart-line"' in html, "map has the Economy (modelled) icon")
check("&& !(SIGNAL_DEFS[c] && SIGNAL_DEFS[c].modelled)" in html, "map suitability preview skips modelled signals")
check("const sigNum = v =>" in html and "sigNum(row[sigId])" in html, "map reads a null signal as missing, not 0")

print(f"test_gdp_signal: {passed} checks passed")

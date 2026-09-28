"""
test_vehicle_signal_dedupe.py — issue #6. cars_per_1000 is an exact copy of
lmv_per_1000. The published signal (API, catalog, map picker, pricing lite,
forecast composites) is lmv_per_1000. The model still trains on cars_per_1000
from vehicle_density.csv; that feature list must not change.

    python3 tests/test_vehicle_signal_dedupe.py
"""

import csv
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "paisamap-etl" / "etl"))

import _pricing  # noqa: E402
import _signals_data as S  # noqa: E402
import blueprints.projects as projects  # noqa: E402

passed = 0


def check(cond, msg):
    global passed
    assert cond, f"FAIL: {msg}"
    passed += 1
    print(f"  ok  {msg}")


check("cars_per_1000" not in S.EXPORT_ALL_COLUMNS, "export columns omit cars_per_1000")
check("lmv_per_1000" in S.EXPORT_ALL_COLUMNS, "export columns keep lmv_per_1000")
check("cars_per_1000" not in S.PRO_COLUMNS, "pro column set omits the duplicate")
check(S.canonical_signal("cars_per_1000") == "lmv_per_1000", "cars_per_1000 aliases to lmv_per_1000")
check(S.canonical_signals(["cars_per_1000", "ppi_ml", "lmv_per_1000"]) == ["lmv_per_1000", "ppi_ml"],
      "aliasing collapses the duplicate id")

stored = projects._signals_json(["cars_per_1000", "ppi_ml", "lmv_per_1000"])
check(json.loads(stored) == ["lmv_per_1000", "ppi_ml"], "project writes store the canonical id")
shaped = projects._shape({
    "signals": '["cars_per_1000", "ppi_ml"]',
    "target_pincodes": '["560038"]',
})
check(shaped["signals"] == ["lmv_per_1000", "ppi_ml"], "_shape maps a saved cars_per_1000 onto lmv_per_1000")
check(shaped["target_pincodes"] == ["560038"], "target pincodes are not run through the signal alias")

from flask import Flask  # noqa: E402
from blueprints.signals import signals_bp  # noqa: E402

app = Flask(__name__)
app.register_blueprint(signals_bp)
catalog = app.test_client().get("/api/signals/catalog").get_json()["signals"]
keys = [item["key"] for item in catalog]
vehicle = [k for k in keys if k in ("cars_per_1000", "lmv_per_1000")]
check(vehicle == ["lmv_per_1000"], f"catalog publishes one vehicle-density signal, got {vehicle}")

lite = _pricing.SIGNAL_TIERS["lite"]["extra_signals"]
check("lmv_per_1000" in lite and "cars_per_1000" not in lite and len(lite) == 10 and len(set(lite)) == 10,
      "lite tier lists lmv_per_1000 once among 10 extras")

index = (REPO / "index.html").read_text()
check('id:"cars_per_1000"' not in index and "id:'cars_per_1000'" not in index,
      "map picker has no cars_per_1000 id")
check('id:"lmv_per_1000"' in index, "map picker still offers lmv_per_1000")

ml_src = (REPO / "paisamap-etl" / "etl" / "ml_refinement.py").read_text()
start = ml_src.index("def load_features(")
end = ml_src.index("\ndef ", start + 1)
body = ml_src[start:end]
check('("vehicle_density.csv",  "cars_per_1000")' in body, "load_features still trains on cars_per_1000")
check("lmv_per_1000" not in body, "load_features does not add lmv_per_1000 as a second feature")

rows, _source = S.load_ppi_signals_rows()
signal_cols = [c for _, cols in S.EXPORT_SIGNAL_FILES for c in cols]
identical = []
for i, left in enumerate(signal_cols):
    for right in signal_cols[i + 1:]:
        both = []
        for row in rows.values():
            a, b = row.get(left), row.get(right)
            if a not in (None, "") and b not in (None, ""):
                both.append((str(a), str(b)))
        if len(both) >= 100 and all(a == b for a, b in both):
            identical.append((left, right, len(both)))
check(not identical, f"no exported signal pair is identical, found {identical}")

raw = REPO / "paisamap-etl" / "data" / "raw"
cars, lmv = {}, {}
with open(raw / "vehicle_density.csv", newline="") as f:
    for row in csv.DictReader(f):
        if row.get("pincode") and row.get("cars_per_1000") not in (None, ""):
            cars[row["pincode"]] = row["cars_per_1000"]
with open(raw / "rto_enhanced.csv", newline="") as f:
    for row in csv.DictReader(f):
        if row.get("pincode") and row.get("lmv_per_1000") not in (None, ""):
            lmv[row["pincode"]] = row["lmv_per_1000"]
joined = [pc for pc in cars if pc in lmv]
equal = sum(1 for pc in joined if cars[pc] == lmv[pc])
print(f"  raw files still match on {equal}/{len(joined)} joined pincodes (source not split; only the published column changed)")
check(equal == len(joined) and len(joined) > 0, "raw cars_per_1000 and lmv_per_1000 remain the same series")

print(f"\n{passed} checks passed")

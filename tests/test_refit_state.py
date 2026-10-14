"""
test_refit_state.py — F1 (refit peer review, 2026-10-06): the weekly refit takes state from the
pincode master, not its old 2-digit prefix table.

    python3 tests/test_refit_state.py

The old table put 7% of master pincodes in the wrong state (Goa with Maharashtra, Uttarakhand with
UP, Yamunanagar with HP, the 79x northeast as "XX"), which fed both the fallback refit grouping
and the state-level vehicle_growth_4yr feature. Plain script.
"""
import os
import sys

import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ETL = os.path.join(REPO, "paisamap-etl", "etl")
sys.path.insert(0, ETL)
os.chdir(ETL)
import ml_refinement as ml  # noqa: E402
from pincode_master import state_for_pincode, _BY_PIN, _load  # noqa: E402

passed = 0


def check(cond, msg):
    global passed
    assert cond, f"FAIL: {msg}"
    passed += 1


src = open(os.path.join(ETL, "ml_refinement.py")).read()
check("_REFIT_GROUP_PREFIX" not in src, "old prefix table is gone")
check(not hasattr(ml, "_REFIT_GROUP_PREFIX"), "no _REFIT_GROUP_PREFIX attribute")

# Pincodes the old table got wrong -> real state
for pc, want in [("403001", "GA"), ("248001", "UK"), ("799001", "TR"), ("135103", "HR"),
                 ("793001", "ML"), ("737101", "SK"), ("160017", "CH"), ("605001", "PY")]:
    check(ml._state(pc) == want, f"_state({pc}) = {ml._state(pc)}, want {want}")
    check(state_for_pincode(pc) == want, f"master state for {pc} is {want}")

# PINCODE_STATE stays as the metro grouping override
for pc, grp in ml.PINCODE_STATE.items():
    check(ml._state(pc) == grp, f"PINCODE_STATE override kept for {pc}")

# No master pincode falls through to "XX"
_load()
xx = [pc for pc in _BY_PIN if ml._state(pc) == "XX"]
check(not xx, f"{len(xx)} master pincodes grouped as XX, e.g. {xx[:5]}")

# vehicle_growth_4yr: every state in vehicle_state_trend.csv maps to a master state code
vst = pd.read_csv(os.path.join(REPO, "paisamap-etl", "data", "raw", "vehicle_state_trend.csv"))
master_codes = set(_BY_PIN.values())
frames = ml.load_features()
growth = frames["vehicle_growth_4yr"]
mapped = {state_for_pincode(pc, log_fallback=False) for pc in growth.dropna().index}
check(len(mapped) >= len(vst) - 1, f"growth reaches {len(mapped)} states of {len(vst)} in the file")
for pc, want in [("403001", "Goa"), ("799001", "Tripura"), ("248001", "Uttarakhand"),
                 ("122002", "Haryana")]:
    if pc in growth.index:
        v = vst.set_index("state_name").loc[want, "growth_4yr_pct"]
        check(growth[pc] == v, f"{pc} vehicle growth = {want} ({growth[pc]} vs {v})")
unmapped_codes = sorted(master_codes - mapped)
print(f"  master state codes with no vehicle growth row: {unmapped_codes}")

print(f"test_refit_state: {passed} checks passed")

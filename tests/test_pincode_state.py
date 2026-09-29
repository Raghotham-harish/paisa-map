"""
test_pincode_state.py — issue #2. State comes from the India Post reference,
not a hand-written prefix table.

    python3 tests/test_pincode_state.py
"""

import logging
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "paisamap-etl" / "etl"))

# Keep the fallback log out of the repo. Read at call time.
_log_dir = tempfile.mkdtemp(prefix="state-fallback-")
os.environ["STATE_FALLBACK_LOG"] = str(Path(_log_dir) / "state_fallback_log.csv")

import pincode_master as M  # noqa: E402

passed = 0


def check(cond, msg):
    global passed
    assert cond, f"FAIL: {msg}"
    passed += 1
    print(f"  ok  {msg}")


# Every pincode in the reference resolves to that file's own state code.
M._load()
mismatches = [(pc, st, M.state_for_pincode(pc)) for pc, st in M._BY_PIN.items()
              if M.state_for_pincode(pc) != st]
check(not mismatches, f"0 mismatches against the reference ({len(M._BY_PIN)} pincodes)")
check(M.fallback_use_count() == 0, "reference hits do not use the prefix fallback")

for pc, want in [
    ("248001", "UK"),  # Uttarakhand, previously folded into UP
    ("131001", "HR"),  # Haryana 13x, previously Himachal
    ("122001", "HR"),  # Haryana 12x, previously Delhi in ml_refinement
    ("403001", "GA"),  # Goa, previously Maharashtra
    ("605001", "PY"),  # Puducherry, previously Tamil Nadu
    ("737101", "SK"),  # Sikkim, previously West Bengal
    ("744101", "AN"),  # Andaman & Nicobar, previously West Bengal
    ("793001", "ML"),  # Meghalaya, previously unmapped
]:
    check(M.state_for_pincode(pc) == want, f"{pc} → {want}")

# Clear-cut prefix corrections, used only when the pincode is not in the file.
check(M.PREFIX_STATE.get("12") == "HR", "prefix 12 → HR")
check(M.PREFIX_STATE.get("13") == "HR", "prefix 13 → HR")
check(M.PREFIX_STATE.get("23") == "UP", "prefix 23 → UP")

# A pincode the reference does not contain still gets a logged fallback.
absent = None
for n in range(100000, 200000):
    pc = f"{n:06d}"
    if pc not in M._BY_PIN and pc[:2] == "13":
        absent = pc
        break
check(absent is not None, "found a 13x pincode absent from the reference")

records = []


class _H(logging.Handler):
    def emit(self, record):
        records.append(record)


logger = logging.getLogger("pincode_master")
logger.setLevel(logging.WARNING)
logger.addHandler(_H())

got = M.state_for_pincode(absent)
check(got == "HR", f"absent {absent} falls back to HR, got {got}")
check(M.fallback_use_count() == 1, "fallback use is counted")
check(records and "prefix fallback" in records[0].getMessage(), "fallback logs a warning")
log_path = Path(os.environ["STATE_FALLBACK_LOG"])
text = log_path.read_text()
check(absent in text and "HR" in text, "fallback use is written to the enrichment fallback log")
# Same pincode again is not a second use.
check(M.state_for_pincode(absent) == "HR" and M.fallback_use_count() == 1,
      "a repeated lookup of the same pincode is not counted twice")

# 79x is not one state. An absent Meghalaya pin must not collapse to Tripura.
absent_ml = None
for n in range(793000, 794000):
    pc = f"{n:06d}"
    if pc not in M._BY_PIN:
        absent_ml = pc
        break
check(absent_ml is not None and M.state_for_pincode(absent_ml, log_fallback=False) == "ML",
      f"absent {absent_ml} → ML via the 3-digit prefix, not a single 79 state")

# The prefix table lives in one module. Call sites use state_for_pincode.
hits = []
etl = REPO / "paisamap-etl" / "etl"
for path in sorted(etl.glob("*.py")):
    for i, line in enumerate(path.read_text().splitlines(), 1):
        if "PREFIX_STATE" in line:
            hits.append(f"{path.name}:{i}")
check(hits and all(h.startswith("pincode_master.py:") for h in hits),
      f"PREFIX_STATE only in the fallback module, found {hits}")

# The refit keeps its old grouping table until the PPI retrain (see the
# comment on _REFIT_GROUP_PREFIX in ml_refinement.py and TODO.md). It must not
# spread to other modules.
held = [f"{p.name}" for p in sorted(etl.glob("*.py")) if "_REFIT_GROUP_PREFIX" in p.read_text()]
check(held == ["ml_refinement.py"], f"legacy refit grouping only in ml_refinement.py, found {held}")

print(f"\n{passed} checks passed")

"""
pincode_master.py — pincode → state from the India Post / LGD reference.

`data/reference/pincode_district_state_india.csv` is the source of truth.
The prefix table is only a last resort for a pincode that is not in that
file, and every such use is logged. Two-digit prefixes that cover more than
one state (UP/UK, MH/GA, TN/PY, WB/SK/AN, KL/LD, BR/JH, the 79x northeast
states, …) are never trusted as a single state: the 3-digit prefix is used
when it is unambiguous, otherwise the majority state is returned and marked
low-confidence.
"""

from __future__ import annotations

import csv
import fcntl
import logging
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("pincode_master")

ROOT = Path(__file__).resolve().parents[1]
REFERENCE_CSV = ROOT / "data" / "reference" / "pincode_district_state_india.csv"

# Last-resort 2-digit prefix → majority state in the reference. Filled by
# _load(). Not used when the pincode is in the reference, and not used when
# the 3-digit prefix already names a single state. Clear-cut corrections
# versus the old hand tables: 12 and 13 are Haryana, 23 is Uttar Pradesh.
PREFIX_STATE: dict[str, str] = {}
# 2- and 3-digit prefix → (state, "high"|"low"). "low" means that prefix
# spans more than one state in the reference, so this is only a majority guess.
_PREFIX2: dict[str, tuple[str, str]] = {}
_PREFIX3: dict[str, tuple[str, str]] = {}
_BY_PIN: dict[str, str] = {}

_FALLBACK_SEEN: set[str] = set()
_LOG_FIELDS = ("timestamp", "pincode", "fallback_state", "confidence", "note")


def _fallback_log_path() -> Path:
    override = os.environ.get("STATE_FALLBACK_LOG")
    if override:
        return Path(override)
    # Sits next to the other enrichment outputs. One row per pincode that
    # had to use the prefix table (counted once per process).
    return ROOT / "data" / "output" / "state_fallback_log.csv"


def _load() -> None:
    if _BY_PIN:
        return
    by2: dict[str, Counter] = defaultdict(Counter)
    by3: dict[str, Counter] = defaultdict(Counter)
    with open(REFERENCE_CSV, newline="") as f:
        for row in csv.DictReader(f):
            pc = (row.get("pincode") or "").strip()
            st = (row.get("state_code") or "").strip()
            if len(pc) != 6 or not st:
                continue
            _BY_PIN[pc] = st
            by2[pc[:2]][st] += 1
            by3[pc[:3]][st] += 1
    PREFIX_STATE.clear()
    _PREFIX2.clear()
    for pfx, counts in by2.items():
        state, _n = counts.most_common(1)[0]
        PREFIX_STATE[pfx] = state
        _PREFIX2[pfx] = (state, "high" if len(counts) == 1 else "low")
    _PREFIX3.clear()
    for pfx, counts in by3.items():
        state, _n = counts.most_common(1)[0]
        confidence = "high" if len(counts) == 1 else "low"
        _PREFIX3[pfx] = (state, confidence)


def _prefix_fallback(pc: str) -> tuple[str, str]:
    """Return (state_code, confidence) from the prefix tables.

    An unambiguous 3-digit prefix wins (this is what separates the 79x
    northeast states). An unambiguous 2-digit prefix is next (12/13 → HR,
    23 → UP). An ambiguous prefix returns its majority state at low
    confidence — the reference file is the real source, and this guess is
    only for a pincode the file does not contain.
    """
    _load()
    hit3 = _PREFIX3.get(pc[:3]) if len(pc) >= 3 else None
    if hit3 and hit3[1] == "high":
        return hit3
    hit2 = _PREFIX2.get(pc[:2]) if len(pc) >= 2 else None
    if hit2 and hit2[1] == "high":
        return hit2
    if hit3:
        return hit3[0], "low"
    if hit2:
        return hit2[0], "low"
    return "XX", "low"


def _note_fallback(pc: str, state: str, confidence: str) -> None:
    """Log a warning and append one enrichment-log row per pincode per process."""
    if pc in _FALLBACK_SEEN:
        return
    _FALLBACK_SEEN.add(pc)
    log.warning(
        "state_for_pincode prefix fallback #%s: pincode %s -> %s "
        "(%s confidence; not in the India Post master)",
        len(_FALLBACK_SEEN), pc, state, confidence,
    )
    path = _fallback_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    row = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pincode": pc,
        "fallback_state": state,
        "confidence": confidence,
        "note": "prefix fallback; pincode absent from pincode_district_state_india.csv",
    }
    with open(lock_path, "a") as lockf:
        fcntl.flock(lockf, fcntl.LOCK_EX)
        try:
            need_header = not path.exists() or path.stat().st_size == 0
            with open(path, "a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=_LOG_FIELDS)
                if need_header:
                    w.writeheader()
                w.writerow(row)
        finally:
            fcntl.flock(lockf, fcntl.LOCK_UN)


def fallback_use_count() -> int:
    """How many distinct pincodes have used the prefix fallback in this process."""
    return len(_FALLBACK_SEEN)


def state_for_pincode(pc: str, *, log_fallback: bool = True) -> str:
    """State code for a pincode.

    The India Post / LGD reference wins. The prefix table is used only when
    the pincode is absent from it, and that use is logged (unless
    log_fallback is False, for callers that are only comparing).
    """
    _load()
    key = str(pc).strip()
    hit = _BY_PIN.get(key)
    if hit:
        return hit
    state, confidence = _prefix_fallback(key)
    if log_fallback:
        _note_fallback(key, state, confidence)
    return state

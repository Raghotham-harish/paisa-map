"""
_filelock.py — cross-process lock guarding writes to the shared PPI data CSVs.

enrich_single.py (spawned per pin-drop — server.py's bulkEnrichNearby/
prefetchHexNeighbors can fire up to ~15 concurrent subprocesses from one
user action) and batch_enrich_hces.py (nightly cron) both do unlocked
read-modify-write on the same raw proxy CSVs and ppi_ml_refined.csv/
ppi_map_data.csv. Without serialization, concurrent writers silently
clobber each other's rows — last write wins, earlier rows vanish with no
error. Every write site touching those shared files must hold this lock
for the full read-check-write cycle, not just the write() call, or the
race just moves to whichever process reads first.
"""
import fcntl
from contextlib import contextmanager
from pathlib import Path

_LOCK_PATH = Path(__file__).resolve().parents[1] / "data" / ".write.lock"


@contextmanager
def write_lock():
    _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_LOCK_PATH, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)

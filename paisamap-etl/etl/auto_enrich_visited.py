#!/usr/bin/env python3
"""
auto_enrich_visited.py — Enrich pincodes recently visited by users but not yet in the ML output.

Reads enrichment_log.csv for entries with source=yah|search|prefetch from the
last N days.  For each new pincode (not already in ppi_ml_refined.csv) it calls
enrich_single.py — the full pipeline: Overpass POI + state priors + IDW PPI.

Run this daily on the server (via cron_enrich.sh) to turn blue pins green
automatically without waiting for the user to click "enrich".

Usage:
  python3 etl/auto_enrich_visited.py                  # last 7 days
  python3 etl/auto_enrich_visited.py --days 30        # look back 30 days
  python3 etl/auto_enrich_visited.py --dry-run        # show what would run
"""

import argparse
import csv
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT  = Path(__file__).resolve().parents[1]
OUT   = ROOT / "data" / "output"
APP   = ROOT.parent / "data" / "output"

ENRICH_LOG  = APP / "enrichment_log.csv"
ENRICH_SCRIPT = Path(__file__).parent / "enrich_single.py"

VENV_PY = ROOT / "venv" / "bin" / "python3"
PYTHON  = str(VENV_PY) if VENV_PY.exists() else sys.executable

# Sources that represent real user visits (not the initial phase1 seed)
VISIT_SOURCES = {"yah", "search", "prefetch", "manual"}


def load_enrichment_log() -> list[dict]:
    log_path = ENRICH_LOG
    if not log_path.exists():
        # Try local path (dev environment)
        log_path = OUT / "enrichment_log.csv"
    if not log_path.exists():
        return []
    with open(log_path, newline="") as f:
        return list(csv.DictReader(f))


def load_done_pincodes() -> set:
    ml_path = OUT / "ppi_ml_refined.csv"
    if not ml_path.exists():
        return set()
    done = set()
    with open(ml_path, newline="") as f:
        for row in csv.DictReader(f):
            if row.get("pincode"):
                done.add(row["pincode"].strip())
    return done


def run_enrich(pc: str, lat: str, lng: str, name: str, dry_run: bool) -> bool:
    print(f"  Enriching {pc} — {name} ({lat}, {lng})")
    if dry_run:
        print(f"    DRY-RUN: would run enrich_single.py {pc} {lat} {lng} '{name}'")
        return True

    result = subprocess.run(
        [PYTHON, str(ENRICH_SCRIPT), pc, lat, lng, name],
        cwd=str(ROOT), timeout=200
    )
    if result.returncode == 0:
        print(f"    ✓ enriched {pc}")
        return True
    else:
        print(f"    ✗ failed {pc} (exit {result.returncode})")
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days",    type=int, default=7, help="Look back N days in enrichment log")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)

    print(f"\nAuto-enrich visited pincodes — last {args.days} days")
    print(f"  Cutoff: {cutoff.strftime('%Y-%m-%d %H:%M UTC')}")

    log_rows = load_enrichment_log()
    done     = load_done_pincodes()

    print(f"  Enrichment log entries: {len(log_rows)}")
    print(f"  Already in ML output:   {len(done)}")

    # Find pincodes from real user visits in the lookback window that aren't done yet
    candidates: dict[str, dict] = {}  # pincode → most-recent log row
    for row in log_rows:
        src = row.get("source", "")
        if src not in VISIT_SOURCES:
            continue
        pc = row.get("pincode", "").strip()
        if not pc or pc in done:
            continue

        # Parse timestamp
        try:
            ts_str = row.get("timestamp", "")
            # Handle both Z and +00:00 formats
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except ValueError:
            continue

        if ts < cutoff:
            continue

        # Keep most recent entry per pincode
        if pc not in candidates or ts > datetime.fromisoformat(
            candidates[pc]["timestamp"].replace("Z", "+00:00")
        ):
            candidates[pc] = row

    print(f"  New pincodes to enrich: {len(candidates)}")
    if not candidates:
        print("  Nothing to do.")
        return

    ok = 0
    fail = 0
    for pc, row in sorted(candidates.items()):
        lat  = row.get("lat", "")
        lng  = row.get("lng", "")
        name = row.get("name", pc)
        if not lat or not lng:
            print(f"  SKIP {pc} — no coordinates in log")
            continue

        success = run_enrich(pc, lat, lng, name, args.dry_run)
        if success:
            ok += 1
            done.add(pc)
        else:
            fail += 1

    print(f"\nDone.  Enriched: {ok}  Failed: {fail}")


if __name__ == "__main__":
    main()

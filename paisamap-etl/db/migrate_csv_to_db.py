#!/usr/bin/env python3
"""
migrate_csv_to_db.py — one-time (and safely re-runnable) backfill of the
current CSV state into the database.

  DATABASE_URL=postgresql+psycopg2://... python3 db/migrate_csv_to_db.py

Loads:
  paisamap-etl/data/output/ppi_ml_refined.csv  → pincodes    (upsert by pincode)
  data/output/enrichment_log.csv                → enrichment_log (insert, dedup on ts+pincode+source)

Idempotent: run it again after new CSV rows land and it will only add what's
missing (pincodes are upserted so re-running just refreshes them; log rows
are deduped on their natural key so re-running adds nothing already present).
"""

import csv
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "etl"))
import _db  # noqa: E402

ROOT     = Path(__file__).resolve().parents[1]
APP_ROOT = ROOT.parent
PPI_CSV  = ROOT / "data" / "output" / "ppi_ml_refined.csv"
LOG_CSV  = APP_ROOT / "data" / "output" / "enrichment_log.csv"


def main():
    if not _db.enabled():
        print("DATABASE_URL is not set — nothing to do. Set it and re-run, e.g.:")
        print("  DATABASE_URL=postgresql+psycopg2://paisamap:PASS@localhost/paisamap python3 db/migrate_csv_to_db.py")
        sys.exit(1)

    print("Creating schema (if not already present)…")
    _db.init_schema()

    print(f"\nLoading pincodes from {PPI_CSV} …")
    if not PPI_CSV.exists():
        print(f"  MISSING — skipping pincodes")
    else:
        with open(PPI_CSV, newline="") as f:
            rows = list(csv.DictReader(f))
        n = _db.bulk_upsert_pincodes(rows)
        print(f"  Upserted {n} pincodes")

    print(f"\nLoading enrichment log from {LOG_CSV} …")
    if not LOG_CSV.exists():
        print(f"  MISSING — skipping enrichment_log")
    else:
        with open(LOG_CSV, newline="") as f:
            rows = list(csv.DictReader(f))
        for r in rows:
            r["ts"] = r.pop("timestamp")
        n = _db.bulk_insert_logs(rows)
        print(f"  Inserted {n} log rows (source file had {len(rows)} — difference is dedup on ts+pincode+source)")

    print("\nRow counts now in DB:", _db.counts())


if __name__ == "__main__":
    main()

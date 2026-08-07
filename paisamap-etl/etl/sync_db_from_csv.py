#!/usr/bin/env python3
"""
sync_db_from_csv.py — one-time catch-up: push the current
ppi_ml_refined.csv into the Postgres "pincodes" table.

Only enrich_single.py/batch_enrich_hces.py dual-write to the DB — a full
ml_refinement.py refit never did (fixed 2026-08-08, see that script's own
dual-write block), so every full refit before that fix silently left the DB
further behind the CSV. Found via /api/db_status: DB had 409 pincodes, CSV
had 600 — 191 pincodes (32% of the pipeline's real output) were computed
but never served, since server.py's /api/export prefers the DB when it's
configured.

This script is a pure read-CSV/write-DB catch-up — it doesn't recompute
anything, just upserts whatever ppi_ml_refined.csv currently has. No-op if
DATABASE_URL isn't set (same as every other _db.py caller). Safe to re-run
any time; every future full refit now does this automatically.

Usage:
  cd paisamap-etl && python3 etl/sync_db_from_csv.py
"""

from pathlib import Path
import pandas as pd
import _db

ROOT = Path(__file__).resolve().parents[1]
OUT  = ROOT / "data" / "output"


def main():
    if not _db.enabled():
        print("DATABASE_URL not set — nothing to sync (this is a no-op here by design).")
        return

    before = _db.counts()
    print(f"Before: DB has {before['pincodes']} pincodes")

    csv_path = OUT / "ppi_ml_refined.csv"
    df = pd.read_csv(csv_path, dtype={"pincode": str})
    print(f"CSV has {len(df)} pincodes")

    rows = [
        {
            "pincode": r["pincode"], "name": r.get("name") or r["pincode"],
            "lat": r["lat"], "lng": r["lng"], "ppi_ml": r["ppi_ml"],
            "ppi_original": r.get("ppi_original"),
            "est_monthly_income_hh": r["est_monthly_income_hh"],
            "est_monthly_spend_hh": r.get("est_monthly_spend_hh"),
        }
        for _, r in df.iterrows()
    ]
    n = _db.bulk_upsert_pincodes(rows)
    print(f"Upserted {n} pincodes")

    after = _db.counts()
    print(f"After: DB has {after['pincodes']} pincodes")
    if after["pincodes"] == len(df):
        print("✓ DB now matches CSV.")
    else:
        print(f"⚠ Still a gap: DB {after['pincodes']} vs CSV {len(df)} — "
              f"check for rows with invalid/missing required fields.")


if __name__ == "__main__":
    main()

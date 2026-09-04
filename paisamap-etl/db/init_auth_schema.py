#!/usr/bin/env python3
"""
init_auth_schema.py — one-time (and safely re-runnable) creator for the auth/
workspace tables: users, organizations, org_members, projects, saved_locations,
reports, credits_ledger, activity_log.

  DATABASE_URL=postgresql+psycopg2://... python3 db/init_auth_schema.py

Unlike migrate_csv_to_db.py, there's no CSV to backfill from — these tables
start empty. This script just runs _auth_db.init_schema() (CREATE TABLE IF NOT
EXISTS for all 8), safe to re-run after any deploy.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "etl"))
import _auth_db  # noqa: E402


def main():
    if not _auth_db.enabled():
        print("DATABASE_URL is not set — nothing to do. Set it and re-run, e.g.:")
        print("  DATABASE_URL=postgresql+psycopg2://paisamap:PASS@localhost/paisamap python3 db/init_auth_schema.py")
        sys.exit(1)

    print("Creating auth/workspace schema (if not already present)…")
    _auth_db.init_schema()
    print("Done. Tables: organizations, users, org_members, projects, "
          "saved_locations, reports, credits_ledger, activity_log")


if __name__ == "__main__":
    main()

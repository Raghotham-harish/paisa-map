#!/usr/bin/env python3
"""
backfill_connection_selections.py — Phase C5: one-time (and safely
re-runnable) migration for connections created before
project_connection_selections existed: gives every oauth_connections row
without a selection an explicit one linking it back to the project that
originally created it, and stamps org_id on any connection that predates
that column.

  DATABASE_URL=postgresql+psycopg2://... python3 db/backfill_connection_selections.py

This preserves each connection's existing behavior exactly (every project
keeps using whatever it already had) — it's the selection layer becoming
explicit, not a data change. See _auth_db.py's "Organizations (Phase C,
staged)" section for what actually reads project_connection_selections.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "etl"))
import _auth_db  # noqa: E402


def main():
    if not _auth_db.enabled():
        print("DATABASE_URL is not set — nothing to do. Set it and re-run, e.g.:")
        print("  DATABASE_URL=postgresql+psycopg2://paisamap:PASS@localhost/paisamap python3 db/backfill_connection_selections.py")
        sys.exit(1)

    print("Applying schema (if not already present)…")
    _auth_db.init_schema()
    _auth_db.migrate_schema()

    print("Backfilling connection selections…")
    counts = _auth_db.backfill_connection_selections()
    print(f"  org_id stamped on:      {counts['org_id_stamped']} connection(s)")
    print(f"  selections created:     {counts['selections_created']}")
    print("Done.")


if __name__ == "__main__":
    main()

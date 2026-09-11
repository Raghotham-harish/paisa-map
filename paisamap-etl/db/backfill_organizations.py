#!/usr/bin/env python3
"""
backfill_organizations.py — Phase C / C3: one-time (and safely re-runnable)
backfill that gives every existing user without one an auto-created default
company, and stamps their existing projects / oauth_connections /
customer_uploads / customer_locations / credits_ledger rows with that org_id.

  DATABASE_URL=postgresql+psycopg2://... python3 db/backfill_organizations.py

Additive in effect, not a behavioral cutover: nothing in the app reads org_id
to make a decision yet (see _auth_db.py's "Organizations (Phase C, staged)"
section), so running this changes zero live behavior today — it only
prepares the data a later, separate cutover will actually read. Idempotent —
only touches rows where org_id IS NULL, so re-running after new signups is
safe and just backfills what's newly missing.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "etl"))
import _auth_db  # noqa: E402


def main():
    if not _auth_db.enabled():
        print("DATABASE_URL is not set — nothing to do. Set it and re-run, e.g.:")
        print("  DATABASE_URL=postgresql+psycopg2://paisamap:PASS@localhost/paisamap python3 db/backfill_organizations.py")
        sys.exit(1)

    print("Applying schema (if not already present)…")
    _auth_db.init_schema()
    _auth_db.migrate_schema()

    print("Backfilling organizations…")
    counts = _auth_db.backfill_organizations()
    print(f"  Users seen:              {counts['users_seen']}")
    print(f"  Orgs created:            {counts['orgs_created']}")
    print(f"  Projects stamped:        {counts['projects_stamped']}")
    print(f"  OAuth connections:       {counts['connections_stamped']}")
    print(f"  Customer uploads:        {counts['uploads_stamped']}")
    print(f"  Customer locations:      {counts['locations_stamped']}")
    print(f"  Credit ledger rows:      {counts['ledger_rows_stamped']}")
    print("Done.")


if __name__ == "__main__":
    main()

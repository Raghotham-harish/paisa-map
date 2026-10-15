"""
apply_viewer_role.py — P5 round 2: let org_members.role and org_invites.role be 'viewer'.

Both tables carry a CHECK constraint limiting role to owner/admin/member. This
replaces every CHECK constraint on those tables that mentions `role` with one
that also allows 'viewer'. Postgres only (SQLite is local dev: its tables are
created fresh from the code's definitions). Idempotent: if 'viewer' is already
allowed it changes nothing. One transaction, so it either fully applies or not
at all. Prints constraint definitions only, never the connection string.

Run AFTER the code is deployed and BEFORE anyone is made a viewer (until then,
adding a viewer fails the old constraint):

    sudo bash -c 'set -a; . /etc/paisamap/db.env; set +a; cd /home/ubuntu/paisa-map; \
      exec venv-flask/bin/python3 paisamap-etl/db/apply_viewer_role.py'
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "etl"))

import _auth_db as A  # noqa: E402
from sqlalchemy import text  # noqa: E402

if not os.environ.get("DATABASE_URL"):
    sys.exit("DATABASE_URL is not set")

engine = A._require_engine()
if engine.dialect.name != "postgresql":
    sys.exit("Not Postgres — nothing to do (local SQLite tables come from the code's definitions).")

ALLOWED = "role IN ('owner','admin','member','viewer')"
TABLES = {"org_members": "ck_org_members_role", "org_invites": "ck_org_invites_role"}


def role_checks(conn, table):
    return conn.execute(text(
        "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = CAST(:t AS regclass) AND contype = 'c' "
        "AND pg_get_constraintdef(oid) ILIKE '%role%'"), {"t": table}).all()


with engine.begin() as conn:
    for table, name in TABLES.items():
        checks = role_checks(conn, table)
        for conname, definition in checks:
            print(f"  {table}: found {conname}: {definition}")
        if checks and all("viewer" in d for _, d in checks):
            print(f"  {table}: viewer already allowed — unchanged")
            continue
        for conname, _ in checks:
            conn.execute(text(f'ALTER TABLE {table} DROP CONSTRAINT "{conname}"'))
        conn.execute(text(f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({ALLOWED})"))
        print(f"  {table}: now {name}: CHECK ({ALLOWED})")

with engine.connect() as conn:
    for table in TABLES:
        defs = [d for _, d in role_checks(conn, table)]
        if not defs or not all("viewer" in d for d in defs):
            sys.exit(f"{table}: viewer is STILL not allowed: {defs}")
print("OK — org_members and org_invites accept role 'viewer'")

"""
apply_billing_v2_columns.py — adds the five nullable columns billing-v2 needs.

Standalone on purpose (SQLAlchemy only, imports nothing from this repo), so it
can be run BEFORE the new code is deployed — the new code's table definitions
select these columns, so they must exist first. Old code ignores them.

Idempotent: safe to run twice. Prints column names only, never the connection
string. Reads DATABASE_URL from the environment.

    sudo bash -c 'set -a; . /etc/paisamap/db.env; set +a; \
      exec /home/ubuntu/paisa-map/venv-flask/bin/python3 -' < apply_billing_v2_columns.py
"""
import os
import sys

from sqlalchemy import create_engine, inspect, text

COLUMNS = [
    ("orders", "org_id", "INTEGER"),
    ("orders", "billing_org_id", "INTEGER"),
    ("orders", "price_book_version", "TEXT"),
    ("credits_ledger", "billing_org_id", "INTEGER"),
    ("organizations", "billing_org_id", "INTEGER"),
]

url = os.environ.get("DATABASE_URL")
if not url:
    sys.exit("DATABASE_URL is not set")
engine = create_engine(url)
is_pg = engine.dialect.name == "postgresql"

with engine.begin() as conn:
    inspector = inspect(conn)
    for table, column, sql_type in COLUMNS:
        existing = {c["name"] for c in inspector.get_columns(table)}
        if column in existing:
            print(f"  already there: {table}.{column}")
            continue
        clause = "ADD COLUMN IF NOT EXISTS" if is_pg else "ADD COLUMN"
        conn.execute(text(f"ALTER TABLE {table} {clause} {column} {sql_type}"))
        print(f"  added:         {table}.{column} {sql_type}")

with engine.connect() as conn:
    inspector = inspect(conn)
    missing = [(t, c) for t, c, _ in COLUMNS
               if c not in {col["name"] for col in inspector.get_columns(t)}]
if missing:
    sys.exit(f"STILL MISSING: {missing}")
print("OK — all five columns present")

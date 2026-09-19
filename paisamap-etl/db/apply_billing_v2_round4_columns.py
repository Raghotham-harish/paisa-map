"""
apply_billing_v2_round4_columns.py — adds the three nullable columns round 4 needs
(company on API keys; website requests). Run it BEFORE the deploy that carries them.

Standalone on purpose (SQLAlchemy only, imports nothing from this repo), like
apply_billing_v2_columns.py: the new code's table definitions select these
columns, so they must exist first; old code ignores them.

Idempotent. Prints column names only, never the connection string.

    sudo bash -c 'set -a; . /etc/paisamap/db.env; set +a; \
      exec /home/ubuntu/paisa-map/venv-flask/bin/python3 -' < apply_billing_v2_round4_columns.py
"""
import os
import sys

from sqlalchemy import create_engine, inspect, text

COLUMNS = [
    ("api_keys", "org_id", "INTEGER"),
    ("credit_link_requests", "via", "TEXT DEFAULT 'email'"),
    ("credit_link_requests", "target_domain", "TEXT"),
]

url = os.environ.get("DATABASE_URL")
if not url:
    sys.exit("DATABASE_URL is not set")
engine = create_engine(url)
is_pg = engine.dialect.name == "postgresql"

with engine.begin() as conn:
    inspector = inspect(conn)
    for table, column, sql_type in COLUMNS:
        if table not in inspector.get_table_names():
            print(f"  skipped:       {table} doesn't exist yet (apply_budgets_table.py creates it)")
            continue
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
               if t in inspector.get_table_names()
               and c not in {col["name"] for col in inspector.get_columns(t)}]
if missing:
    sys.exit(f"STILL MISSING: {missing}")
print("OK — the round 4 columns are present")

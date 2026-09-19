"""
apply_budgets_table.py — creates the billing-v2 side tables (`credit_budgets`,
`credit_member_budgets`, `credit_link_requests`) and adds any of their columns
that are missing.

Needs the NEW code deployed (it uses the code's own table definitions, so the
tables can never drift from what the app expects). Only ever ADDS: it creates
missing tables, and adds missing NULLABLE columns to a table an earlier version
of this script already created. It never alters an existing column and never
drops anything, so it is safe to run any number of times, in any order relative
to deploys. Reads DATABASE_URL from the environment; prints table and column
names only, never the connection string.

    sudo bash -c 'set -a; . /etc/paisamap/db.env; set +a; cd /home/ubuntu/paisa-map; \
      exec venv-flask/bin/python3 paisamap-etl/db/apply_budgets_table.py'
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "etl"))

import _auth_db as A  # noqa: E402
from sqlalchemy import inspect, text  # noqa: E402

TABLES = ("credit_budgets", "credit_member_budgets", "credit_link_requests")

if not os.environ.get("DATABASE_URL"):
    sys.exit("DATABASE_URL is not set")

engine = A._require_engine()
tables = A._get_tables()

for name in TABLES:
    table = tables[name]
    if not inspect(engine).has_table(name):
        table.create(engine)
        print(f"  created:       {name}")
    else:
        have = {c["name"] for c in inspect(engine).get_columns(name)}
        added = False
        for col in table.columns:
            if col.name in have:
                continue
            if not col.nullable and col.server_default is None:
                sys.exit(f"{name}.{col.name} is missing and NOT NULL without a default — refusing to guess; tell Claude")
            sql_type = col.type.compile(dialect=engine.dialect)
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {name} ADD COLUMN {col.name} {sql_type}"))
            print(f"  added column:  {name}.{col.name} {sql_type}")
            added = True
        if not added:
            print(f"  already there: {name}")

for name in TABLES:
    if not inspect(engine).has_table(name):
        sys.exit(f"{name} is STILL MISSING")
    have = {c["name"] for c in inspect(engine).get_columns(name)}
    want = {c.name for c in tables[name].columns}
    if have != want:
        sys.exit(f"{name} exists but its columns differ: missing {sorted(want - have)}, extra {sorted(have - want)}")
print(f"OK — {len(TABLES)} billing tables present, all columns match")

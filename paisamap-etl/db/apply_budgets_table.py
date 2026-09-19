"""
apply_budgets_table.py — creates the `credit_budgets` table (billing-v2 budgets).

Needs the NEW code deployed (it uses the code's own table definition, so the
table can never drift from what the app expects). Creates only what is missing —
`create_all` skips tables that already exist — so it is safe to run twice, and
it never alters or drops anything. Reads DATABASE_URL from the environment;
prints table and column names only, never the connection string.

    sudo bash -c 'set -a; . /etc/paisamap/db.env; set +a; cd /home/ubuntu/paisa-map; \
      exec venv-flask/bin/python3 paisamap-etl/db/apply_budgets_table.py'
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "etl"))

import _auth_db as A  # noqa: E402
from sqlalchemy import inspect  # noqa: E402

if not os.environ.get("DATABASE_URL"):
    sys.exit("DATABASE_URL is not set")

engine = A._require_engine()
table = A._get_tables()["credit_budgets"]
existed = inspect(engine).has_table("credit_budgets")
table.create(engine, checkfirst=True)

if not inspect(engine).has_table("credit_budgets"):
    sys.exit("credit_budgets is STILL MISSING")
have = {c["name"] for c in inspect(engine).get_columns("credit_budgets")}
want = {c.name for c in table.columns}
if have != want:
    sys.exit(f"credit_budgets exists but its columns differ: missing {sorted(want - have)}, extra {sorted(have - want)}")
print("  already there: credit_budgets" if existed else "  created:       credit_budgets")
print(f"OK — credit_budgets present with {len(want)} columns")

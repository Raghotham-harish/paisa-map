"""
apply_billing_v2_round4.py — the schema for company-attributed API keys, website
verification and connect-by-website. Idempotent; only ever ADDS.

  1. adds the nullable columns  api_keys.org_id,
     credit_link_requests.via / .target_domain  (if missing)
  2. creates the  org_domains  table (if missing)
  3. gives every existing API key the company of the person who made it
     (their primary company, only where they are a member of it) so it shows up
     in that company's admin view — a key with no company keeps working exactly
     as before either way
  4. checks that every table's columns match what the code expects

Needs the NEW code deployed (it uses the code's own table definitions). Safe in
either order relative to the deploy: until it has run, the API-key lookup falls
back to the old query and the new pages simply show empty states.

To add the three columns BEFORE deploying instead (recommended — no errors on the API-keys page in
between), run the standalone paisamap-etl/db/apply_billing_v2_round4_columns.py first (see the runbook,
step 5c); this script then only creates the table and does the backfill.

Reads DATABASE_URL from the environment; prints table/column names and counts
only — never the connection string.

    sudo bash -c 'set -a; . /etc/paisamap/db.env; set +a; cd /home/ubuntu/paisa-map; \
      exec venv-flask/bin/python3 paisamap-etl/db/apply_billing_v2_round4.py'
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "etl"))

import _auth_db as A  # noqa: E402
from sqlalchemy import inspect, text  # noqa: E402

NEW_COLUMNS = (("api_keys", "org_id"), ("credit_link_requests", "via"), ("credit_link_requests", "target_domain"))
NEW_TABLES = ("org_domains",)
CHECK = ("api_keys", "credit_link_requests", "org_domains")

if not os.environ.get("DATABASE_URL"):
    sys.exit("DATABASE_URL is not set")

engine = A._require_engine()
tables = A._get_tables()

for name in NEW_TABLES:
    if not inspect(engine).has_table(name):
        tables[name].create(engine)
        print(f"  created table: {name}")
    else:
        print(f"  already there: {name}")

for table, col in NEW_COLUMNS:
    have = {c["name"] for c in inspect(engine).get_columns(table)}
    if col in have:
        print(f"  already there: {table}.{col}")
        continue
    c = tables[table].c[col]
    sql_type = c.type.compile(dialect=engine.dialect)
    default = " DEFAULT 'email'" if col == "via" else ""
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {sql_type}{default}"))
    print(f"  added column:  {table}.{col} {sql_type}{default}")

with engine.begin() as conn:
    # Rows written before the column existed read as 'email' (the only kind there was).
    conn.execute(text("UPDATE credit_link_requests SET via = 'email' WHERE via IS NULL"))
    done = conn.execute(text(
        "UPDATE api_keys SET org_id = ("
        "  SELECT u.org_id FROM users u WHERE u.id = api_keys.user_id AND EXISTS ("
        "    SELECT 1 FROM org_members m WHERE m.org_id = u.org_id AND m.user_id = u.id)) "
        "WHERE org_id IS NULL"))
    left = conn.execute(text("SELECT COUNT(*) FROM api_keys WHERE org_id IS NULL")).scalar()
    total = conn.execute(text("SELECT COUNT(*) FROM api_keys")).scalar()
print(f"  api keys: {total} total, {left} still without a company (their maker has no company they belong to — they keep working as before)")

for name in CHECK:
    have = {c["name"] for c in inspect(engine).get_columns(name)}
    want = {c.name for c in tables[name].columns}
    if have != want:
        sys.exit(f"{name} columns differ: missing {sorted(want - have)}, extra {sorted(have - want)}")
print("OK — company-attributed keys, website verification and website requests are ready")

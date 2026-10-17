"""
apply_addon_subscriptions.py — P5 round 3: self-serve extra seats / companies.

Two steps, in this order:
  1. Postgres only: let orders.kind be 'addon' (each add-on charge is recorded as
     an order, so it gets an invoice). Replaces every CHECK constraint on orders
     that mentions `kind` with one that also allows 'addon'.
  2. Creates `addon_subscriptions` (from the code's own definition).

The app treats the add-on feature as off until the table exists, so the table
is created LAST: once it is there, an add-on charge can always be recorded.
Idempotent; only ever widens a constraint and adds a table. Prints names and
constraint definitions only, never the connection string.

Run AFTER the code is deployed:

    sudo bash -c 'set -a; . /etc/paisamap/db.env; set +a; cd /home/ubuntu/paisa-map; \
      exec venv-flask/bin/python3 paisamap-etl/db/apply_addon_subscriptions.py'
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "etl"))

import _auth_db as A  # noqa: E402
from sqlalchemy import inspect, text  # noqa: E402

if not os.environ.get("DATABASE_URL"):
    sys.exit("DATABASE_URL is not set")

engine = A._require_engine()
tables = A._get_tables()
ALLOWED = "kind IN ('credit_pack','plan_upgrade','report_purchase','addon')"


def kind_checks(conn):
    return conn.execute(text(
        "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = CAST('orders' AS regclass) AND contype = 'c' "
        "AND pg_get_constraintdef(oid) ILIKE '%kind%'")).all()


if engine.dialect.name == "postgresql":
    with engine.begin() as conn:
        checks = kind_checks(conn)
        for conname, definition in checks:
            print(f"  orders: found {conname}: {definition}")
        if checks and all("addon" in d for _, d in checks):
            print("  orders: 'addon' already allowed — unchanged")
        else:
            for conname, _ in checks:
                conn.execute(text(f'ALTER TABLE orders DROP CONSTRAINT "{conname}"'))
            conn.execute(text(f"ALTER TABLE orders ADD CONSTRAINT ck_orders_kind CHECK ({ALLOWED})"))
            print(f"  orders: now ck_orders_kind: CHECK ({ALLOWED})")
    with engine.connect() as conn:
        defs = [d for _, d in kind_checks(conn)]
        if not defs or not all("addon" in d for d in defs):
            sys.exit(f"orders: 'addon' is STILL not allowed: {defs} — table NOT created")
else:
    print("  not Postgres — orders constraint comes from the code's definition")

name = "addon_subscriptions"
if inspect(engine).has_table(name):
    have = {c["name"] for c in inspect(engine).get_columns(name)}
    want = {c.name for c in tables[name].columns}
    if have != want:
        sys.exit(f"{name} exists but its columns differ: missing {sorted(want - have)}, extra {sorted(have - want)}")
    print(f"  already there: {name}")
else:
    tables[name].create(engine)
    print(f"  created:       {name}")
print("OK — orders accept 'addon', addon_subscriptions present")

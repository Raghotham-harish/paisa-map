"""
billing_cutover.py — inspect, and (deliberately) prepare, the credits cutover.

    billing_cutover.py report   read-only: would flipping BILLING_SCOPE=wallet
                                change anyone's visible balance or plan? Lists
                                every problem that must be fixed first.
    billing_cutover.py apply    the two one-time data steps, then the report:
                                  1. backfill_organizations()  (stamp company +
                                     wallet on every legacy ledger row)
                                  2. link_extra_companies_to_primary()  (merge
                                     each extra company into its owner's wallet
                                     — the same shared pool they had before)
                                Idempotent. WRITES to the database.

Needs the NEW code deployed (and the five columns added). Reads DATABASE_URL
from the environment; prints ids and numbers only — no emails, no secrets.

    sudo bash -c 'set -a; . /etc/paisamap/db.env; set +a; cd /home/ubuntu/paisa-map; \
      exec venv-flask/bin/python3 paisamap-etl/db/billing_cutover.py report'
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "etl"))

import _auth_db as A  # noqa: E402
from sqlalchemy import select  # noqa: E402


def visible_changes():
    """Every user whose balance or plan would read differently under
    BILLING_SCOPE=wallet than under the current per-user scope."""
    users = A._get_tables()["users"]
    with A._require_engine().connect() as conn:
        ids = [r[0] for r in conn.execute(select(users.c.id).order_by(users.c.id))]
    saved = os.environ.get("BILLING_SCOPE")
    out = []
    try:
        for uid in ids:
            os.environ["BILLING_SCOPE"] = "user"
            before = (A.get_credit_balance(uid), A.get_effective_plan_for_user(uid))
            os.environ["BILLING_SCOPE"] = "wallet"
            after = (A.get_credit_balance(uid), A.get_effective_plan_for_user(uid))
            if before != after:
                out.append({"user_id": uid, "balance_now": before[0], "balance_after_flip": after[0],
                            "plan_now": before[1], "plan_after_flip": after[1]})
    finally:
        if saved is None:
            os.environ.pop("BILLING_SCOPE", None)
        else:
            os.environ["BILLING_SCOPE"] = saved
    return ids, out


def report():
    pre = A.wallet_mode_preflight()
    ids, changes = visible_changes()
    print(json.dumps({
        "ready_to_flip": pre["ok"],
        "blocking_problems": pre["problems"],
        "users_checked": len(ids),
        "users_whose_balance_or_plan_would_change": changes,
        "informational": pre["info"],
    }, indent=2, default=str))
    return pre["ok"]


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if not os.environ.get("DATABASE_URL"):
        sys.exit("DATABASE_URL is not set")
    if mode == "report":
        sys.exit(0 if report() else 1)
    if mode == "apply":
        print("backfill_organizations:", A.backfill_organizations())
        print("link_extra_companies_to_primary:", A.link_extra_companies_to_primary())
        sys.exit(0 if report() else 1)
    sys.exit(__doc__)

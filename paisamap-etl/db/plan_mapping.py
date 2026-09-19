"""
plan_mapping.py — map legacy accounts ('pro' / 'team') onto v2 tiers.

    plan_mapping.py report   [--override EMAIL=TIER ...]   read-only: what WOULD change
    plan_mapping.py apply    [--override EMAIL=TIER ...] [--allow-downgrade] [--yes]
                             without --yes it is only a preview; with --yes it WRITES
    plan_mapping.py rollback [--yes]                        put back what apply changed

It sets organizations.plan on the company each such account owns (never
users.plan), so it changes nothing anyone can see until BILLING_SCOPE=wallet is
on — and even then it can only raise an account, never lower it. Default tier =
the LOWEST v2 tier that keeps everything the account can do today (Growth for
both legacy 'pro' and 'team'); use --override for anything else, e.g. the
owner's own account:

    --override you@example.com=pro

Reads DATABASE_URL from the environment; prints ids and tier names only — no
secrets. Needs the NEW code deployed.

    sudo bash -c 'set -a; . /etc/paisamap/db.env; set +a; cd /home/ubuntu/paisa-map; \
      exec venv-flask/bin/python3 paisamap-etl/db/plan_mapping.py report'
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "etl"))

import _auth_db as A  # noqa: E402
import _plan_mapping as M  # noqa: E402


def parse_overrides(pairs):
    out = {}
    for p in pairs or []:
        if "=" not in p:
            sys.exit(f"--override wants EMAIL=TIER, got {p!r}")
        email, tier = p.rsplit("=", 1)
        out[email.strip().lower()] = tier.strip().lower()
    return out


def show(rows):
    if not rows:
        print("  (nothing)")
    for r in rows:
        line = (f"  company {r['org_id']:>5}  owner {r['owner_user_id']}  {r['members']} member(s)  "
                f"{r['legacy_plan']:<5} -> {r.get('proposed_label') or '-':<10}")
        if r.get("override"):
            line += "  [override]"
        if r.get("reason"):
            line += f"  ({r['reason']})"
        print(line)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("report", "apply", "rollback"))
    ap.add_argument("--override", action="append", metavar="EMAIL=TIER")
    ap.add_argument("--allow-downgrade", action="store_true")
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()
    if not os.environ.get("DATABASE_URL"):
        sys.exit("DATABASE_URL is not set")
    overrides = parse_overrides(args.override)

    if args.mode == "rollback":
        res = M.rollback(A, dry_run=not args.yes)
        print(("ROLLED BACK" if args.yes else "PREVIEW (add --yes to write)") + f": {len(res['restored'])} restored, {len(res['left_alone'])} left alone")
        for r in res["restored"]:
            print(f"  company {r['org_id']}: {r['from']} -> {r['back_to']}")
        for r in res["left_alone"]:
            print(f"  company {r['org_id']}: left alone (plan is now {r['current']}, not {r['from']})")
        return

    res = M.apply(A, overrides, args.allow_downgrade, dry_run=(args.mode == "report" or not args.yes))
    for e in res["errors"]:
        print("PROBLEM:", e)
    wrote = args.mode == "apply" and args.yes
    print(("APPLIED" if wrote else "PREVIEW — nothing has been written") + f"  ({len(res['mapped'])} to map, {len(res['unchanged'])} already v2, "
          f"{len(res['blocked'])} blocked, {len(res['skipped'])} skipped)")
    print("Will map" if not wrote else "Mapped"); show(res["mapped"])
    if res["blocked"]:
        print("BLOCKED (would give the account LESS than it has today)"); show(res["blocked"])
    if res["skipped"]:
        print("Skipped"); show(res["skipped"])
    if res["changed_underneath"]:
        print("Changed while running — left alone, run again"); show(res["changed_underneath"])
    if res["unchanged"]:
        print("Already on a v2 tier"); show(res["unchanged"])
    if not wrote:
        print("\nTo write this: rerun as `apply --yes` (with the same --override flags).")
    if res["blocked"] or res["errors"]:
        sys.exit(1)


if __name__ == "__main__":
    main()

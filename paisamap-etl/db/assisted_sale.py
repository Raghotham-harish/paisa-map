"""
assisted_sale.py — record a sales-assisted plan (an invoice paid by NEFT/UPI/cheque
instead of Razorpay): sets the company's plan for N months, adds its credits, and
writes the audit row. Billing-v2 P12 rail; see paisamap-etl/etl/_assisted_sale.py.

Every command is a DRY RUN until you add --yes: it prints exactly what would change
(plan before/after, credits, wallet balance before/after, the period) and touches
nothing. There is deliberately no web endpoint for this.

    # once, on the server (this is the shape every command below takes):
    ASALE() { sudo bash -c 'set -a; . /etc/paisamap/db.env; set +a; cd /home/ubuntu/paisa-map; \\
        exec venv-flask/bin/python3 paisamap-etl/db/assisted_sale.py "$@"' _ "$@"; }

    ASALE grant --owner-email buyer@company.com --tier scale --months 12 \\
        --invoice PM/2026-27/001 --amount 300000 --method neft --ref UTR123456789
    ASALE grant ... --yes                     # ...the same, applied
    ASALE grant --org-id 42 --tier growth --months 3 --method complimentary \\
        --invoice PILOT-001 --amount 0 --note "3-month design-partner pilot" --yes
    ASALE list [--org-id 42] [--status active]
    ASALE revoke --sale-id 7 --reason "refunded, invoice cancelled" --yes

--amount is RUPEES actually received (0 for complimentary). --credits defaults to the
tier's monthly credits x months. Reads DATABASE_URL from the environment and never
prints it. Exit code 0 = ok / dry run ok, 1 = refused, 2 = bad usage.
"""
import argparse
import getpass
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "etl"))

import _assisted_sale as AS  # noqa: E402
import _auth_db as A  # noqa: E402
import _pricing  # noqa: E402


def _operator():
    return os.environ.get("SUDO_USER") or os.environ.get("USER") or getpass.getuser()


def _fmt_dt(dt):
    dt = AS.S._aware(dt)
    return dt.strftime("%d %b %Y") if dt else "-"


def _rupees(paise):
    return f"Rs {paise / 100:,.2f}"


def _die(res, code=1):
    print(f"REFUSED [{res['error']}]: {res['detail']}")
    sys.exit(code)


def cmd_grant(a):
    org_id = a.org_id
    if org_id is None:
        org_id = AS.find_org_by_email(a.owner_email)
        if org_id is None:
            print(f"REFUSED: no user with email {a.owner_email!r} (or they have no company yet)")
            sys.exit(1)
    amount = AS.rupees_to_paise(a.amount)
    if amount is None:
        print("REFUSED: --amount must be a non-negative number of rupees with at most 2 decimals")
        sys.exit(2)
    res = AS.grant(org_id, a.tier, months=a.months, credits=a.credits, invoice_number=a.invoice,
                   amount_paise=amount, payment_method=a.method, payment_ref=a.ref,
                   operator=_operator(), notes=a.note, dry_run=not a.yes)
    if "error" in res:
        _die(res)
    s = res["summary"]
    print(("APPLIED" if a.yes else "DRY RUN (nothing changed; add --yes to apply)") + ":")
    print(f"  company        #{s['org_id']}  {s['org_name']}")
    print(f"  plan           {s['plan_before']}  ->  {s['plan_after']}  ({s['tier_label']})")
    print(f"  period         {_fmt_dt(s['period_start'])}  to  {_fmt_dt(s['period_end'])}"
          f"  ({s['months']} month{'s' if s['months'] != 1 else ''}{', stacked after the current period' if s['stacked'] else ''})")
    print(f"  credits        +{s['credits']}   (wallet {s['wallet_balance_before']} -> {s['wallet_balance_after']})")
    print(f"  received       {_rupees(s['amount_paise'])} by {s['payment_method']}"
          f"{' ref ' + s['payment_ref'] if s['payment_ref'] else ''}")
    print(f"  invoice        {s['invoice_number']}")
    if a.yes:
        print(f"  audit row      assisted sale #{res['sale']['id']} (granted by {res['sale']['granted_by']})")
        print("  reminder       the plan ends on its own at the period end - make sure the hourly "
              "subscription_sweep.py cron is installed.")


def cmd_revoke(a):
    res = AS.revoke(a.sale_id, reason=a.reason, operator=_operator(), dry_run=not a.yes)
    if "error" in res:
        _die(res)
    sale = res["sale"]
    print(("REVOKED" if a.yes else "DRY RUN (nothing changed; add --yes to apply)") + ":")
    print(f"  sale #{sale['id']}  company #{sale['org_id']}  {sale['tier']}  invoice {sale['invoice_number']}")
    print(f"  the plan is handed to whatever else still entitles the company, or ends now")
    print(f"  credits ALREADY granted ({sale['credits']}) are NOT taken back - adjust separately if needed")


def cmd_list(a):
    rows = AS.list_sales(org_id=a.org_id, status=a.status)
    if not rows:
        print("no assisted sales")
        return
    print(f"{'#':>4}  {'company':>7}  {'tier':<10} {'status':<8} {'period':<25} {'credits':>7}  {'received':>14}  invoice")
    for r in rows:
        print(f"{r['id']:>4}  {r['org_id']:>7}  {r['tier']:<10} {r['status']:<8} "
              f"{_fmt_dt(r['period_start']) + ' - ' + _fmt_dt(r['period_end']):<25} {r['credits']:>7}  "
              f"{_rupees(r['amount_paise']):>14}  {r['invoice_number']}")


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("grant", help="record a sale (dry run unless --yes)")
    who = g.add_mutually_exclusive_group(required=True)
    who.add_argument("--org-id", type=int)
    who.add_argument("--owner-email")
    g.add_argument("--tier", required=True, choices=[t for t in _pricing.TIERS if _pricing.TIERS[t]["price_paise"]])
    g.add_argument("--months", type=int, default=1)
    g.add_argument("--credits", type=int, help="default: the tier's monthly credits x months")
    g.add_argument("--invoice", required=True, help="the EXTERNAL invoice number (unique; re-running is safe)")
    g.add_argument("--amount", required=True, help="rupees actually received (0 for complimentary)")
    g.add_argument("--method", required=True, choices=list(AS.METHODS))
    g.add_argument("--ref", help="payment reference: UTR / cheque number (required unless complimentary)")
    g.add_argument("--note")
    g.add_argument("--yes", action="store_true", help="apply (otherwise a dry run)")
    g.set_defaults(fn=cmd_grant)
    r = sub.add_parser("revoke", help="end a sale now (dry run unless --yes)")
    r.add_argument("--sale-id", type=int, required=True)
    r.add_argument("--reason", required=True)
    r.add_argument("--yes", action="store_true")
    r.set_defaults(fn=cmd_revoke)
    l = sub.add_parser("list", help="list recorded sales")
    l.add_argument("--org-id", type=int)
    l.add_argument("--status", choices=["active", "expired", "revoked"])
    l.set_defaults(fn=cmd_list)
    args = p.parse_args()

    if not os.environ.get("DATABASE_URL"):
        sys.exit("DATABASE_URL is not set")
    if not AS.S.tables_ready():
        print("REFUSED: the billing tables aren't applied yet - run paisamap-etl/db/apply_subscriptions_tables.py first")
        sys.exit(1)
    args.fn(args)


if __name__ == "__main__":
    main()

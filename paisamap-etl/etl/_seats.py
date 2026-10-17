"""
_seats.py — P5: seat and company limits per paying account (pricing doc §5, decisions #1, #10, #15).

A paying account (wallet) is one company plus every company that draws on it
(`organizations.billing_org_id`). Its tier comes from the wallet company's
`organizations.plan` (a v2 plan id). Limits are the tier's `seats` / `companies`
plus anything bought on top (`wallet_addons`).

Counting (owner decisions, 15 Oct 2026):
  - seats are counted across the WHOLE account: a person in two of its companies
    is one seat;
  - a PENDING invite holds a seat until it is accepted, revoked or expires;
  - viewers never use a seat (no viewer role exists yet; the count already
    skips one so it can be added without touching this file).

Never removes anyone: an account already over its limit (a downgrade, or limits
switched on after the fact) keeps everyone and only stops ADDING people or
companies until it is back under, or buys more.

Enforced only with SEAT_LIMITS=1 (switched on with subscriptions, runbook S3)
and only for v2 dashboard tiers; free and legacy accounts are not limited here.
"""
import os

FREE_ROLES = ("viewer",)


def enforced() -> bool:
    return os.environ.get("SEAT_LIMITS") == "1"


def _A():
    import _auth_db
    return _auth_db


def _addons(conn, wallet_id):
    A = _A()
    if not A._has_budget_table(conn, "wallet_addons"):
        return 0, 0
    t = A._get_tables()["wallet_addons"]
    from sqlalchemy import select
    row = conn.execute(select(t.c.extra_seats, t.c.extra_companies)
                       .where(t.c.wallet_org_id == wallet_id)).first()
    return (int(row[0]), int(row[1])) if row else (0, 0)


def _wallet_tier(conn, wallet_id):
    """('starter', TIERS row) for a v2 dashboard tier, else (None, None)."""
    import _pricing
    A = _A()
    from sqlalchemy import select
    orgs = A._get_tables()["organizations"]
    plan = conn.execute(select(orgs.c.plan).where(orgs.c.id == wallet_id)).scalar()
    kind, name = _pricing.parse_plan(plan or "free")
    if kind != "v2" or name not in _pricing.TIERS or _pricing.is_signal_tier(name):
        return None, None
    return name, _pricing.TIERS[name]


def limits(conn, wallet_id):
    """{'tier', 'seats', 'companies', 'extra_seats', 'extra_companies',
    'extra_seat_paise', 'extra_company_paise'} — seats/companies None means
    unlimited. None when this account isn't limited at all."""
    if not enforced() or wallet_id is None:
        return None
    name, row = _wallet_tier(conn, wallet_id)
    if row is None:
        return None
    xs, xc = _addons(conn, wallet_id)
    seats = None if row["seats"] is None else row["seats"] + xs
    companies = None if row["companies"] is None else row["companies"] + xc
    return {"tier": name, "seats": seats, "companies": companies,
            "extra_seats": xs, "extra_companies": xc,
            "extra_seat_paise": row["extra_seat_paise"], "extra_company_paise": row["extra_company_paise"]}


def account_org_ids(conn, wallet_id):
    A = _A()
    from sqlalchemy import select, or_
    orgs = A._get_tables()["organizations"]
    return [r[0] for r in conn.execute(
        select(orgs.c.id).where(or_(orgs.c.id == wallet_id, orgs.c.billing_org_id == wallet_id)))]


def _seat_holders(conn, org_ids):
    """(user ids holding a seat, emails of pending invites holding one) across
    `org_ids`. An invite for someone who already holds a seat adds nothing."""
    A = _A()
    from sqlalchemy import select
    t = A._get_tables()
    m, inv, users = t["org_members"], t["org_invites"], t["users"]
    if not org_ids:
        return set(), set()
    user_ids = {r[0] for r in conn.execute(
        select(m.c.user_id).where(m.c.org_id.in_(org_ids), m.c.role.notin_(FREE_ROLES)))}
    member_emails = {(r[0] or "").lower() for r in conn.execute(
        select(users.c.email).where(users.c.id.in_(user_ids)))} if user_ids else set()
    now = A._now()
    emails = set()
    for email, expires in conn.execute(
            select(inv.c.email, inv.c.expires_at)
            .where(inv.c.org_id.in_(org_ids), inv.c.status == "pending", inv.c.role.notin_(FREE_ROLES))):
        e = (email or "").lower()
        if e and e not in member_emails and A._ensure_aware(expires) >= now:
            emails.add(e)
    return user_ids, emails


def usage(conn, wallet_id):
    users, invites = _seat_holders(conn, account_org_ids(conn, wallet_id))
    return {"seats": len(users) + len(invites), "members": len(users), "pending_invites": len(invites),
            "companies": len(account_org_ids(conn, wallet_id))}


def _over(kind, lim, used):
    key = "seats" if kind == "seat" else "companies"
    out = {"error": f"{kind}_limit", "limit": lim[key], "used": used, "tier": lim["tier"]}
    out["extra_paise"] = lim["extra_seat_paise"] if kind == "seat" else lim["extra_company_paise"]
    return out


def check_new_seat(conn, org_id, *, user_id=None, email=None, role="member", ignore_invite_email=None):
    """None if `user_id` / `email` may take a seat in `org_id`'s account, else
    {'error': 'seat_limit', ...}. Someone already holding a seat in the account
    (or invited to it) needs no new one. `ignore_invite_email`: the invite being
    accepted right now, which is converting rather than adding."""
    if role in FREE_ROLES:
        return None
    wallet = _A()._payer_org_id(conn, org_id)
    lim = limits(conn, wallet)
    if lim is None or lim["seats"] is None:
        return None
    users, invites = _seat_holders(conn, account_org_ids(conn, wallet))
    if ignore_invite_email:
        invites.discard(ignore_invite_email.lower())
    if user_id is not None and user_id in users:
        return None
    if email and email.lower() in invites:
        return None
    used = len(users) + len(invites)
    return None if used < lim["seats"] else _over("seat", lim, used)


def check_new_company(conn, payer_id):
    """None if one more company may draw on `payer_id`'s account."""
    lim = limits(conn, payer_id)
    if lim is None or lim["companies"] is None:
        return None
    used = len(account_org_ids(conn, payer_id))
    return None if used < lim["companies"] else _over("company", lim, used)


def check_link(conn, org_id, payer_id):
    """Linking `org_id` under `payer_id` adds a company AND brings its people
    into the payer's seat count."""
    err = check_new_company(conn, payer_id)
    if err:
        return err
    lim = limits(conn, payer_id)
    if lim is None or lim["seats"] is None:
        return None
    users, invites = _seat_holders(conn, account_org_ids(conn, payer_id))
    add_users, add_invites = _seat_holders(conn, [org_id])
    used = len(users | add_users) + len(invites | add_invites)
    if used > lim["seats"]:
        out = _over("seat", lim, len(users) + len(invites))
        out["would_use"] = used
        return out
    return None


def view_for(org_id):
    """view() for the account `org_id` draws on, on its own connection."""
    A = _A()
    with A._require_engine().connect() as conn:
        return view(conn, A._payer_org_id(conn, org_id))


def view(conn, wallet_id):
    """What the Billing / Members pages show: limits + usage, or None."""
    lim = limits(conn, wallet_id)
    if lim is None:
        return None
    return {**lim, **{"used_" + k: v for k, v in usage(conn, wallet_id).items()}}

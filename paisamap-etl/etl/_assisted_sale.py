"""
_assisted_sale.py — billing-v2: sales-assisted plans (PRICING §7 / P12 rail).

Scale and above, anything annual, and pilots can't be sold through self-serve
Razorpay subscriptions (the RBI e-mandate cap makes silent autopay impossible above
Rs 15,000/cycle). Those customers pay against an invoice by NEFT/UPI/cheque, and an
operator then records the sale here: a plan for N months plus credits, against the
external invoice number and payment reference. The `assisted_sales` row is the audit
trail. There is deliberately NO web endpoint that can do this — it is run on the
server by the operator through paisamap-etl/db/assisted_sale.py (dry-run by default).

Rules that matter:
  * One invoice number can be recorded once (unique), so re-running a command can
    never grant twice.
  * The plan goes on the PAYING company (the wallet), like a subscription's; a company
    that is linked under a payer is refused with the payer's id.
  * A company with a live paid self-serve subscription is refused (double billing), and
    a company already holding a HIGHER v2 plan is refused (never downgrade by accident).
  * Repeat sales of the same tier stack: the new period starts when the current one ends.
  * Access ends at period_end (the sweep expires it); a plan is only ever revoked when
    it is still exactly this sale's tier, and it is handed to whatever else still
    entitles the company (another active sale, or a paid subscription) rather than
    revoked from under it.
  * Credits are granted once, at grant time, and are NOT clawed back on revoke — that is
    a deliberate, separate decision for the operator (the CLI says so).
"""

import calendar
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import _auth_db as A
import _pricing
import _subscriptions as S

log = logging.getLogger("paisamap.assisted_sale")

METHODS = ("neft", "rtgs", "imps", "upi", "cheque", "cash", "complimentary", "other")
MAX_MONTHS = 36
MAX_CREDITS = 1_000_000


def _now():
    return A._now()


def add_months(dt, months):
    """`dt` plus whole calendar months, clamping the day (31 Jan + 1 month = 28/29 Feb)."""
    m = dt.month - 1 + months
    year, month = dt.year + m // 12, m % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def rupees_to_paise(text):
    """'12000' / '12,000.50' -> paise (int). None if unusable or negative."""
    try:
        value = Decimal(str(text).replace(",", "").strip())
    except (InvalidOperation, AttributeError):
        return None
    if value < 0 or value != value.quantize(Decimal("0.01")):
        return None
    return int(value * 100)


def _err(code, detail, **extra):
    return {"error": code, "detail": detail, **extra}


def _row(r):
    return dict(r) if r else None


# ── Lookups ──────────────────────────────────────────────────────────────────
def find_org_by_email(email):
    """A user's own (primary) company by their email, or None."""
    users = A._get_tables()["users"]
    from sqlalchemy import select
    with A._require_engine().connect() as conn:
        return conn.execute(select(users.c.org_id).where(
            users.c.email == (email or "").strip().lower())).scalar()


def _org(conn, org_id):
    orgs = A._get_tables()["organizations"]
    from sqlalchemy import select
    return conn.execute(select(orgs.c.id, orgs.c.name, orgs.c.plan, orgs.c.owner_user_id,
                               orgs.c.billing_org_id).where(orgs.c.id == org_id)).mappings().first()


def _active(conn, org_id, now, exclude_id=None):
    sales = A._get_tables()["assisted_sales"]
    from sqlalchemy import select
    q = select(sales).where(sales.c.org_id == org_id, sales.c.status == "active", sales.c.period_end > now)
    if exclude_id is not None:
        q = q.where(sales.c.id != exclude_id)
    return [dict(r) for r in conn.execute(q.order_by(sales.c.period_end)).mappings().all()]


def _paid_live_subscription(conn, org_id):
    subs = A._get_tables()["subscriptions"]
    from sqlalchemy import select
    return conn.execute(select(subs.c.id).where(
        subs.c.org_id == org_id, subs.c.status.in_(S.LIVE_STATUSES), subs.c.paid_count >= 1)).scalar() is not None


def active_for_org(org_id, now=None):
    """The company's current assisted sale (the one that ends last), or None."""
    with A._require_engine().connect() as conn:
        rows = _active(conn, org_id, now or _now())
    return rows[-1] if rows else None


def public_view(sale):
    """What the Billing page may show a customer about a plan managed directly."""
    if not sale:
        return None
    tier = sale["tier"]
    return {"tier": tier, "label": _pricing.TIERS[tier]["label"] if tier in _pricing.TIERS else tier,
            "period_start": S._iso(sale["period_start"]), "period_end": S._iso(sale["period_end"])}


def list_sales(org_id=None, status=None, limit=50):
    sales = A._get_tables()["assisted_sales"]
    from sqlalchemy import select
    q = select(sales)
    if org_id is not None:
        q = q.where(sales.c.org_id == org_id)
    if status:
        q = q.where(sales.c.status == status)
    with A._require_engine().connect() as conn:
        return [dict(r) for r in conn.execute(q.order_by(sales.c.id.desc()).limit(limit)).mappings().all()]


def get(sale_id):
    sales = A._get_tables()["assisted_sales"]
    from sqlalchemy import select
    with A._require_engine().connect() as conn:
        return _row(conn.execute(select(sales).where(sales.c.id == sale_id)).mappings().first())


# ── Plan hand-over (inside the caller's transaction) ─────────────────────────
def _best_remaining_tier(rows):
    if not rows:
        return None
    return max((r["tier"] for r in rows), key=lambda t: _pricing.TIER_ORDER.index(t))


def release_plan(conn, org_id, tier, now, exclude_id=None):
    """A sale (or subscription) of `tier` no longer entitles `org_id`. Hand the plan to
    whatever still does instead of revoking it from under them:
      - another active assisted sale -> lower the plan to the best remaining tier;
      - a paid live subscription     -> leave it (the subscription owns the plan now);
      - nothing                      -> back to 'free'.
    Every write is conditional on the plan still being exactly this tier's, so a plan
    someone assigned in the meantime is never touched. Returns True if the plan changed."""
    orgs = A._get_tables()["organizations"]
    current = _pricing.plan_id_v2(tier)
    remaining = _active(conn, org_id, now, exclude_id=exclude_id)
    best = _best_remaining_tier(remaining)
    if best is not None:
        if best == tier:
            return False
        res = conn.execute(orgs.update().where(orgs.c.id == org_id, orgs.c.plan == current)
                           .values(plan=_pricing.plan_id_v2(best)))
        return res.rowcount > 0
    if _paid_live_subscription(conn, org_id):
        return False
    return S._revoke_plan(conn, org_id, tier)


def fallback_tier(conn, org_id, now):
    """For the subscription sweep: the tier an active assisted sale still entitles
    the company to, or None."""
    return _best_remaining_tier(_active(conn, org_id, now))


# ── Grant ────────────────────────────────────────────────────────────────────
def grant(org_id, tier, *, months=1, credits=None, invoice_number, amount_paise, payment_method,
          payment_ref=None, operator, notes=None, dry_run=True, now=None):
    """Record an assisted sale: set the company's plan, add its credits, write the audit
    row — all in one transaction. dry_run (the default) validates and returns exactly what
    WOULD happen without changing anything. Returns {"ok": True, "dry_run": bool, ...} or
    {"error": <code>, "detail": ...}."""
    now = now or _now()
    if not S.tables_ready():
        return _err("tables_missing", "the billing tables aren't applied yet (run apply_subscriptions_tables.py)")
    if tier not in _pricing.TIERS or not _pricing.TIERS[tier]["price_paise"]:
        return _err("invalid_tier", f"tier must be one of {[t for t in _pricing.TIERS if _pricing.TIERS[t]['price_paise']]}")
    if isinstance(months, bool) or not isinstance(months, int) or not 1 <= months <= MAX_MONTHS:
        return _err("invalid_months", f"months must be a whole number from 1 to {MAX_MONTHS}")
    if credits is None:
        credits = (_pricing.TIERS[tier]["credits_per_month"] or 0) * months
    if isinstance(credits, bool) or not isinstance(credits, int) or not 0 <= credits <= MAX_CREDITS:
        return _err("invalid_credits", f"credits must be a whole number from 0 to {MAX_CREDITS}")
    invoice_number = (invoice_number or "").strip()
    if not invoice_number or len(invoice_number) > 64:
        return _err("invalid_invoice", "the external invoice number is required (max 64 characters)")
    if isinstance(amount_paise, bool) or not isinstance(amount_paise, int) or amount_paise < 0:
        return _err("invalid_amount", "amount must be zero or more")
    if payment_method not in METHODS:
        return _err("invalid_method", f"payment method must be one of {list(METHODS)}")
    payment_ref = (payment_ref or "").strip() or None
    if payment_method == "complimentary":
        if amount_paise != 0:
            return _err("invalid_amount", "a complimentary grant must have amount 0")
        if not (notes or "").strip():
            return _err("note_required", "say why it is complimentary (--note)")
    else:
        if amount_paise <= 0:
            return _err("invalid_amount", "a paid sale needs an amount above 0 (use --method complimentary for a free grant)")
        if not payment_ref:
            return _err("payment_ref_required", "the payment reference (UTR / cheque number) is required")
    if not (operator or "").strip():
        return _err("operator_required", "who is granting this?")

    engine, tables = A._require_engine(), A._get_tables()
    sales = tables["assisted_sales"]
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError
    with engine.connect() as conn:
        org = _org(conn, org_id)
        if org is None:
            return _err("org_not_found", f"no company with id {org_id}")
        if org["billing_org_id"] is not None:
            return _err("not_a_payer", f"company {org_id} is paid for by company {org['billing_org_id']}; "
                                       "grant to the paying company", payer_org_id=org["billing_org_id"])
        if org["owner_user_id"] is None:
            return _err("no_owner", "the company has no owner to credit")
        if _paid_live_subscription(conn, org_id):
            return _err("has_subscription", "this company has a live self-serve subscription; "
                                            "cancel it first so they aren't billed twice")
        held = org["plan"]
        target = _pricing.plan_id_v2(tier)
        if _pricing.parse_plan(held)[0] == "v2" and _pricing.tier_rank(held) > _pricing.tier_rank(target):
            return _err("higher_plan_held", f"the company already has {_pricing.tier_label(held)}, "
                                            f"which is higher than {_pricing.TIERS[tier]['label']}")
        existing = conn.execute(select(sales).where(sales.c.invoice_number == invoice_number)).mappings().first()
        if existing:
            return _err("invoice_already_recorded", f"invoice {invoice_number} was already recorded "
                        f"(sale #{existing['id']}); nothing granted", sale=dict(existing))
        same_tier_end = [r["period_end"] for r in _active(conn, org_id, now) if r["tier"] == tier]
        start = max([now] + [S._aware(e) for e in same_tier_end])
        end = add_months(start, months)
        balance_before = A._wallet_balance(conn, org_id, org["owner_user_id"])

    summary = {
        "org_id": org_id, "org_name": org["name"], "tier": tier, "tier_label": _pricing.TIERS[tier]["label"],
        "months": months, "credits": credits, "invoice_number": invoice_number, "amount_paise": amount_paise,
        "payment_method": payment_method, "payment_ref": payment_ref,
        "period_start": start, "period_end": end, "stacked": start > now,
        "plan_before": held, "plan_after": target if _pricing.parse_plan(held)[0] != "v2"
        or _pricing.tier_rank(held) <= _pricing.tier_rank(target) else held,
        "wallet_balance_before": balance_before, "wallet_balance_after": balance_before + credits,
    }
    if dry_run:
        return {"ok": True, "dry_run": True, "summary": summary}

    try:
        with engine.begin() as conn:
            sale_id = conn.execute(sales.insert().values(
                org_id=org_id, tier=tier, months=months, credits=credits, invoice_number=invoice_number,
                amount_paise=amount_paise, payment_method=payment_method, payment_ref=payment_ref,
                period_start=start, period_end=end, status="active", plan_before=held,
                granted_by=operator.strip(), notes=(notes or "").strip() or None, created_at=now,
            )).inserted_primary_key[0]
            S._grant_plan(conn, org_id, tier)
            if credits:
                A.grant_credits(org["owner_user_id"], credits, reason="assisted_sale", ref_type="assisted_sale",
                                ref_id=sale_id, conn=conn, org_id=org_id)
    except IntegrityError:
        return _err("invoice_already_recorded", f"invoice {invoice_number} was recorded by another run; nothing granted")
    log.info("assisted sale #%s: org %s %s x%s months, %s credits, invoice %s, by %s",
             sale_id, org_id, tier, months, credits, invoice_number, operator)
    return {"ok": True, "dry_run": False, "sale": get(sale_id), "summary": summary}


# ── Revoke / expire ──────────────────────────────────────────────────────────
def revoke(sale_id, *, reason, operator, dry_run=True, now=None):
    """End an assisted sale now (error, refund, cancelled pilot). The plan is handed to
    whatever else still entitles the company, or revoked. Credits already granted are
    NOT taken back. dry_run (default) reports what would change."""
    now = now or _now()
    if not (reason or "").strip():
        return _err("reason_required", "say why (--reason)")
    if not (operator or "").strip():
        return _err("operator_required", "who is revoking this?")
    sale = get(sale_id)
    if sale is None:
        return _err("not_found", f"no assisted sale #{sale_id}")
    if sale["status"] != "active":
        return _err("not_active", f"sale #{sale_id} is already {sale['status']}")
    if dry_run:
        return {"ok": True, "dry_run": True, "sale": sale}
    engine, tables = A._require_engine(), A._get_tables()
    sales = tables["assisted_sales"]
    with engine.begin() as conn:
        claimed = conn.execute(sales.update().where(sales.c.id == sale_id, sales.c.status == "active")
                               .values(status="revoked", revoked_at=now, revoked_by=operator.strip(),
                                       revoke_reason=reason.strip())).rowcount
        if not claimed:
            return _err("not_active", f"sale #{sale_id} was changed by another run")
        changed = release_plan(conn, sale["org_id"], sale["tier"], now, exclude_id=sale_id)
    return {"ok": True, "dry_run": False, "sale": get(sale_id), "plan_changed": changed}


def expire_due(now=None):
    """Expire assisted sales whose period is over and release their plan. Called by the
    subscription sweep (cron + webhook). Idempotent. Returns how many companies' plans
    changed."""
    engine, tables = A._require_engine(), A._get_tables()
    sales = tables["assisted_sales"]
    from sqlalchemy import select
    now = now or _now()
    changed = 0
    with engine.begin() as conn:
        due = conn.execute(select(sales).where(sales.c.status == "active")).mappings().all()
        for r in due:
            if S._aware(r["period_end"]) > now:
                continue
            claimed = conn.execute(sales.update().where(sales.c.id == r["id"], sales.c.status == "active")
                                   .values(status="expired", expired_at=now)).rowcount
            if claimed and release_plan(conn, r["org_id"], r["tier"], now, exclude_id=r["id"]):
                changed += 1
    return changed

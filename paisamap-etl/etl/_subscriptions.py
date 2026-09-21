"""
_subscriptions.py — billing-v2 P2: recurring subscriptions (database side).

Everything here is DB-only and Razorpay-free, so it can be tested without a
gateway; blueprints/billing.py owns the Razorpay calls and the webhook.

The PLAN a company has still lives on organizations.plan ('v2_<tier>'); the
`subscriptions` table is the billing state that decides when that plan is
granted and taken away. Rules that matter for money:

  * Access is granted only when a CHARGE has actually succeeded (apply_charge),
    never on "authenticated"/"created". A live plan follows a paid cycle.
  * Every path converges: apply_charge is keyed by the Razorpay PAYMENT id (via
    the local order's unique razorpay_order_id), so /verify, the webhook and a
    redelivery can all run in any order or twice and end in the same state.
  * A late, older webhook can't overwrite newer state (last_event_at), and an
    ended subscription is never resurrected by a stale "active".
  * The plan is never DOWNGRADED past what someone else assigned: granting keeps
    a higher v2 tier already on the company, revoking only touches a plan that
    is still exactly the subscription's own tier.
  * A halted (failed-renewal) subscription keeps its plan for now — the
    soft-lock/dunning behaviour is P13. This module only records halted_at.
"""

import logging
from datetime import datetime, timezone

import _auth_db as A
import _pricing

log = logging.getLogger("paisamap.subscriptions")

LIVE_STATUSES = ("authenticated", "active", "pending", "halted", "paused")
ENDED_STATUSES = ("cancelled", "completed", "expired")
KNOWN_STATUSES = frozenset(("created",) + LIVE_STATUSES + ENDED_STATUSES)


def _now():
    return A._now()


def _aware(dt):
    """SQLite hands timezone-aware columns back naive; treat naive as UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def ts(epoch):
    """Razorpay epoch seconds -> aware datetime (None passes through)."""
    if not epoch:
        return None
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc)


def _row(row):
    return dict(row) if row else None


# ── Schema readiness ─────────────────────────────────────────────────────────
# Deploying the code and running paisamap-etl/db/apply_subscriptions_tables.py are
# separate steps, so for a while the code can be live without its tables. Until
# they exist there are, by definition, no subscriptions: reads must answer "none"
# instead of a 500, a webhook must be acked as unknown, and — the one that matters
# for money — nobody may START one (their payment would have nowhere to be recorded).
_ready = False


def tables_ready():
    """True once the subscriptions tables exist. Only a True answer is cached, so
    running the apply script takes effect without a restart."""
    global _ready
    if _ready:
        return True
    from sqlalchemy import inspect
    engine = A._require_engine()
    insp = inspect(engine)
    _ready = insp.has_table("subscriptions") and insp.has_table("razorpay_plans")
    return _ready


# ── Reads ────────────────────────────────────────────────────────────────────
def get_by_razorpay_id(razorpay_subscription_id):
    engine, tables = A._require_engine(), A._get_tables()
    subs = tables["subscriptions"]
    from sqlalchemy import select
    with engine.connect() as conn:
        return _row(conn.execute(select(subs).where(
            subs.c.razorpay_subscription_id == razorpay_subscription_id)).mappings().first())


def get_live(org_id):
    """The company's one live subscription (halted counts — it still owes and
    still holds access), or None."""
    engine, tables = A._require_engine(), A._get_tables()
    subs = tables["subscriptions"]
    from sqlalchemy import select
    with engine.connect() as conn:
        return _row(conn.execute(
            select(subs).where(subs.c.org_id == org_id, subs.c.status.in_(LIVE_STATUSES))
            .order_by(subs.c.id.desc())).mappings().first())


def get_latest(org_id):
    """What to show on the Billing page: the live subscription if there is one,
    else the most recent one that ever started (ended or pending checkout)."""
    live = get_live(org_id)
    if live:
        return live
    engine, tables = A._require_engine(), A._get_tables()
    subs = tables["subscriptions"]
    from sqlalchemy import select
    with engine.connect() as conn:
        return _row(conn.execute(
            select(subs).where(subs.c.org_id == org_id, subs.c.status.in_(("created",) + ENDED_STATUSES))
            .order_by(subs.c.id.desc())).mappings().first())


def public_view(sub):
    """The fields the Billing page may show — never the Razorpay ids."""
    if not sub:
        return None
    tier = sub["tier"]
    return {
        "tier": tier, "label": _pricing.TIERS[tier]["label"] if tier in _pricing.TIERS else tier,
        "cycle": sub["cycle"], "status": sub["status"], "amount_paise": sub["amount_paise"],
        "current_start": _iso(sub["current_start"]), "current_end": _iso(sub["current_end"]),
        "cancel_at_period_end": bool(sub["cancel_at_period_end"]),
        "ends_at": _iso(sub["ends_at"]), "paid_count": sub["paid_count"],
        "past_due": sub["status"] == "halted",
    }


def _iso(dt):
    dt = _aware(dt)
    return dt.isoformat() if dt else None


# ── Razorpay plan cache ──────────────────────────────────────────────────────
def cached_plan_id(tier, cycle, amount_paise):
    engine, tables = A._require_engine(), A._get_tables()
    rp = tables["razorpay_plans"]
    from sqlalchemy import select
    with engine.connect() as conn:
        return conn.execute(select(rp.c.razorpay_plan_id).where(
            rp.c.tier == tier, rp.c.cycle == cycle, rp.c.amount_paise == amount_paise)).scalar()


def save_plan_id(tier, cycle, amount_paise, razorpay_plan_id):
    """Store a freshly created Razorpay plan. If a concurrent request already
    stored one for the same (tier, cycle, amount), keep the first and return it
    (ours becomes an unused, harmless duplicate plan on Razorpay's side)."""
    engine, tables = A._require_engine(), A._get_tables()
    rp = tables["razorpay_plans"]
    from sqlalchemy.exc import IntegrityError
    try:
        with engine.begin() as conn:
            conn.execute(rp.insert().values(
                tier=tier, cycle=cycle, amount_paise=amount_paise, razorpay_plan_id=razorpay_plan_id,
                price_book_version=_pricing.PRICE_BOOK_VERSION, created_at=_now()))
        return razorpay_plan_id
    except IntegrityError:
        return cached_plan_id(tier, cycle, amount_paise)


# ── Create ───────────────────────────────────────────────────────────────────
def create_local(org_id, user_id, tier, cycle, razorpay_subscription_id, razorpay_plan_id, amount_paise):
    """Record a subscription the moment Razorpay has issued its id, BEFORE the
    customer authorises anything. A checkout the customer walked away from
    earlier is replaced (status 'abandoned') so it can't block a new attempt."""
    engine, tables = A._require_engine(), A._get_tables()
    subs = tables["subscriptions"]
    now = _now()
    with engine.begin() as conn:
        conn.execute(subs.update().where(subs.c.org_id == org_id, subs.c.status == "created")
                     .values(status="abandoned", updated_at=now))
        new_id = conn.execute(subs.insert().values(
            org_id=org_id, created_by_user_id=user_id, tier=tier, cycle=cycle,
            razorpay_subscription_id=razorpay_subscription_id, razorpay_plan_id=razorpay_plan_id,
            status="created", amount_paise=amount_paise, price_book_version=_pricing.PRICE_BOOK_VERSION,
            paid_count=0, cancel_at_period_end=False, created_at=now, updated_at=now,
        )).inserted_primary_key[0]
    return get_by_razorpay_id(razorpay_subscription_id) if new_id else None


# ── Plan grant / revoke (always inside the caller's transaction) ─────────────
def _grant_plan(conn, org_id, tier):
    """organizations.plan -> 'v2_<tier>', unless the company already holds a
    HIGHER v2 tier (e.g. one assigned by hand for an invoice customer)."""
    orgs = A._get_tables()["organizations"]
    from sqlalchemy import select
    target = _pricing.plan_id_v2(tier)
    current = conn.execute(select(orgs.c.plan).where(orgs.c.id == org_id)).scalar()
    if (_pricing.parse_plan(current)[0] == "v2"
            and _pricing.tier_rank(current) > _pricing.tier_rank(target)):
        return False
    conn.execute(orgs.update().where(orgs.c.id == org_id).values(plan=target))
    return True


def _revoke_plan(conn, org_id, tier):
    """Back to 'free', but ONLY if the plan is still exactly this subscription's
    own tier — never clobber a plan someone assigned since."""
    orgs = A._get_tables()["organizations"]
    res = conn.execute(orgs.update()
                       .where(orgs.c.id == org_id, orgs.c.plan == _pricing.plan_id_v2(tier))
                       .values(plan="free"))
    return res.rowcount > 0


# ── State machine ────────────────────────────────────────────────────────────
def record_state(razorpay_subscription_id, *, status=None, current_start=None, current_end=None,
                 paid_count=None, event_ts=None, cancel_at_period_end=None, grant=False):
    """Apply what Razorpay says about a subscription.

    event_ts is the webhook's own timestamp (aware datetime); an event older than
    the newest one already applied is ignored. Local snapshots (a fetch during
    /verify) pass event_ts=None: no stale check and no stamp, so they can't make
    a genuinely newer webhook look stale through clock skew.

    grant=True (only apply_charge passes it) also sets the company's plan, in the
    same transaction as the status change.

    Returns {"subscription": row} plus one of "stale"/"duplicate"/"ignored_status"
    when applicable, or {"error": "unknown_subscription"}."""
    engine, tables = A._require_engine(), A._get_tables()
    subs = tables["subscriptions"]
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError
    now = _now()
    try:
        with engine.begin() as conn:
            row = conn.execute(select(subs).where(
                subs.c.razorpay_subscription_id == razorpay_subscription_id)
                .with_for_update()).mappings().first()
            if row is None:
                return {"error": "unknown_subscription"}
            out = {}
            last = _aware(row["last_event_at"])
            if event_ts is not None and last is not None and event_ts < last:
                return {"stale": True, "subscription": dict(row)}

            vals = {"updated_at": now}
            if event_ts is not None:
                vals["last_event_at"] = event_ts
            if current_start:
                vals["current_start"] = current_start
            if current_end:
                vals["current_end"] = current_end
            if paid_count is not None:
                vals["paid_count"] = max(row["paid_count"] or 0, paid_count)
            if cancel_at_period_end is not None:
                vals["cancel_at_period_end"] = bool(cancel_at_period_end)

            new_status = status if status in KNOWN_STATUSES else None
            if status and new_status is None:
                log.warning("subscription %s: unknown status %r ignored", razorpay_subscription_id, status)
            if new_status and row["status"] in ENDED_STATUSES and new_status not in ENDED_STATUSES:
                # An ended subscription is never resurrected by a late "active".
                out["ignored_status"] = new_status
                new_status = None
            if new_status and row["status"] == "duplicate":
                out["ignored_status"] = new_status
                new_status = None
            if new_status:
                if new_status in LIVE_STATUSES and row["status"] not in LIVE_STATUSES:
                    clash = conn.execute(select(subs.c.id).where(
                        subs.c.org_id == row["org_id"], subs.c.status.in_(LIVE_STATUSES),
                        subs.c.id != row["id"])).scalar()
                    if clash is not None:
                        new_status = "duplicate"
                        out["duplicate"] = True
                        log.error("subscription %s is live for org %s which already has live subscription id=%s "
                                  "— marked duplicate, needs a human (cancel it at Razorpay)",
                                  razorpay_subscription_id, row["org_id"], clash)
                vals["status"] = new_status
                if new_status == "halted" and not row["halted_at"]:
                    vals["halted_at"] = now
                if new_status in ("active", "authenticated"):
                    vals["halted_at"] = None
                if new_status in ENDED_STATUSES:
                    if new_status == "cancelled" and not row["cancelled_at"]:
                        vals["cancelled_at"] = now
                    end = _aware(current_end or row["current_end"])
                    vals["ends_at"] = end if end and end > now else now
            conn.execute(subs.update().where(subs.c.id == row["id"]).values(**vals))
            if grant and (new_status or row["status"]) in LIVE_STATUSES and new_status != "duplicate":
                _grant_plan(conn, row["org_id"], row["tier"])
            elif grant:
                log.error("subscription %s was charged but is %s — no plan granted, needs a human",
                          razorpay_subscription_id, new_status or row["status"])
            out["subscription"] = dict(conn.execute(
                select(subs).where(subs.c.id == row["id"])).mappings().first())
            return out
    except IntegrityError:
        # Two workers raced two different subscriptions to "live" for one company
        # and the partial unique index caught it. Demote this one.
        with engine.begin() as conn:
            conn.execute(subs.update().where(subs.c.razorpay_subscription_id == razorpay_subscription_id)
                         .values(status="duplicate", updated_at=now))
        log.error("subscription %s lost a live-uniqueness race — marked duplicate", razorpay_subscription_id)
        return {"duplicate": True, "subscription": get_by_razorpay_id(razorpay_subscription_id)}


def apply_charge(razorpay_subscription_id, payment_id, amount_paise, *, current_start=None,
                 current_end=None, paid_count=None, event_ts=None, razorpay_order_id=None):
    """One successful charge on a subscription: record it as a paid local order
    (idempotent per payment id) and grant/extend the plan.

    The order's razorpay_order_id is derived from the PAYMENT id ('sub_charge_
    <payment id>'), which both /verify and the webhook know, so the two paths and
    any redelivery collapse onto one row. Safe to call repeatedly; the second call
    returns newly_paid=False and changes nothing but a refreshed period.

    Returns {"order", "subscription", "newly_paid"} or {"error": ...}."""
    sub = get_by_razorpay_id(razorpay_subscription_id)
    if sub is None:
        return {"error": "unknown_subscription"}
    from sqlalchemy.exc import IntegrityError
    key = f"sub_charge_{payment_id}"
    order = A.get_order_by_razorpay_id(key)
    if order is None:
        owner = sub["created_by_user_id"] or _org_owner(sub["org_id"])
        if owner is None:
            return {"error": "no_billing_user"}
        try:
            order = A.create_order(
                owner, "plan_upgrade", key, amount_paise,
                target_plan=_pricing.plan_id_v2(sub["tier"]),
                meta={"subscription_id": sub["razorpay_subscription_id"], "cycle": sub["cycle"],
                      "tier": sub["tier"], "razorpay_order_id": razorpay_order_id},
                org_id=sub["org_id"], billing_org_id=sub["org_id"])
        except IntegrityError:
            order = A.get_order_by_razorpay_id(key)      # a concurrent delivery got there first
    updated, newly_paid = A.mark_order_paid(order["id"], payment_id, None)
    state = record_state(
        razorpay_subscription_id, status="active", current_start=current_start, current_end=current_end,
        paid_count=paid_count if paid_count is not None else ((sub["paid_count"] or 0) + 1 if newly_paid else None),
        event_ts=event_ts, grant=True)
    if state.get("stale"):
        # An older event than one already applied: the payment is real and recorded,
        # but don't touch the status; still make sure access follows a paid cycle.
        state = record_state(razorpay_subscription_id, current_start=current_start,
                             current_end=current_end, grant=True)
    return {"order": updated or order, "subscription": state.get("subscription"), "newly_paid": newly_paid,
            "duplicate": state.get("duplicate", False)}


def _org_owner(org_id):
    engine, tables = A._require_engine(), A._get_tables()
    orgs = tables["organizations"]
    from sqlalchemy import select
    with engine.connect() as conn:
        return conn.execute(select(orgs.c.owner_user_id).where(orgs.c.id == org_id)).scalar()


def sweep_ended(now=None):
    """Take the plan away from subscriptions that have ended AND whose paid-for
    period is over. Idempotent; run from cron and opportunistically from the
    webhook. Returns the number of companies whose plan was revoked."""
    engine, tables = A._require_engine(), A._get_tables()
    subs = tables["subscriptions"]
    from sqlalchemy import select
    now = now or _now()
    revoked = 0
    with engine.begin() as conn:
        due = conn.execute(select(subs.c.id, subs.c.org_id, subs.c.tier, subs.c.ends_at).where(
            subs.c.status.in_(ENDED_STATUSES), subs.c.plan_revoked_at.is_(None),
            subs.c.ends_at.is_not(None))).all()
        for r in due:
            if _aware(r.ends_at) > now:
                continue
            if _revoke_plan(conn, r.org_id, r.tier):
                revoked += 1
            conn.execute(subs.update().where(subs.c.id == r.id).values(plan_revoked_at=now, updated_at=now))
    return revoked


def org_plan(org_id):
    engine, tables = A._require_engine(), A._get_tables()
    orgs = tables["organizations"]
    from sqlalchemy import select
    with engine.connect() as conn:
        return conn.execute(select(orgs.c.plan).where(orgs.c.id == org_id)).scalar()


def invoice_exists(order_id):
    engine, tables = A._require_engine(), A._get_tables()
    inv = tables["invoices"]
    from sqlalchemy import select
    with engine.connect() as conn:
        return conn.execute(select(inv.c.id).where(inv.c.order_id == order_id)).scalar() is not None

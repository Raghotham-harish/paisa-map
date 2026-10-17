"""
_addon_subs.py — P5 round 3: self-serve extra seats and companies (database side).

Each purchase ("+2 seats", "+1 company") is its OWN small monthly Razorpay
subscription (owner decision, 17 Oct 2026). Razorpay can't change the amount of
a subscription authorised by UPI Autopay or eMandate, which is how most Indian
customers pay, so extras can't be added to the plan's own monthly charge. A
separate subscription works with every payment method, needs no proration, and
removing extras is just cancelling that one (it runs to the end of the month
already paid for).

Like _subscriptions.py this module is Razorpay-free except cancel_orphans(),
which takes the caller's client; blueprints/billing.py owns checkout and the
webhook.

Money rules (same as the plan subscriptions):
  * extras count only after a charge has actually succeeded (paid_count >= 1);
  * every charge path (/verify, webhook, redelivery) collapses onto one order,
    keyed by the Razorpay payment id;
  * a late, older webhook can't overwrite newer state, and an ended add-on is
    never resurrected;
  * extras stop counting when the add-on is halted (renewal failed and Razorpay
    gave up) or its paid month is over after a cancel. Nobody is ever removed:
    an account over its limit only stops adding people/companies (see _seats).
  * when the company no longer has a live plan subscription, its add-ons are
    cancelled at Razorpay so they never charge for extras that do nothing.
"""

import logging
from datetime import timedelta, timezone

import _auth_db as A
import _pricing

log = logging.getLogger("paisamap.addons")

KINDS = ("seat", "company")
MAX_QUANTITY = 50                      # per purchase; a typo guard, not a business limit
LIVE_STATUSES = ("authenticated", "active", "pending", "halted", "paused")
ENDED_STATUSES = ("cancelled", "completed", "expired")
KNOWN_STATUSES = frozenset(("created",) + LIVE_STATUSES + ENDED_STATUSES)
# A failing renewal Razorpay is still retrying keeps the extras; halted (it gave up) doesn't.
ENTITLED_LIVE = ("active", "pending")


def _now():
    return A._now()


def _aware(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _row(row):
    return dict(row) if row else None


def tables_ready(conn=None):
    from sqlalchemy import inspect
    return inspect(conn if conn is not None else A._require_engine()).has_table("addon_subscriptions")


def unit_paise(tier, kind):
    """Ex-GST monthly price of one extra seat/company on `tier`, or None when the
    tier doesn't sell that extra self-serve (free, signal plans, Scale and up)."""
    row = _pricing.TIERS.get(tier)
    if not row or not row.get("self_serve") or _pricing.is_signal_tier(tier):
        return None
    return row["extra_seat_paise"] if kind == "seat" else row["extra_company_paise"]


def plan_key(kind, tier):
    """razorpay_plans.tier value for an add-on plan (cached by amount there too)."""
    return f"addon_{kind}_{tier}"


# ── Entitlement ──────────────────────────────────────────────────────────────
def _entitled(row, now):
    if (row["paid_count"] or 0) < 1:
        return False
    if row["status"] in ENTITLED_LIVE:
        return True
    if row["status"] in ENDED_STATUSES:
        end = _aware(row["ends_at"])
        return end is not None and end > now
    return False


def entitled_extras(conn, wallet_id, now=None):
    """(extra_seats, extra_companies) the wallet's paid add-ons give right now.
    (0, 0) before the table exists."""
    if wallet_id is None or not tables_ready(conn):
        return 0, 0
    t = A._get_tables()["addon_subscriptions"]
    from sqlalchemy import select
    now = now or _now()
    seats = companies = 0
    for r in conn.execute(select(t).where(t.c.wallet_org_id == wallet_id, t.c.paid_count >= 1)).mappings():
        if _entitled(r, now):
            if r["kind"] == "seat":
                seats += r["quantity"]
            else:
                companies += r["quantity"]
    return seats, companies


# ── Reads ────────────────────────────────────────────────────────────────────
def get_by_razorpay_id(razorpay_subscription_id):
    t = A._get_tables()["addon_subscriptions"]
    from sqlalchemy import select
    with A._require_engine().connect() as conn:
        if not tables_ready(conn):
            return None
        return _row(conn.execute(select(t).where(
            t.c.razorpay_subscription_id == razorpay_subscription_id)).mappings().first())


def get(addon_id):
    t = A._get_tables()["addon_subscriptions"]
    from sqlalchemy import select
    with A._require_engine().connect() as conn:
        return _row(conn.execute(select(t).where(t.c.id == addon_id)).mappings().first())


def list_for(wallet_id):
    """Every add-on worth showing: live ones, and ended ones still counting."""
    t = A._get_tables()["addon_subscriptions"]
    from sqlalchemy import select
    now = _now()
    with A._require_engine().connect() as conn:
        rows = conn.execute(select(t).where(t.c.wallet_org_id == wallet_id).order_by(t.c.id)).mappings().all()
    return [dict(r) for r in rows if r["status"] in LIVE_STATUSES or _entitled(r, now)]


def live_for(wallet_id):
    t = A._get_tables()["addon_subscriptions"]
    from sqlalchemy import select
    with A._require_engine().connect() as conn:
        if not tables_ready(conn):
            return []
        return [dict(r) for r in conn.execute(select(t).where(
            t.c.wallet_org_id == wallet_id, t.c.status.in_(LIVE_STATUSES))).mappings()]


def public_view(row):
    """What the Billing page may show — never the Razorpay ids."""
    if not row:
        return None
    iso = lambda d: _aware(d).isoformat() if d else None  # noqa: E731
    return {
        "id": row["id"], "kind": row["kind"], "quantity": row["quantity"],
        "unit_paise": row["unit_paise"], "amount_paise": row["amount_paise"],
        "status": row["status"], "paid": (row["paid_count"] or 0) >= 1,
        "counting": _entitled(row, _now()),
        "current_end": iso(row["current_end"]), "ends_at": iso(row["ends_at"]),
        "cancel_at_period_end": bool(row["cancel_at_period_end"]),
        "past_due": row["status"] in ("pending", "halted"),
    }


def line_item(row):
    noun = "seat" if row["kind"] == "seat" else "company"
    plural = noun if row["quantity"] == 1 else ("seats" if noun == "seat" else "companies")
    return f"Extra {plural} × {row['quantity']} — monthly"


# ── Create ───────────────────────────────────────────────────────────────────
def create_local(wallet_id, user_id, kind, quantity, tier, unit, amount_paise,
                 razorpay_subscription_id, razorpay_plan_id, meta=None):
    """Record an add-on the moment Razorpay has issued its id, before the customer
    authorises anything. Grants nothing. An earlier checkout for the same wallet
    that was never authorised is marked abandoned (it can't be paid any more
    without Razorpay telling us, and if it is, record_state brings it back)."""
    t = A._get_tables()["addon_subscriptions"]
    now = _now()
    with A._require_engine().begin() as conn:
        conn.execute(t.update().where(t.c.wallet_org_id == wallet_id, t.c.status == "created",
                                      t.c.kind == kind)
                     .values(status="abandoned", updated_at=now))
        conn.execute(t.insert().values(
            wallet_org_id=wallet_id, created_by_user_id=user_id, kind=kind, quantity=quantity, tier=tier,
            unit_paise=unit, amount_paise=amount_paise, razorpay_subscription_id=razorpay_subscription_id,
            razorpay_plan_id=razorpay_plan_id, status="created", paid_count=0, cancel_at_period_end=False,
            meta=meta, created_at=now, updated_at=now))
    return get_by_razorpay_id(razorpay_subscription_id)


# ── State machine ────────────────────────────────────────────────────────────
def record_state(razorpay_subscription_id, *, status=None, current_start=None, current_end=None,
                 paid_count=None, event_ts=None, cancel_at_period_end=None, ends_at=None):
    """Apply what Razorpay says about an add-on. Same staleness rules as
    _subscriptions.record_state. Returns {"addon": row} (+ "stale" /
    "ignored_status") or {"error": "unknown_addon"}."""
    t = A._get_tables()["addon_subscriptions"]
    from sqlalchemy import select
    now = _now()
    with A._require_engine().begin() as conn:
        row = conn.execute(select(t).where(t.c.razorpay_subscription_id == razorpay_subscription_id)
                           .with_for_update()).mappings().first()
        if row is None:
            return {"error": "unknown_addon"}
        out = {}
        last = _aware(row["last_event_at"])
        if event_ts is not None and last is not None and event_ts < last:
            return {"stale": True, "addon": dict(row)}
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
            log.warning("addon %s: unknown status %r ignored", razorpay_subscription_id, status)
        if new_status and row["status"] in ENDED_STATUSES and new_status not in ENDED_STATUSES:
            out["ignored_status"] = new_status
            new_status = None
        if new_status:
            vals["status"] = new_status
            if new_status in ENDED_STATUSES:
                if new_status == "cancelled" and not row["cancelled_at"]:
                    vals["cancelled_at"] = now
                end = _aware(ends_at or current_end or row["current_end"])
                vals["ends_at"] = end if end and end > now else now
        conn.execute(t.update().where(t.c.id == row["id"]).values(**vals))
        out["addon"] = dict(conn.execute(select(t).where(t.c.id == row["id"])).mappings().first())
        return out


def apply_charge(razorpay_subscription_id, payment_id, amount_paise, *, current_start=None,
                 current_end=None, paid_count=None, event_ts=None, razorpay_order_id=None, order_meta=None):
    """One successful add-on charge: a paid local order (kind 'addon', idempotent
    per payment id) and the add-on marked active. Returns {"order", "addon",
    "newly_paid"} or {"error": ...}."""
    addon = get_by_razorpay_id(razorpay_subscription_id)
    if addon is None:
        return {"error": "unknown_addon"}
    from sqlalchemy.exc import IntegrityError
    key = f"sub_charge_{payment_id}"
    order = A.get_order_by_razorpay_id(key)
    if order is None:
        owner = addon["created_by_user_id"] or _org_owner(addon["wallet_org_id"])
        if owner is None:
            return {"error": "no_billing_user"}
        try:
            order = A.create_order(
                owner, "addon", key, amount_paise,
                meta={**(order_meta or {}), "addon_subscription_id": addon["razorpay_subscription_id"],
                      "addon_kind": addon["kind"], "quantity": addon["quantity"], "tier": addon["tier"],
                      "razorpay_order_id": razorpay_order_id},
                org_id=addon["wallet_org_id"], billing_org_id=addon["wallet_org_id"])
        except IntegrityError:
            order = A.get_order_by_razorpay_id(key)
    updated, newly_paid = A.mark_order_paid(order["id"], payment_id, None)
    pc = paid_count if paid_count is not None else ((addon["paid_count"] or 0) + 1 if newly_paid else None)
    state = record_state(razorpay_subscription_id, status="active", current_start=current_start,
                         current_end=current_end, paid_count=pc, event_ts=event_ts)
    if state.get("stale"):
        # Older than an event already applied: the payment is real and counted,
        # but the status stays as the newer event left it.
        state = record_state(razorpay_subscription_id, current_start=current_start,
                             current_end=current_end, paid_count=pc)
    return {"order": updated or order, "addon": state.get("addon"), "newly_paid": newly_paid}


def _org_owner(org_id):
    orgs = A._get_tables()["organizations"]
    from sqlalchemy import select
    with A._require_engine().connect() as conn:
        return conn.execute(select(orgs.c.owner_user_id).where(orgs.c.id == org_id)).scalar()


# ── Tied to the plan ─────────────────────────────────────────────────────────
# Fixing a failed renewal (billing /subscriptions/recover) cancels the failing plan
# subscription and starts a replacement that isn't live until it is paid. For
# that long, a fresh replacement checkout still counts as "the plan goes on".
REPLACEMENT_GRACE = timedelta(days=2)


def plan_is_live(wallet_id, now=None):
    """True while the wallet has a plan subscription that can still renew — the
    only thing that makes its extras worth paying for — or a replacement checkout
    started within REPLACEMENT_GRACE."""
    import _subscriptions
    if not _subscriptions.tables_ready():
        return False
    live = _subscriptions.get_live(wallet_id)
    if live:
        return not live["cancel_at_period_end"]
    t = A._get_tables()["subscriptions"]
    from sqlalchemy import select
    now = now or _now()
    with A._require_engine().connect() as conn:
        created = conn.execute(select(t.c.created_at).where(
            t.c.org_id == wallet_id, t.c.status == "created")).scalars().all()
    return any(_aware(c) > now - REPLACEMENT_GRACE for c in created)


def cancel_all(client, wallet_id, *, at_cycle_end):
    """Cancel every live add-on of `wallet_id` at Razorpay and record it. A
    paid-up add-on cancelled at cycle end keeps counting to the end of its paid
    month; anything else ends now. Never raises: returns the number cancelled
    and logs each failure (the next webhook / sweep tries again)."""
    done = 0
    for a in live_for(wallet_id):
        end_later = at_cycle_end and a["status"] == "active"
        try:
            client.subscription.cancel(a["razorpay_subscription_id"],
                                       {"cancel_at_cycle_end": 1 if end_later else 0})
        except Exception:
            log.exception("cancelling addon %s at Razorpay failed — will retry", a["razorpay_subscription_id"])
            continue
        if end_later:
            record_state(a["razorpay_subscription_id"], cancel_at_period_end=True)
        else:
            record_state(a["razorpay_subscription_id"], status="cancelled")
        done += 1
    return done


def orphan_wallets():
    """Wallets holding a live add-on that still renews but no live plan
    subscription to go with it."""
    t = A._get_tables()["addon_subscriptions"]
    from sqlalchemy import select
    with A._require_engine().connect() as conn:
        if not tables_ready(conn):
            return []
        wallets = {r[0] for r in conn.execute(select(t.c.wallet_org_id).where(
            t.c.status.in_(LIVE_STATUSES), t.c.cancel_at_period_end.is_(False)))}
    return sorted(w for w in wallets if not plan_is_live(w))


def cancel_orphans(client):
    """Stop add-ons from charging for a company whose plan has ended or will not
    renew. Run from the webhook and the hourly sweep."""
    n = 0
    for w in orphan_wallets():
        n += cancel_all(client, w, at_cycle_end=True)
    return n

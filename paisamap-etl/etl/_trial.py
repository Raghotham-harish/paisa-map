"""
_trial.py — billing-v2 P4: the no-card free trial (database side).

Starting a trial needs no card and no Razorpay: the PAYING company gets the
'trial' plan (the full dashboard) and TRIAL_CREDITS that expire with it, for
TRIAL_DAYS. It can subscribe at any time; a paid plan simply outranks 'trial'.
When the trial ends:
  * still on 'trial'  -> back to Free (saved data is kept), outcome 'lapsed', one email;
  * on a paid plan    -> nothing to undo, outcome 'converted'.
One trial per company and per person, ever — enforced by UNIQUE constraints on
`trials`, so two racing clicks or a second company of the same person can't get
a second one. A company that has ever paid for a subscription can't start one.
"""

import logging
from datetime import timedelta
from sqlalchemy.exc import IntegrityError

import _auth_db as A
import _pricing
import _subscriptions as S

log = logging.getLogger("paisamap.trial")


def _now():
    return A._now()


def _plan_of(conn, org_id):
    orgs = A._get_tables()["organizations"]
    from sqlalchemy import select
    return conn.execute(select(orgs.c.plan).where(orgs.c.id == org_id)).scalar()


def _used(conn, org_id, user_id):
    tables = A._get_tables()
    trials, subs = tables["trials"], tables["subscriptions"]
    from sqlalchemy import select, or_
    if conn.execute(select(trials.c.id).where(
            or_(trials.c.org_id == org_id, trials.c.user_id == user_id)).limit(1)).scalar() is not None:
        return True
    return conn.execute(select(subs.c.id).where(
        subs.c.org_id == org_id, subs.c.paid_count >= 1).limit(1)).scalar() is not None


def unavailable(org_id, user_id):
    """None if `user_id` may start the free trial for the paying company `org_id`,
    else why not: 'trial_used' (this company or this person has had a trial, or the
    company has paid for a subscription before) or 'has_plan' (it already has the
    dashboard)."""
    with A._require_engine().connect() as conn:
        if _used(conn, org_id, user_id):
            return "trial_used"
        if _pricing.is_dashboard_tier(_plan_of(conn, org_id) or "free"):
            return "has_plan"
    return None


def for_org(org_id):
    trials = A._get_tables()["trials"]
    from sqlalchemy import select
    with A._require_engine().connect() as conn:
        row = conn.execute(select(trials).where(trials.c.org_id == org_id)).mappings().first()
    return dict(row) if row else None


def public_view(trial, now=None):
    """What the Billing page shows: {state: running|ended, ends_at, outcome}."""
    if not trial:
        return None
    ends = S._aware(trial["ends_at"])
    running = trial["ended_at"] is None and (now or _now()) < ends
    return {"state": "running" if running else "ended", "ends_at": S._iso(ends),
            "outcome": trial["outcome"], "credits": _pricing.TRIAL_CREDITS}


def start(org_id, user_id, now=None):
    """Start the trial: row + 'trial' plan in one transaction, then the credits
    (idempotent per trial, so a crash in between heals on the next sweep).
    Returns {"trial": row} or {"error": 'trial_used' | 'has_plan'}."""
    engine, tables = A._require_engine(), A._get_tables()
    trials, orgs = tables["trials"], tables["organizations"]
    from sqlalchemy import select
    now = (now or _now()).replace(microsecond=0)
    try:
        with engine.begin() as conn:
            if _used(conn, org_id, user_id):
                return {"error": "trial_used"}
            if _pricing.is_dashboard_tier(_plan_of(conn, org_id) or "free"):
                return {"error": "has_plan"}
            tid = conn.execute(trials.insert().values(
                org_id=org_id, user_id=user_id, started_at=now,
                ends_at=now + timedelta(days=_pricing.TRIAL_DAYS))).inserted_primary_key[0]
            conn.execute(orgs.update().where(orgs.c.id == org_id).values(plan=_pricing.plan_id_v2("trial")))
            row = dict(conn.execute(select(trials).where(trials.c.id == tid)).mappings().first())
    except IntegrityError:
        # A concurrent start for this company or by this person won the UNIQUE race.
        return {"error": "trial_used"}
    _grant_credits(row)
    return {"trial": row}


def _grant_credits(row):
    if row["user_id"] is None:
        return None
    return A.grant_plan_credits(row["user_id"], row["org_id"], _pricing.TRIAL_CREDITS,
                                source_ref=f"trial:{row['id']}", expires_at=S._aware(row["ends_at"]),
                                reason="trial_credits", ref_type="trial", ref_id=row["id"])


def sweep(now=None, background=False):
    """Time-driven half, idempotent, each step claimed with a conditional UPDATE:
      * TRIAL_REMINDER_HOURS before the end, one "your trial ends tomorrow —
        subscribe to keep access" email, unless the company already has a paid plan;
      * at the end: back to Free if still on 'trial' (one email), else 'converted';
      * re-grants trial credits a crash may have skipped.
    Returns the notices."""
    import _assisted_sale
    engine, tables = A._require_engine(), A._get_tables()
    trials = tables["trials"]
    from sqlalchemy import select
    now = now or _now()
    remind = timedelta(hours=_pricing.TRIAL_REMINDER_HOURS)
    trial_plan = _pricing.plan_id_v2("trial")
    notices, running = [], []
    with engine.begin() as conn:
        rows = conn.execute(select(trials).where(trials.c.ended_at.is_(None))).mappings().all()
        for r in rows:
            ends = S._aware(r["ends_at"])
            on_trial = _plan_of(conn, r["org_id"]) == trial_plan
            if now >= ends:
                outcome = "lapsed" if on_trial else "converted"
                claimed = conn.execute(trials.update().where(trials.c.id == r["id"], trials.c.ended_at.is_(None))
                                       .values(ended_at=now, outcome=outcome)).rowcount
                if claimed and on_trial:
                    _assisted_sale.release_plan(conn, r["org_id"], "trial", now)
                    notices.append({"kind": "trial_lapsed", "to": S._recipients(conn, r["org_id"]),
                                    "org_name": S._org_name(conn, r["org_id"]), "lock_at": ends,
                                    "amount_paise": None})
                continue
            running.append(dict(r))
            if on_trial and r["reminded_at"] is None and ends - remind <= now:
                claimed = conn.execute(trials.update().where(trials.c.id == r["id"], trials.c.reminded_at.is_(None))
                                       .values(reminded_at=now)).rowcount
                if claimed:
                    notices.append({"kind": "trial_ending", "to": S._recipients(conn, r["org_id"]),
                                    "org_name": S._org_name(conn, r["org_id"]), "lock_at": ends,
                                    "amount_paise": None})
    for r in running:
        _grant_credits(r)
    S._deliver(notices, background)
    return notices

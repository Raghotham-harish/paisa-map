"""
_plan_mapping.py — map legacy accounts ('pro' / 'team') onto v2 tiers.

What "map" means, precisely
  * It sets ONE thing: organizations.plan = 'v2_<tier>' on the company an
    account owns as its primary company. users.plan is never touched (it has a
    CHECK constraint pinned to free/pro/team, and leaving it alone is what makes
    this reversible and lossless: the personal plan stays as a floor).
  * It is inert until BILLING_SCOPE=wallet: only wallet scope reads a company's
    plan. In user scope every account keeps exactly what users.plan gives it.
  * In wallet scope a person's effective plan is the BEST of their own plan and
    their company's, so nobody can be lowered by it, and teammates inherit the
    company's tier.

The rule for picking a tier (no judgment call hidden in it)
  The LOWEST v2 dashboard tier that keeps everything the account can do today
  (_pricing.entitlements). Legacy 'pro' and 'team' both unlock the Pro signal
  columns AND the raised API limit, and the lowest v2 tier with the API is
  Growth — so both map to Growth by default. A mapping that would give an
  account LESS than it has now is refused unless explicitly allowed.
  Where you want something else (the owner's own account, a comp), pass an
  override: {email: tier}.

Nothing is guessed about money: no credits are granted or removed, no invoice
is created, and seat/company limits are not enforced anywhere yet (they arrive
with subscriptions), so a mapped tier only ever changes what the two enforced
entitlements say.
"""

import _pricing

MAPPABLE_TIERS = tuple(t for t in _pricing.TIER_ORDER if t in _pricing.TIERS and t != "trial")
ACTION = "plan_mapped"
ROLLED_BACK = "plan_mapping_rolled_back"


def propose_tier(legacy_plan):
    """The lowest v2 dashboard tier that keeps every entitlement of `legacy_plan`,
    or None when there is nothing to keep (free / unknown)."""
    want = _pricing.entitlements(legacy_plan)
    if not any(want.values()):
        return None
    for tier in MAPPABLE_TIERS:
        have = _pricing.entitlements(_pricing.plan_id_v2(tier))
        if all(have[k] or not want[k] for k in want):
            return tier
    return None


def _legacy_paid(plan):
    return _pricing.parse_plan(plan)[0] == "legacy"


def build_report(A, overrides=None, allow_downgrade=False):
    """One row per company that is (or whose owner is) on a legacy paid plan.
    Read-only. Returns {"rows": [...], "errors": [...]}; a row's `action` is
    'map' (will change), 'already_v2', 'skip' (with `reason`) or 'blocked'."""
    engine = A._require_engine()
    tables = A._get_tables()
    users, orgs, members = tables["users"], tables["organizations"], tables["org_members"]
    from sqlalchemy import select, func
    overrides = {k.strip().lower(): v for k, v in (overrides or {}).items()}
    errors, rows, matched = [], [], set()
    for email, tier in overrides.items():
        if tier not in MAPPABLE_TIERS:
            errors.append(f"override for {email}: {tier!r} is not a tier you can map to ({', '.join(MAPPABLE_TIERS)})")
    with engine.connect() as conn:
        cands = conn.execute(
            select(orgs.c.id, orgs.c.name, orgs.c.plan, orgs.c.billing_org_id, orgs.c.owner_user_id,
                   users.c.id.label("uid"), users.c.email, users.c.plan.label("user_plan"), users.c.org_id.label("primary_org"))
            .select_from(orgs.outerjoin(users, users.c.id == orgs.c.owner_user_id))
            .order_by(orgs.c.id)).all()
        for o in cands:
            email = (o.email or "").strip().lower()
            org_legacy = o.plan if _legacy_paid(o.plan) else "free"
            user_legacy = o.user_plan if _legacy_paid(o.user_plan) else "free"
            is_primary = o.uid is not None and o.primary_org == o.id
            already_v2 = _pricing.parse_plan(o.plan)[0] == "v2"
            # A company is a candidate if it (or, for a person's primary company, its owner) holds a legacy paid plan.
            legacy = _pricing.best_plan(org_legacy, user_legacy if is_primary else "free")
            if legacy == "free" and not already_v2:
                continue
            row = {"org_id": o.id, "org_name": o.name, "owner_user_id": o.uid, "owner_email": o.email,
                   "legacy_plan": legacy, "current_plan": o.plan, "proposed_plan": None, "proposed_label": None,
                   "members": conn.execute(select(func.count()).select_from(members).where(members.c.org_id == o.id)).scalar() or 0,
                   "action": "skip", "reason": None, "override": False}
            if email in overrides:
                matched.add(email)          # an override only ever applies to an account that IS on a legacy paid plan
            if already_v2:
                row.update(action="already_v2", reason="already on a v2 tier", proposed_plan=o.plan,
                           proposed_label=_pricing.tier_label(o.plan))
                rows.append(row)
                continue
            if o.uid is None:
                row["reason"] = "the company has no owner account"
            elif not is_primary:
                row["reason"] = "not the owner's primary company (an extra company inherits through its payer)"
            elif o.billing_org_id is not None:
                row["reason"] = "another company pays for this one, so its own plan isn't used"
            else:
                tier = overrides.get(email) if email in overrides else propose_tier(legacy)
                row["override"] = email in overrides
                if tier is None or tier not in MAPPABLE_TIERS:
                    row["reason"] = "no valid tier to map to"
                else:
                    target = _pricing.plan_id_v2(tier)
                    row.update(proposed_plan=target, proposed_label=_pricing.tier_label(target))
                    if (_pricing.plan_strength(target) < _pricing.plan_strength(legacy)) and not allow_downgrade:
                        lost = [k for k, v in _pricing.entitlements(legacy).items() if v and not _pricing.entitlements(target)[k]]
                        row.update(action="blocked", reason="would take away: " + ", ".join(lost or ["rank"]) +
                                   " (use --allow-downgrade only if that is intended)")
                    else:
                        row["action"] = "map"
            rows.append(row)
    for email in overrides:
        if email not in matched:
            errors.append(f"override for {email}: no company owned by that email is on a legacy paid plan (or it doesn't exist)")
    return {"rows": rows, "errors": errors}


def apply(A, overrides=None, allow_downgrade=False, dry_run=True):
    """Write the mapping. `dry_run` (the default) changes nothing. Each company
    is updated only if its plan is still what the report saw (so a re-run, or a
    plan bought a second ago, can never be overwritten by a stale report), and
    each change is logged (activity_log 'plan_mapped', from -> to) — which is
    also what rollback() reads."""
    report = build_report(A, overrides, allow_downgrade)
    out = {"dry_run": dry_run, "mapped": [], "unchanged": [], "blocked": [], "skipped": [], "changed_underneath": [],
           "errors": report["errors"]}
    engine = A._require_engine()
    orgs = A._get_tables()["organizations"]
    for r in report["rows"]:
        if r["action"] == "map":
            if dry_run:
                out["mapped"].append(r)
                continue
            with engine.begin() as conn:
                done = conn.execute(orgs.update().where(orgs.c.id == r["org_id"], orgs.c.plan == r["current_plan"])
                                    .values(plan=r["proposed_plan"]))
                if not done.rowcount:
                    out["changed_underneath"].append(r)
                    continue
                A.log_activity(r["owner_user_id"], ACTION, "organization", r["org_id"],
                               {"from": r["current_plan"], "to": r["proposed_plan"], "legacy": r["legacy_plan"],
                                "price_book": _pricing.PRICE_BOOK_VERSION, "override": r["override"]}, conn=conn)
            out["mapped"].append(r)
        elif r["action"] == "already_v2":
            out["unchanged"].append(r)
        elif r["action"] == "blocked":
            out["blocked"].append(r)
        else:
            out["skipped"].append(r)
    return out


def rollback(A, dry_run=True):
    """Undo mappings: for every company whose plan is STILL what its latest
    logged mapping set it to, put back what it was (and log that, so the trail
    stays whole and a second rollback does nothing). A company whose plan has
    changed since — someone bought, or was moved by hand — is left alone and
    reported."""
    engine = A._require_engine()
    tables = A._get_tables()
    orgs, log = tables["organizations"], tables["activity_log"]
    from sqlalchemy import select
    out = {"dry_run": dry_run, "restored": [], "left_alone": []}
    with engine.begin() as conn:
        rows = conn.execute(select(log.c.action, log.c.user_id, log.c.target_id, log.c.metadata)
                            .where(log.c.action.in_((ACTION, ROLLED_BACK))).order_by(log.c.id.desc())).all()
        seen = set()
        for action, user_id, org_id, meta in rows:
            if org_id in seen:
                continue
            seen.add(org_id)                        # only the most recent event for each company decides
            if action == ROLLED_BACK or not isinstance(meta, dict):
                continue
            current = conn.execute(select(orgs.c.plan).where(orgs.c.id == org_id)).scalar()
            item = {"org_id": org_id, "from": meta.get("to"), "back_to": meta.get("from"), "current": current}
            if current == meta.get("to"):
                if not dry_run:
                    conn.execute(orgs.update().where(orgs.c.id == org_id).values(plan=meta.get("from")))
                    A.log_activity(user_id, ROLLED_BACK, "organization", org_id,
                                   {"from": meta.get("to"), "to": meta.get("from")}, conn=conn)
                out["restored"].append(item)
            else:
                out["left_alone"].append(item)
    return out

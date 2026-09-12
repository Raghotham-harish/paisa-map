"""
_session.py — shared session/DB-availability decorators for the auth and
workspace blueprints.

Each blueprint module does its own sys.path insert for paisamap-etl/etl rather
than relying on server.py having done it first — cheap, idempotent, and avoids
an import-order coupling that isn't obvious from reading a blueprint file alone.
"""

import sys
from pathlib import Path
from functools import wraps
from flask import session, jsonify, request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "paisamap-etl" / "etl"))
try:
    import _auth_db
except ImportError:
    _auth_db = None
try:
    import _api_keys
except ImportError:
    _api_keys = None


def require_db(fn):
    """DB-required routes fail loudly (503) if DATABASE_URL isn't configured —
    there's no CSV fallback for auth/workspace data, so a silent no-op here
    would be worse than an explicit error."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if _auth_db is None:
            return jsonify({"error": "auth_unavailable", "detail": "sqlalchemy not importable"}), 503
        if not _auth_db.enabled():
            return jsonify({"error": "auth_unavailable", "detail": "DATABASE_URL not configured"}), 503
        return fn(*args, **kwargs)
    return wrapper


def require_login(fn):
    """Injects the current user_id as the first positional arg. 401 if no
    session; implies require_db (nothing to look up without it)."""
    @wraps(fn)
    @require_db
    def wrapper(*args, **kwargs):
        uid = session.get("user_id")
        if not uid:
            return jsonify({"error": "not_authenticated"}), 401
        return fn(uid, *args, **kwargs)
    return wrapper


def require_plan(min_plan):
    """Decorator factory — must sit UNDER @require_login in the stacking order
    (@require_login above, @require_plan below) since it needs user_id already
    injected as the route's first positional arg. 403 if the caller's plan
    ranks below min_plan. Not used by any route yet (no Pro-only workspace
    route exists as of Phase 3) — added alongside get_effective_plan() below
    since both need the same plan_rank() concept, and this gives any future
    Pro-gated workspace route a ready decorator."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(user_id, *args, **kwargs):
            import _pricing
            user = _auth_db.get_user(user_id)
            if user is None or _pricing.plan_rank(user["plan"]) < _pricing.plan_rank(min_plan):
                return jsonify({"error": "plan_required",
                                 "detail": f"requires {min_plan} plan or higher"}), 403
            return fn(user_id, *args, **kwargs)
        return wrapper
    return decorator


def get_effective_plan():
    """Returns the caller's plan without requiring a session — 'free' for an
    anonymous request or when the DB is unavailable, the real users.plan for a
    logged-in one. Use this (not require_login) for an endpoint like
    /api/export that must stay public but still needs to filter server-side by
    whatever plan is actually available.

    Also checks an X-API-Key header (Track 1's B2B data-API product, see
    _api_keys.py) — a valid key elevates access to its owner's live plan, the
    same as that user's own session would, checked ahead of the session cookie
    so a request that legitimately carries both never has the weaker one win.
    This is the ONLY place API-key auth plugs in — it must never be added to
    require_login/require_db below. Those gate identity-bearing surfaces
    (saved locations, customer uploads, billing) that an API key was never
    meant to reach; merging the two paths would turn a leaked key (meant only
    to unlock Pro-tier map columns) into access to someone's private
    workspace data."""
    if _api_keys is not None:
        key = _api_keys.resolve(request)
        if key:
            return key["plan"]
    uid = session.get("user_id")
    if not uid or _auth_db is None or not _auth_db.enabled():
        return "free"
    user = _auth_db.get_user(uid)
    return user["plan"] if user else "free"


def log_data_access(action, target_type=None, target_id=None, metadata=None):
    """Phase D4 — records a data-access event (export/download/view) for the
    admin-visible org audit log, attributing it to whichever identity the
    request actually carries: an X-API-Key's owner first (same precedence as
    get_effective_plan()), else the session user, else None for a genuinely
    anonymous caller (still logged — just won't show up in any org's audit
    log, since there's no member to attribute it to). Best-effort like
    intelligence.py's _log_if_signed_in: never lets a logging failure affect
    the actual export/download response."""
    if _auth_db is None or not _auth_db.enabled():
        return
    try:
        user_id = None
        if _api_keys is not None:
            key = _api_keys.resolve(request)
            if key:
                user_id = key["user_id"]
        if user_id is None:
            user_id = session.get("user_id")
        _auth_db.log_activity(user_id, action, target_type=target_type, target_id=target_id, metadata=metadata)
    except Exception:
        pass


def require_api_key(fn):
    """For a future route that should require a key specifically, rather than
    get_effective_plan()'s "elevate if present, else stay anonymous"
    semantics. 401 if no valid key is presented. Injects the resolved key
    dict ({"key_id","user_id","plan"}) as the first positional arg — deliberately
    NOT the same shape as require_login's bare user_id, so a route can't
    accidentally use the two decorators interchangeably."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if _api_keys is None:
            return jsonify({"error": "auth_unavailable"}), 503
        key = _api_keys.resolve(request)
        if not key:
            return jsonify({"error": "invalid_api_key"}), 401
        return fn(key, *args, **kwargs)
    return wrapper


def charge_credits(user_id, action_key, ref_type=None, ref_id=None):
    """Thin wrapper: looks up the cost of `action_key` and spends it. Not a
    decorator — deliberately called at a different point in a handler body
    than the balance check (see reports.py/expansion.py): check the balance
    early (fail fast, before expensive work), charge only after the action
    actually succeeds, since a failed report generation must not burn
    credits."""
    import _pricing
    cost = _pricing.credit_cost(action_key)
    return _auth_db.spend_credits(user_id, cost, reason=action_key, ref_type=ref_type, ref_id=ref_id)

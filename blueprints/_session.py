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
from flask import session, jsonify

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "paisamap-etl" / "etl"))
try:
    import _auth_db
except ImportError:
    _auth_db = None


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

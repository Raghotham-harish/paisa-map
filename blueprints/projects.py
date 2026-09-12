"""
projects.py — /api/projects CRUD, ownership-scoped by session user_id.
"""

import json
import secrets
from urllib.parse import urlsplit

from flask import Blueprint, request, jsonify

from ._session import require_db, require_login, _auth_db

projects_bp = Blueprint("projects", __name__, url_prefix="/api/projects")

# JSON-array columns on the projects row — stored as text (see _auth_db's
# "no JSON column type, for SQLite portability" convention), decoded back to
# real arrays in every API response by _shape() below.
_ARRAY_FIELDS = ("signals", "target_pincodes")
_OUTCOME_GOALS = ("revenue_reach", "store_count", "balanced")
_REVENUE_PERIODS = ("monthly", "annual")


def _parse_avg_ticket(body):
    """avg_ticket is optional and user-typed — coerce cleanly, don't 500 on
    a stray non-numeric string from the form."""
    raw = body.get("avg_ticket")
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _num_or_none(raw):
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _int_or_none(raw):
    n = _num_or_none(raw)
    return int(n) if n is not None else None


def _str_or_none(raw):
    return (raw or "").strip() or None if isinstance(raw, str) else None


def _json_array(raw):
    """Accepts a list (from JSON body) of scalars; returns a JSON string of
    trimmed, de-duplicated, non-empty strings, or None when empty. A stray
    non-list is treated as "not provided" rather than an error."""
    if not isinstance(raw, list):
        return None
    seen, out = set(), []
    for item in raw:
        s = str(item).strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return json.dumps(out) if out else None


def _wizard_fields(body):
    """Shared shaping for the project-setup wizard fields, used by both create
    and update. Only keys actually present in `body` are returned, so a partial
    PUT doesn't wipe fields the caller didn't send."""
    out = {}
    if "industry" in body:
        out["industry"] = _str_or_none(body.get("industry"))
    if "signals" in body:
        out["signals"] = _json_array(body.get("signals"))
    if "target_pincodes" in body:
        out["target_pincodes"] = _json_array(body.get("target_pincodes"))
    if "catchment_km" in body:
        out["catchment_km"] = _num_or_none(body.get("catchment_km"))
    if "total_investment" in body:
        out["total_investment"] = _num_or_none(body.get("total_investment"))
    if "outcome_goal" in body:
        goal = _str_or_none(body.get("outcome_goal"))
        out["outcome_goal"] = goal if goal in _OUTCOME_GOALS else None
    if "time_horizon_months" in body:
        out["time_horizon_months"] = _int_or_none(body.get("time_horizon_months"))
    if "gross_margin_pct" in body:
        pct = _num_or_none(body.get("gross_margin_pct"))
        out["gross_margin_pct"] = pct if pct is not None and 0 < pct <= 100 else None
    if "revenue_period" in body:
        period = _str_or_none(body.get("revenue_period"))
        out["revenue_period"] = period if period in _REVENUE_PERIODS else None
    return out


def _parse_website_url(body):
    """Trims, adds a scheme if the user just typed "example.com", and rejects
    anything not http(s) — this gets rendered as a clickable link, so a
    javascript:/data: scheme here would be a stored-XSS vector, not just bad data.

    Uses urlsplit rather than a startswith("https://") check on the raw string:
    a bare prefix check is fooled by input with no "//" at all (e.g.
    "javascript:alert(1)" has a scheme but no "//", so naively prepending
    "https://" when "://" is absent turns it into "https://javascript:alert(1)"
    — which still starts with "https://" and would wrongly pass a substring
    check). Parse first, THEN decide whether a scheme needs adding.
    """
    raw = (body.get("website_url") or "").strip()
    if not raw:
        return None
    parsed = urlsplit(raw)
    if not parsed.scheme:
        # No scheme at all (e.g. "example.com") — safe to assume https.
        parsed = urlsplit("https://" + raw)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return parsed.geturl()


def _shape(project):
    """Decode the JSON-array columns back to real arrays for the API response."""
    if project is None:
        return None
    out = dict(project)
    for field in _ARRAY_FIELDS:
        raw = out.get(field)
        if isinstance(raw, str):
            try:
                out[field] = json.loads(raw)
            except (ValueError, TypeError):
                out[field] = []
        elif raw is None:
            out[field] = []
    return out


@projects_bp.route("", methods=["GET"])
@require_login
def list_projects(user_id):
    include_archived = request.args.get("include_archived") == "1"
    return jsonify({"projects": [_shape(p) for p in _auth_db.list_projects(user_id, include_archived=include_archived)]})


@projects_bp.route("", methods=["POST"])
@require_login
def create_project(user_id):
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    description = (body.get("description") or "").strip() or None
    org_id = body.get("org_id")
    org_id = int(org_id) if isinstance(org_id, (int, float, str)) and str(org_id).strip().isdigit() else None
    project = _auth_db.create_project(
        user_id, name, description, org_id=org_id,
        business_type=(body.get("business_type") or "").strip() or None,
        target_segment=(body.get("target_segment") or "").strip() or None,
        avg_ticket=_parse_avg_ticket(body),
        website_url=_parse_website_url(body),
        **_wizard_fields(body),
    )
    return jsonify({"project": _shape(project)}), 201


@projects_bp.route("/<int:project_id>", methods=["GET"])
@require_login
def get_project(user_id, project_id):
    project = _auth_db.get_project(project_id, user_id)
    if project is None:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"project": _shape(project)})


@projects_bp.route("/<int:project_id>", methods=["PUT"])
@require_login
def update_project(user_id, project_id):
    if _auth_db.get_project(project_id, user_id) is None:
        return jsonify({"error": "not_found"}), 404
    body = request.get_json(silent=True) or {}
    project = _auth_db.update_project(
        project_id, user_id,
        name=body.get("name"), description=body.get("description"),
        business_type=body.get("business_type"), target_segment=body.get("target_segment"),
        avg_ticket=_parse_avg_ticket(body),
        website_url=_parse_website_url(body) if "website_url" in body else None,
        **_wizard_fields(body),
    )
    return jsonify({"project": _shape(project)})


@projects_bp.route("/<int:project_id>", methods=["DELETE"])
@require_login
def delete_project(user_id, project_id):
    deleted = _auth_db.delete_project(project_id, user_id)
    if not deleted:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"status": "ok"})


@projects_bp.route("/<int:project_id>/archive", methods=["POST"])
@require_login
def archive_project(user_id, project_id):
    project = _auth_db.archive_project(project_id, user_id)
    if project is None:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"project": _shape(project)})


@projects_bp.route("/<int:project_id>/unarchive", methods=["POST"])
@require_login
def unarchive_project(user_id, project_id):
    project = _auth_db.unarchive_project(project_id, user_id)
    if project is None:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"project": _shape(project)})


@projects_bp.route("/<int:project_id>/duplicate", methods=["POST"])
@require_login
def duplicate_project(user_id, project_id):
    project = _auth_db.duplicate_project(project_id, user_id)
    if project is None:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"project": _shape(project)}), 201


@projects_bp.route("/<int:project_id>/share", methods=["POST"])
@require_login
def share_project(user_id, project_id):
    """E2 — owner/admin only, see set_project_share_token. A project that
    already has a token gets it reused (idempotent "get the current link"),
    same convention as reports' share_report."""
    existing = _auth_db.get_project(project_id, user_id)
    if existing is None:
        return jsonify({"error": "not_found"}), 404
    token = existing.get("share_token") or secrets.token_urlsafe(24)
    project = _auth_db.set_project_share_token(project_id, user_id, token)
    if project is None:
        return jsonify({"error": "forbidden"}), 403
    return jsonify({"project": _shape(project)})


@projects_bp.route("/<int:project_id>/share", methods=["DELETE"])
@require_login
def unshare_project(user_id, project_id):
    if _auth_db.get_project(project_id, user_id) is None:
        return jsonify({"error": "not_found"}), 404
    project = _auth_db.set_project_share_token(project_id, user_id, None)
    if project is None:
        return jsonify({"error": "forbidden"}), 403
    return jsonify({"project": _shape(project)})


@projects_bp.route("/shared/<token>", methods=["GET"])
@require_db
def view_shared_project(token):
    """Public, unauthenticated — no @require_login, same pattern as
    reports.py's view_shared_report. The token itself is the credential."""
    project = _auth_db.get_project_by_share_token(token)
    if project is None:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"project": project})

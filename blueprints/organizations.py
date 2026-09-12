"""
organizations.py — /api/organizations CRUD + membership (Phase C, staged).

Additive only: real orgs + membership, but nothing else in the app resolves
plan/credits/project ownership from an org yet — see the "Organizations"
section of _auth_db.py for why that cutover is deliberately a separate, later
step. This blueprint just gives the frontend a real company switcher to build
against.
"""

from flask import Blueprint, request, jsonify

from ._session import require_db, require_login, _auth_db

organizations_bp = Blueprint("organizations", __name__, url_prefix="/api/organizations")

_ERROR_STATUS = {
    "forbidden": 403,
    "not_a_member": 404,
    "user_not_found": 404,
    "already_a_member": 409,
    "cannot_remove_last_owner": 409,
    "cannot_demote_last_owner": 409,
    "invalid_role": 400,
    "not_found": 404,
    "invalid_invite": 404,
    "email_mismatch": 409,
}


def _error_response(result):
    """Maps one of _auth_db's {"error": "..."} results to a status code —
    every write function in the Organizations section returns this shape on
    failure instead of raising, so the mapping lives in one place."""
    code = result.get("error", "bad_request")
    return jsonify(result), _ERROR_STATUS.get(code, 400)


@organizations_bp.route("", methods=["GET"])
@require_login
def list_organizations(user_id):
    return jsonify({"organizations": _auth_db.list_organizations(user_id)})


@organizations_bp.route("", methods=["POST"])
@require_login
def create_organization(user_id):
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    org = _auth_db.create_organization(user_id, name)
    return jsonify({"organization": org}), 201


@organizations_bp.route("/<int:org_id>", methods=["GET"])
@require_login
def get_organization(user_id, org_id):
    org = _auth_db.get_organization(org_id, user_id)
    if org is None:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"organization": org})


@organizations_bp.route("/<int:org_id>", methods=["PUT"])
@require_login
def update_organization(user_id, org_id):
    body = request.get_json(silent=True) or {}
    org = _auth_db.update_organization(
        org_id, user_id,
        name=(body.get("name") or "").strip() or None,
        website_url=(body.get("website_url") or "").strip() or None,
    )
    if org is None:
        return jsonify({"error": "forbidden"}), 403
    return jsonify({"organization": org})


@organizations_bp.route("/<int:org_id>", methods=["DELETE"])
@require_login
def delete_organization(user_id, org_id):
    deleted = _auth_db.delete_organization(org_id, user_id)
    if not deleted:
        return jsonify({"error": "forbidden"}), 403
    return jsonify({"status": "ok"})


@organizations_bp.route("/<int:org_id>/members", methods=["GET"])
@require_login
def list_members(user_id, org_id):
    members = _auth_db.list_org_members(org_id, user_id)
    if members is None:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"members": members})


@organizations_bp.route("/<int:org_id>/members", methods=["POST"])
@require_login
def add_member(user_id, org_id):
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip()
    if not email:
        return jsonify({"error": "email is required"}), 400
    role = (body.get("role") or "member").strip()
    result = _auth_db.add_org_member(org_id, user_id, email, role)
    if "error" in result:
        return _error_response(result)
    return jsonify(result), 201


@organizations_bp.route("/<int:org_id>/members/<int:member_user_id>", methods=["PUT"])
@require_login
def update_member_role(user_id, org_id, member_user_id):
    body = request.get_json(silent=True) or {}
    role = (body.get("role") or "").strip()
    result = _auth_db.update_org_member_role(org_id, user_id, member_user_id, role)
    if "error" in result:
        return _error_response(result)
    return jsonify(result)


@organizations_bp.route("/<int:org_id>/members/<int:member_user_id>", methods=["DELETE"])
@require_login
def remove_member(user_id, org_id, member_user_id):
    result = _auth_db.remove_org_member(org_id, user_id, member_user_id)
    if "error" in result:
        return _error_response(result)
    return jsonify(result)


@organizations_bp.route("/<int:org_id>/invites", methods=["GET"])
@require_login
def list_invites(user_id, org_id):
    invites = _auth_db.list_org_invites(org_id, user_id)
    if invites is None:
        return jsonify({"error": "forbidden"}), 403
    return jsonify({"invites": invites})


@organizations_bp.route("/<int:org_id>/invites", methods=["POST"])
@require_login
def create_invite(user_id, org_id):
    """D1 — invite someone with no PaisaMap account yet. Calling this again
    for an email that already has a pending invite resends it (new token,
    extended expiry). An email that already has an account is added
    directly instead (see create_or_resend_org_invite)."""
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip()
    if not email:
        return jsonify({"error": "email is required"}), 400
    role = (body.get("role") or "member").strip()
    result = _auth_db.create_or_resend_org_invite(org_id, user_id, email, role)
    if "error" in result:
        return _error_response(result)
    return jsonify(result), 201


@organizations_bp.route("/<int:org_id>/invites/<int:invite_id>", methods=["DELETE"])
@require_login
def revoke_invite(user_id, org_id, invite_id):
    result = _auth_db.revoke_org_invite(org_id, user_id, invite_id)
    if "error" in result:
        return _error_response(result)
    return jsonify(result)


@organizations_bp.route("/invites/<token>", methods=["GET"])
@require_db
def get_invite(token):
    """Public — no @require_login. The landing page needs to show who
    invited whom to what before the visitor has signed in at all."""
    invite = _auth_db.get_invite_by_token(token)
    if invite is None:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"invite": invite})


@organizations_bp.route("/invites/<token>/accept", methods=["POST"])
@require_login
def accept_invite(user_id, token):
    result = _auth_db.accept_org_invite(token, user_id)
    if "error" in result:
        return _error_response(result)
    return jsonify(result)


@organizations_bp.route("/invites/<token>/decline", methods=["POST"])
@require_db
def decline_invite(token):
    """Public — declining doesn't grant access, so no login is required,
    same as reports.py's public share-view route."""
    result = _auth_db.decline_org_invite(token)
    if "error" in result:
        return _error_response(result)
    return jsonify(result)


@organizations_bp.route("/<int:org_id>/audit-log", methods=["GET"])
@require_login
def audit_log(user_id, org_id):
    """Phase D4 — owner/admin only. Data-access events (exports, downloads,
    report views, location scores/compares) for every member of this org."""
    limit = min(request.args.get("limit", 100, type=int) or 100, 500)
    entries = _auth_db.list_org_audit_log(org_id, user_id, limit=limit)
    if entries is None:
        return jsonify({"error": "forbidden"}), 403
    return jsonify({"entries": entries})

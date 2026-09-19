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
    "invalid_amount": 400,
    "invalid_period": 400,
    "invalid_end_date": 400,
    "exceeds_company_budget": 400,
    "company_budget_required": 409,
    "invalid_email": 400,
    "invalid_company": 400,
    "invalid_period": 400,
    "invalid_payer": 400,
    "payer_has_payer": 409,
    "already_a_payer": 409,
    "company_has_credits": 409,
    "request_invalid": 409,
    "not_linked": 409,
    "already_linked": 409,
    "expired": 410,
    "too_many_pending": 429,
    "rate_limited": 429,
    "no_website": 409,
    "invalid_website": 400,
    "not_started": 409,
    "website_changed": 409,
    "domain_taken": 409,
    "too_soon": 429,
    "no_match": 404,
    "invalid_value": 400,
    "no_company": 409,
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
    # Only the owner may even learn a company has billing state; anyone else
    # falls through to the same 403 as before.
    if _auth_db.get_org_role(org_id, user_id) == "owner":
        blocker = _auth_db.org_delete_blocker(org_id)
        if blocker:
            return jsonify({"error": blocker,
                            "detail": "This company pays for other companies — unlink them first."
                                      if blocker == "pays_for_companies" else
                                      "This company has billing history (credits or orders) and can't be deleted."}), 409
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


# ── Credit budgets (billing-v2) ─────────────────────────────────────────────
# Managed by an owner/admin of the PAYING company (the wallet). Non-members get
# the same 404 as a company that doesn't exist; plain members get 403.
@organizations_bp.route("/<int:wallet_id>/budgets", methods=["GET"])
@require_login
def list_budgets(user_id, wallet_id):
    result = _auth_db.list_wallet_budgets(user_id, wallet_id)
    return _error_response(result) if "error" in result else jsonify(result)


@organizations_bp.route("/<int:wallet_id>/budgets/<int:org_id>", methods=["PUT"])
@require_login
def set_budget(user_id, wallet_id, org_id):
    body = request.get_json(silent=True) or {}
    result = _auth_db.set_credit_budget(
        user_id, wallet_id, org_id, body.get("amount"),
        period=body.get("period") or "billing_cycle", ends_at=body.get("ends_at"))
    return _error_response(result) if "error" in result else jsonify(result)


@organizations_bp.route("/<int:wallet_id>/budgets/<int:org_id>", methods=["DELETE"])
@require_login
def delete_budget(user_id, wallet_id, org_id):
    result = _auth_db.delete_credit_budget(user_id, wallet_id, org_id)
    return _error_response(result) if "error" in result else jsonify(result)


@organizations_bp.route("/<int:wallet_id>/reserve", methods=["PUT"])
@require_login
def set_reserve(user_id, wallet_id):
    body = request.get_json(silent=True) or {}
    result = _auth_db.set_wallet_reserve(user_id, wallet_id, body.get("credits"))
    return _error_response(result) if "error" in result else jsonify(result)


# ── Personal allowances inside a company (billing-v2 budgets) ────────────────
# Managed by an owner/admin of the company itself (a client's own admin) or of
# the wallet that pays for it; never above the company's own budget.
@organizations_bp.route("/<int:org_id>/member-budgets", methods=["GET"])
@require_login
def list_member_budgets(user_id, org_id):
    result = _auth_db.list_member_budgets(user_id, org_id)
    return _error_response(result) if "error" in result else jsonify(result)


@organizations_bp.route("/<int:org_id>/member-budgets/<int:member_user_id>", methods=["PUT"])
@require_login
def set_member_budget(user_id, org_id, member_user_id):
    body = request.get_json(silent=True) or {}
    result = _auth_db.set_member_budget(
        user_id, org_id, member_user_id, body.get("amount"),
        period=body.get("period") or "billing_cycle", ends_at=body.get("ends_at"))
    return _error_response(result) if "error" in result else jsonify(result)


@organizations_bp.route("/<int:org_id>/member-budgets/<int:member_user_id>", methods=["DELETE"])
@require_login
def delete_member_budget(user_id, org_id, member_user_id):
    result = _auth_db.delete_member_budget(user_id, org_id, member_user_id)
    return _error_response(result) if "error" in result else jsonify(result)


# ── Who pays for a company: link requests, detaching, usage statement ───────
# A paying company asks by EMAIL; the person addressed picks which company of
# theirs to link and approves. Either side can detach at any time.
@organizations_bp.route("/<int:payer_id>/link-requests", methods=["POST"])
@require_login
def create_link_request(user_id, payer_id):
    body = request.get_json(silent=True) or {}
    result = _auth_db.create_link_request(user_id, payer_id, body.get("email"), body.get("note"))
    if "error" in result:
        return _error_response(result)
    return jsonify(result), 201


@organizations_bp.route("/<int:payer_id>/link-requests", methods=["GET"])
@require_login
def list_outgoing_link_requests(user_id, payer_id):
    result = _auth_db.list_outgoing_link_requests(user_id, payer_id)
    return _error_response(result) if "error" in result else jsonify(result)


@organizations_bp.route("/<int:payer_id>/link-requests/<int:request_id>", methods=["DELETE"])
@require_login
def cancel_link_request(user_id, payer_id, request_id):
    result = _auth_db.cancel_link_request(user_id, payer_id, request_id)
    return _error_response(result) if "error" in result else jsonify(result)


@organizations_bp.route("/link-requests/incoming", methods=["GET"])
@require_login
def list_incoming_link_requests(user_id):
    return jsonify(_auth_db.list_incoming_link_requests(user_id))


@organizations_bp.route("/link-requests/<int:request_id>/approve", methods=["POST"])
@require_login
def approve_link_request(user_id, request_id):
    body = request.get_json(silent=True) or {}
    result = _auth_db.approve_link_request(user_id, request_id, body.get("org_id"))
    return _error_response(result) if "error" in result else jsonify(result)


@organizations_bp.route("/link-requests/<int:request_id>/decline", methods=["POST"])
@require_login
def decline_link_request(user_id, request_id):
    result = _auth_db.decline_link_request(user_id, request_id)
    return _error_response(result) if "error" in result else jsonify(result)


# ── Website verification, and asking a company to connect by its website ─────
def _search_console_owns(org_id, domain):
    """True if a Search Console account connected to this company is a verified
    OWNER of `domain`. Any failure to reach Google just means 'not proven this way'."""
    import _google_oauth
    import _site_verify
    from . import analytics_connections as ac
    for conn in _auth_db.list_org_search_console_connections(org_id):
        token = ac._access_token_for_connection(conn)
        if token is None:
            continue
        try:
            sites = _google_oauth.list_gsc_sites(token)
        except _google_oauth.GoogleOAuthError:
            continue
        if _site_verify.gsc_owner_of(sites, domain):
            return True
    return False


@organizations_bp.route("/<int:org_id>/website-verification", methods=["GET"])
@require_login
def get_website_verification(user_id, org_id):
    result = _auth_db.get_domain_verification(user_id, org_id)
    return _error_response(result) if "error" in result else jsonify(result)


@organizations_bp.route("/<int:org_id>/website-verification", methods=["POST"])
@require_login
def start_website_verification(user_id, org_id):
    result = _auth_db.start_domain_verification(user_id, org_id)
    return _error_response(result) if "error" in result else jsonify(result)


@organizations_bp.route("/<int:org_id>/website-verification/check", methods=["POST"])
@require_login
def check_website_verification(user_id, org_id):
    result = _auth_db.check_domain_verification(user_id, org_id, gsc_check=_search_console_owns)
    return _error_response(result) if "error" in result else jsonify(result)


@organizations_bp.route("/<int:org_id>/website-verification", methods=["PATCH"])
@require_login
def update_website_verification(user_id, org_id):
    body = request.get_json(silent=True) or {}
    result = _auth_db.set_domain_discoverable(user_id, org_id, body.get("discoverable"))
    return _error_response(result) if "error" in result else jsonify(result)


@organizations_bp.route("/<int:org_id>/website-verification", methods=["DELETE"])
@require_login
def remove_website_verification(user_id, org_id):
    result = _auth_db.remove_domain_verification(user_id, org_id)
    return _error_response(result) if "error" in result else jsonify(result)


@organizations_bp.route("/<int:payer_id>/link-requests/find-website", methods=["POST"])
@require_login
def find_company_by_website(user_id, payer_id):
    body = request.get_json(silent=True) or {}
    result = _auth_db.find_company_by_website(user_id, payer_id, body.get("website"))
    return _error_response(result) if "error" in result else jsonify(result)


@organizations_bp.route("/<int:payer_id>/link-requests/by-website", methods=["POST"])
@require_login
def create_website_link_request(user_id, payer_id):
    body = request.get_json(silent=True) or {}
    result = _auth_db.create_website_link_request(user_id, payer_id, body.get("website"), body.get("note"))
    if "error" in result:
        return _error_response(result)
    return jsonify(result), 201


# ── API keys attributed to a company ─────────────────────────────────────────
@organizations_bp.route("/<int:org_id>/api-keys", methods=["GET"])
@require_login
def list_company_api_keys(user_id, org_id):
    result = _auth_db.list_company_api_keys(user_id, org_id)
    return _error_response(result) if "error" in result else jsonify(result)


@organizations_bp.route("/<int:org_id>/api-keys/<int:key_id>", methods=["DELETE"])
@require_login
def revoke_company_api_key(user_id, org_id, key_id):
    result = _auth_db.revoke_company_api_key(user_id, org_id, key_id)
    return _error_response(result) if "error" in result else jsonify(result)


@organizations_bp.route("/<int:org_id>/billing-link", methods=["GET"])
@require_login
def billing_link(user_id, org_id):
    result = _auth_db.billing_link_view(user_id, org_id)
    return _error_response(result) if "error" in result else jsonify(result)


@organizations_bp.route("/<int:org_id>/detach", methods=["POST"])
@require_login
def detach_company(user_id, org_id):
    result = _auth_db.unlink_company(user_id, org_id)
    return _error_response(result) if "error" in result else jsonify(result)


@organizations_bp.route("/<int:org_id>/usage-statement", methods=["GET"])
@require_login
def usage_statement(user_id, org_id):
    result = _auth_db.usage_statement(user_id, org_id, request.args.get("period", "this_month"))
    return _error_response(result) if "error" in result else jsonify(result)

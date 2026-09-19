"""
credits.py — GET /api/credits, the signed-in user's balance + ledger.

/api/auth/me already returns the bare balance (see auth.py's _user_payload,
via _auth_db.get_credit_balance) for header/dashboard display — this is the
fuller transaction-history view for the workspace Credits page.
"""

from flask import Blueprint, request, jsonify

from ._session import require_login, _auth_db

credits_bp = Blueprint("credits", __name__, url_prefix="/api/credits")


@credits_bp.route("", methods=["GET"])
@require_login
def get_credits(user_id):
    limit = min(request.args.get("limit", 50, type=int) or 50, 200)
    # Optional ?org_id= — view the wallet behind a company you belong to (the
    # one selected in the switcher). Ignored under the legacy per-user scope.
    # Same 404 whether it doesn't exist or you're not a member.
    org_id = request.args.get("org_id", type=int)
    if org_id is not None and _auth_db.get_org_role(org_id, user_id) is None:
        return jsonify({"error": "not_found"}), 404
    view = _auth_db.get_credit_view(user_id, org_id=org_id)
    return jsonify({
        "balance": view["balance"],          # None if the wallet belongs to a company you don't belong to
        "paid_by": view["paid_by"],
        "budget": view["budget"],            # this company's own cap + usage (None if none set)
        "member_budget": view["member_budget"],   # this person's own allowance inside it (None if none)
        "ledger": _auth_db.list_credit_ledger(user_id, limit=limit, org_id=org_id),
    })

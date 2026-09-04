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
    return jsonify({
        "balance": _auth_db.get_credit_balance(user_id),
        "ledger": _auth_db.list_credit_ledger(user_id, limit=limit),
    })

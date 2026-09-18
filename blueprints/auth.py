"""
auth.py — POST /api/auth/google, POST /api/auth/logout, GET /api/auth/me.

Verifies the Google ID token server-side (google-auth's verify_oauth2_token),
unlike the previous client-side-only decode in index.html. On first-ever
sign-in, grants a signup bonus so the credits system has something real to
show from day one.
"""

import os
from flask import Blueprint, request, jsonify, session

from ._session import require_db, require_login, _auth_db

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

SIGNUP_BONUS_CREDITS = 50  # placeholder — Phase 3 owns the real credit economy/pricing


def _user_payload(user_id, org_id=None):
    user = _auth_db.get_user(user_id)
    if user is None:
        return None
    view = _auth_db.get_credit_view(user_id, org_id=org_id)
    return {
        "id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "picture_url": user["picture_url"],
        "plan": _auth_db.get_effective_plan_for_user(user_id),
        "credits": view["balance"],
        "credits_paid_by": view["paid_by"],
    }


@auth_bp.route("/google", methods=["POST"])
@require_db
def google_signin():
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    if not client_id:
        return jsonify({"error": "auth_unavailable", "detail": "GOOGLE_CLIENT_ID not configured"}), 503

    body = request.get_json(silent=True) or {}
    credential = body.get("credential", "").strip()
    if not credential:
        return jsonify({"error": "credential required"}), 400

    try:
        from google.oauth2 import id_token as google_id_token
        from google.auth.transport import requests as google_requests
        payload = google_id_token.verify_oauth2_token(
            credential, google_requests.Request(), client_id
        )
    except Exception as e:
        return jsonify({"error": "invalid_credential", "detail": str(e)}), 401

    if not payload.get("email_verified", False):
        return jsonify({"error": "email_not_verified"}), 401

    result = _auth_db.upsert_user(
        google_sub=payload["sub"],
        email=payload["email"],
        name=payload.get("name"),
        picture_url=payload.get("picture"),
    )

    if result["created"]:
        # Phase C: every account gets a default company from day one — matches
        # the pricing model (every tier includes 1 company) and C3's backfill,
        # which did the same for pre-existing accounts. A display name isn't
        # known yet this early for some providers, so fall back to the email
        # local-part exactly like the backfill does.
        display = payload.get("name") or payload["email"].split("@")[0]
        _auth_db.create_default_organization_for_user(result["id"], f"{display}'s Workspace")
        # After the company exists, so the bonus row is stamped with its org_id
        # (billing-v2 stage 2a) — grant_credits resolves users.org_id.
        _auth_db.grant_credits(result["id"], SIGNUP_BONUS_CREDITS, "signup_bonus")

    _auth_db.log_activity(result["id"], "login")

    session.clear()
    session["user_id"] = result["id"]
    session.permanent = True

    user = _user_payload(result["id"])
    return jsonify({"user": user, "plan": user["plan"], "credits": user["credits"]})


@auth_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"status": "ok"})


@auth_bp.route("/me", methods=["GET"])
@require_login
def me(user_id):
    # Optional ?org_id= — the company selected in the switcher, so the credits
    # shown are that company's wallet. A company you don't belong to is simply
    # ignored (no error, no information): you get your own primary company's.
    org_id = request.args.get("org_id", type=int)
    if org_id is not None and _auth_db.get_org_role(org_id, user_id) is None:
        org_id = None
    user = _user_payload(user_id, org_id=org_id)
    if user is None:
        # session pointed at a user_id that no longer exists — clear the stale cookie
        session.clear()
        return jsonify({"error": "not_authenticated"}), 401
    return jsonify({"user": user, "plan": user["plan"], "credits": user["credits"]})

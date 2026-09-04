"""
activity.py — GET /api/activity, the signed-in user's recent activity feed.
"""

from flask import Blueprint, request, jsonify

from ._session import require_login, _auth_db

activity_bp = Blueprint("activity", __name__, url_prefix="/api/activity")


@activity_bp.route("", methods=["GET"])
@require_login
def list_activity(user_id):
    limit = min(request.args.get("limit", 50, type=int) or 50, 200)
    return jsonify({"activity": _auth_db.list_activity(user_id, limit=limit)})

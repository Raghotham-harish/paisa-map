"""
reports.py — GET /api/reports, read-only for now.

No report generation exists yet (Phase 2 wires the intelligence engine that
actually produces one) — this just proves the shell/API in front of an
always-empty reports table until then.
"""

from flask import Blueprint, jsonify

from ._session import require_login, _auth_db

reports_bp = Blueprint("reports", __name__, url_prefix="/api/reports")


@reports_bp.route("", methods=["GET"])
@require_login
def list_reports(user_id):
    return jsonify({"reports": _auth_db.list_reports(user_id)})

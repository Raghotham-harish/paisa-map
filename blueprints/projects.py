"""
projects.py — /api/projects CRUD, ownership-scoped by session user_id.
"""

from flask import Blueprint, request, jsonify

from ._session import require_login, _auth_db

projects_bp = Blueprint("projects", __name__, url_prefix="/api/projects")


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


@projects_bp.route("", methods=["GET"])
@require_login
def list_projects(user_id):
    return jsonify({"projects": _auth_db.list_projects(user_id)})


@projects_bp.route("", methods=["POST"])
@require_login
def create_project(user_id):
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    description = (body.get("description") or "").strip() or None
    project = _auth_db.create_project(
        user_id, name, description,
        business_type=(body.get("business_type") or "").strip() or None,
        target_segment=(body.get("target_segment") or "").strip() or None,
        avg_ticket=_parse_avg_ticket(body),
    )
    return jsonify({"project": project}), 201


@projects_bp.route("/<int:project_id>", methods=["GET"])
@require_login
def get_project(user_id, project_id):
    project = _auth_db.get_project(project_id, user_id)
    if project is None:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"project": project})


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
    )
    return jsonify({"project": project})


@projects_bp.route("/<int:project_id>", methods=["DELETE"])
@require_login
def delete_project(user_id, project_id):
    deleted = _auth_db.delete_project(project_id, user_id)
    if not deleted:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"status": "ok"})

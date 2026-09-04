"""
locations.py — /api/locations CRUD, ownership-scoped by session user_id.

POST with no project_id attaches to the user's auto-created/reused "Saved
Locations" project (see _auth_db.get_or_create_default_project) — this is
what lets the public map's Save button work with zero project-picker UI.
"""

from flask import Blueprint, request, jsonify

from ._session import require_login, _auth_db

locations_bp = Blueprint("locations", __name__, url_prefix="/api/locations")


@locations_bp.route("", methods=["GET"])
@require_login
def list_locations(user_id):
    project_id = request.args.get("project_id", type=int)
    return jsonify({"locations": _auth_db.list_saved_locations(user_id, project_id)})


@locations_bp.route("", methods=["POST"])
@require_login
def create_location(user_id):
    body = request.get_json(silent=True) or {}
    pincode = (body.get("pincode") or "").strip()
    if not pincode:
        return jsonify({"error": "pincode is required"}), 400

    project_id = body.get("project_id")
    if project_id is not None:
        if _auth_db.get_project(project_id, user_id) is None:
            return jsonify({"error": "project not_found"}), 404
    else:
        project_id = _auth_db.get_or_create_default_project(user_id)

    location, created = _auth_db.create_saved_location(
        user_id, project_id, pincode,
        name=body.get("name"), lat=body.get("lat"), lng=body.get("lng"),
    )
    return jsonify({"location": location, "created": created}), 201 if created else 200


@locations_bp.route("/<int:location_id>", methods=["PUT"])
@require_login
def update_location(user_id, location_id):
    if _auth_db.get_saved_location(location_id, user_id) is None:
        return jsonify({"error": "not_found"}), 404
    body = request.get_json(silent=True) or {}
    location = _auth_db.update_saved_location(
        location_id, user_id,
        status=body.get("status"), tags=body.get("tags"), notes=body.get("notes"),
    )
    return jsonify({"location": location})


@locations_bp.route("/<int:location_id>", methods=["DELETE"])
@require_login
def delete_location(user_id, location_id):
    deleted = _auth_db.delete_saved_location(location_id, user_id)
    if not deleted:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"status": "ok"})

"""
reports.py — GET /api/reports, POST /api/reports (generate), GET /api/reports/<id>/download.

Generation is synchronous — a project's saved-location set is a user's own
shortlist (tens, not thousands), and compute_location_intelligence_batch is
already a live in-memory aggregation (no new ML work per intelligence.py's
own docstring), so there's no need for a background job queue here.

PDFs are written under REPORTS_DIR, which must NOT be inside the git-tracked
app tree that deploy.sh rsyncs from — deploy.sh mirrors the fresh git
checkout onto the nginx static root with `rsync --delete`, which would wipe
any generated PDF that only ever existed in the destination copy. Default
(REPORTS_DIR unset) is fine for local dev; production sets REPORTS_DIR to a
sibling directory outside /var/www/paisamap (documented alongside
DATABASE_URL/SECRET_KEY in /etc/paisamap/db.env).
"""

import os
import secrets
import sys
from pathlib import Path

from flask import Blueprint, request, jsonify, send_file

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "paisamap-etl" / "etl"))
import _report_pdf  # noqa: E402

from ._session import require_login, require_db, _auth_db
from .intelligence import compute_location_intelligence_batch  # noqa: E402

reports_bp = Blueprint("reports", __name__, url_prefix="/api/reports")

REPORTS_DIR = Path(os.environ.get("REPORTS_DIR")
                    or (Path(__file__).resolve().parent.parent / "data" / "reports"))


def _business_profile(project):
    if not (project.get("avg_ticket") or project.get("target_segment") or project.get("business_type")):
        return None
    return {
        "avg_ticket": project.get("avg_ticket"),
        "target_segment": project.get("target_segment"),
        "business_type": project.get("business_type"),
    }


@reports_bp.route("", methods=["GET"])
@require_login
def list_reports(user_id):
    return jsonify({"reports": _auth_db.list_reports(user_id)})


@reports_bp.route("", methods=["POST"])
@require_login
def generate_report(user_id):
    body = request.get_json(silent=True) or {}
    project_id = body.get("project_id")
    project = _auth_db.get_project(project_id, user_id) if project_id else None
    if project is None:
        return jsonify({"error": "project not_found"}), 404

    locations = _auth_db.list_saved_locations(user_id, project_id)
    if not locations:
        return jsonify({"error": "no_locations",
                         "detail": "This project has no saved locations yet."}), 400

    business = _business_profile(project)
    pincodes = [loc["pincode"] for loc in locations]
    intel_by_pincode = {i["pincode"]: i for i in compute_location_intelligence_batch(pincodes, business=business)}
    # A saved location's own user-given name wins over the signals dataset's name.
    for loc in locations:
        i = intel_by_pincode.get(loc["pincode"])
        if i and loc.get("name"):
            i["name"] = loc["name"]
    intel_list = [intel_by_pincode[pc] for pc in pincodes if pc in intel_by_pincode]

    title = (body.get("title") or "").strip() or f"{project['name']} — Location Intelligence Report"
    report = _auth_db.create_report(user_id, project_id, title, status="processing",
                                     params={"pincodes": pincodes, "business": business})

    user_dir = REPORTS_DIR / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    file_path = user_dir / f"report_{report['id']}.pdf"
    try:
        _report_pdf.build_project_report_pdf(project, intel_list, str(file_path))
    except Exception as e:
        _auth_db.update_report(report["id"], user_id, status="failed")
        return jsonify({"error": "generation_failed", "detail": str(e)}), 500

    report = _auth_db.update_report(
        report["id"], user_id, status="ready", file_path=str(file_path),
        params={"pincodes": pincodes, "business": business, "locations": intel_list},
    )
    _auth_db.log_activity(user_id, "report_generate", target_type="report", target_id=report["id"],
                           metadata={"project_id": project_id})
    return jsonify({"report": report}), 201


@reports_bp.route("/<int:report_id>/download", methods=["GET"])
@require_login
def download_report(user_id, report_id):
    report = _auth_db.get_report(report_id, user_id)
    if report is None or not report.get("file_path"):
        return jsonify({"error": "not_found"}), 404
    path = Path(report["file_path"])
    if not path.exists():
        return jsonify({"error": "file_missing",
                         "detail": "The report record exists but its file is gone — try regenerating."}), 404
    return send_file(str(path), mimetype="application/pdf", as_attachment=True,
                      download_name=f"{report['title']}.pdf")


@reports_bp.route("/<int:report_id>/share", methods=["POST"])
@require_login
def share_report(user_id, report_id):
    report = _auth_db.get_report(report_id, user_id)
    if report is None:
        return jsonify({"error": "not_found"}), 404
    if report["status"] != "ready":
        return jsonify({"error": "not_ready",
                         "detail": "Only a ready report can be shared."}), 400
    token = report.get("share_token") or secrets.token_urlsafe(24)
    report = _auth_db.set_report_share_token(report_id, user_id, token)
    return jsonify({"report": report})


@reports_bp.route("/<int:report_id>/share", methods=["DELETE"])
@require_login
def unshare_report(user_id, report_id):
    report = _auth_db.get_report(report_id, user_id)
    if report is None:
        return jsonify({"error": "not_found"}), 404
    report = _auth_db.set_report_share_token(report_id, user_id, None)
    return jsonify({"report": report})


@reports_bp.route("/shared/<token>", methods=["GET"])
@require_db
def view_shared_report(token):
    """Public, unauthenticated — no @require_login. The token itself, not a
    session, is the credential; anyone holding the link can view (not
    download-force) the PDF inline in the browser."""
    report = _auth_db.get_report_by_share_token(token)
    if report is None or not report.get("file_path"):
        return jsonify({"error": "not_found"}), 404
    path = Path(report["file_path"])
    if not path.exists():
        return jsonify({"error": "file_missing"}), 404
    return send_file(str(path), mimetype="application/pdf", as_attachment=False,
                      download_name=f"{report['title']}.pdf")

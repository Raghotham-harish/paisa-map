"""
customer_data.py — Phase 05 (round 1): customer store-data upload → column
mapping → validation → geocoding → signal-join "customer location profile".

Upload flow is three requests, not one, because column mapping needs a human
in the loop (arbitrary headers can't be guessed reliably):
  1. POST /uploads          — parse the file, store it, return headers+preview
  2. POST /uploads/<id>/commit — apply the user's chosen mapping, validate,
                                  import rows, kick off geocoding if needed
  3. GET  /uploads/<id>      — poll while status == "geocoding"

Geocoding runs in a background thread rather than inline in the commit
request: prod runs the bare Werkzeug server behind nginx with no
proxy_read_timeout override (60s default), and Nominatim's usage policy caps
requests at ~1/sec — a file with more than ~50 addresses needing geocoding
would blow through that timeout if done synchronously. The DB row (not
in-memory state) is the source of truth, so polling works regardless of
process/worker.
"""

import csv
import io
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, request, jsonify

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "paisamap-etl" / "etl"))
import _geocode  # noqa: E402

from ._session import require_login, _auth_db
from .intelligence import compute_location_intelligence_batch  # noqa: E402

customer_data_bp = Blueprint("customer_data", __name__, url_prefix="/api/customer-data")

# Bounds worst-case geocoding time (500 rows * 1.1s throttle ≈ 9min, fine for
# a background job with polling) and keeps the raw_rows JSONB blob reasonable.
MAX_UPLOAD_ROWS = 500

CANONICAL_FIELDS = ("store_name", "address", "pincode", "revenue", "rent", "capex")

_PINCODE_RE = re.compile(r"^\d{6}$")
_NUMERIC_STRIP_RE = re.compile(r"[^\d.\-]")


def _parse_csv(raw_bytes):
    text = raw_bytes.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return [], []
    headers = [h.strip() for h in rows[0]]
    data_rows = [dict(zip(headers, row)) for row in rows[1:] if any(c.strip() for c in row)]
    return headers, data_rows


def _parse_xlsx(raw_bytes):
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), read_only=True, data_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    try:
        header_row = next(rows_iter)
    except StopIteration:
        return [], []
    headers = [str(h).strip() if h is not None else "" for h in header_row]
    data_rows = []
    for row in rows_iter:
        if row is None or all(c is None for c in row):
            continue
        data_rows.append({headers[i]: ("" if v is None else str(v))
                           for i, v in enumerate(row) if i < len(headers)})
    return headers, data_rows


def _parse_numeric(raw):
    if raw in (None, ""):
        return None, False
    cleaned = _NUMERIC_STRIP_RE.sub("", str(raw))
    if not cleaned or cleaned in ("-", "."):
        return None, True
    try:
        return float(cleaned), False
    except ValueError:
        return None, True


@customer_data_bp.route("/uploads", methods=["POST"])
@require_login
def upload_file(user_id):
    project_id = request.form.get("project_id", type=int)
    if project_id is None or _auth_db.get_project(project_id, user_id) is None:
        return jsonify({"error": "project not_found"}), 404

    f = request.files.get("file")
    if f is None or not f.filename:
        return jsonify({"error": "file is required"}), 400

    filename = f.filename
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ("csv", "xlsx"):
        return jsonify({"error": "unsupported_format",
                         "detail": "Only .csv and .xlsx files are supported."}), 400

    raw_bytes = f.read()
    try:
        headers, rows = (_parse_csv if ext == "csv" else _parse_xlsx)(raw_bytes)
    except Exception as e:
        return jsonify({"error": "parse_failed", "detail": str(e)}), 400

    if not headers or not rows:
        return jsonify({"error": "empty_file",
                         "detail": "No data rows found in the file."}), 400
    if len(rows) > MAX_UPLOAD_ROWS:
        return jsonify({"error": "too_many_rows",
                         "detail": f"This file has {len(rows)} rows; the limit "
                                   f"is {MAX_UPLOAD_ROWS}. Split it and upload in parts."}), 400

    upload = _auth_db.create_customer_upload(user_id, project_id, filename, ext, headers, rows)
    return jsonify({"upload": {
        **upload, "raw_rows": None,  # don't echo the full payload back
        "sample_rows": rows[:5], "row_count": len(rows),
    }}), 201


@customer_data_bp.route("/uploads/<int:upload_id>", methods=["GET"])
@require_login
def get_upload(user_id, upload_id):
    upload = _auth_db.get_customer_upload(upload_id, user_id)
    if upload is None:
        return jsonify({"error": "not_found"}), 404
    raw_rows = upload.get("raw_rows") or []
    return jsonify({"upload": {
        **upload, "raw_rows": None,
        "sample_rows": raw_rows[:5], "row_count": len(raw_rows),
    }})


@customer_data_bp.route("/uploads", methods=["GET"])
@require_login
def list_uploads(user_id):
    project_id = request.args.get("project_id", type=int)
    uploads = _auth_db.list_customer_uploads(user_id, project_id)
    return jsonify({"uploads": [
        {**u, "raw_rows": None, "row_count": len(u.get("raw_rows") or [])} for u in uploads
    ]})


def _run_geocode_job(upload_id, user_id):
    try:
        pending = _auth_db.list_pending_geocode_locations(upload_id, user_id)
        for i, loc in enumerate(pending):
            result = _geocode.geocode_address(loc.get("raw_address"))
            if result and result.get("pincode") and _PINCODE_RE.match(result["pincode"]):
                _auth_db.update_customer_location(
                    loc["id"], user_id, pincode=result["pincode"],
                    lat=result["lat"], lng=result["lng"], geocode_status="geocoded",
                )
            else:
                _auth_db.update_customer_location(loc["id"], user_id, geocode_status="failed")
            if i < len(pending) - 1:
                time.sleep(_geocode.GEOCODE_THROTTLE_SEC)
        _auth_db.update_customer_upload(upload_id, user_id, status="ready")
    except Exception as e:
        _auth_db.update_customer_upload(upload_id, user_id, status="failed", error=str(e))


@customer_data_bp.route("/uploads/<int:upload_id>/commit", methods=["POST"])
@require_login
def commit_upload(user_id, upload_id):
    upload = _auth_db.get_customer_upload(upload_id, user_id)
    if upload is None:
        return jsonify({"error": "not_found"}), 404
    if upload["status"] != "pending_mapping":
        return jsonify({"error": "already_committed",
                         "detail": f"This upload is already {upload['status']}."}), 400

    body = request.get_json(silent=True) or {}
    mapping = {k: v for k, v in (body.get("mapping") or {}).items()
               if k in CANONICAL_FIELDS and v}
    if not mapping.get("address") and not mapping.get("pincode"):
        return jsonify({"error": "mapping_incomplete",
                         "detail": "Map at least one of Address or Pincode."}), 400

    raw_rows = upload.get("raw_rows") or []
    headers = set(upload.get("headers") or [])
    for _field, header in mapping.items():
        if header not in headers:
            return jsonify({"error": "invalid_mapping",
                             "detail": f"Column '{header}' not found in the uploaded file."}), 400

    seen_keys = set()
    duplicate_count = 0
    missing_location = 0
    numeric_parse_failures = {"revenue": 0, "rent": 0, "capex": 0}
    to_insert = []

    for raw in raw_rows:
        store_name = (raw.get(mapping.get("store_name", "")) or "").strip() or None
        raw_pincode = (raw.get(mapping.get("pincode", "")) or "").strip()
        raw_address = (raw.get(mapping.get("address", "")) or "").strip() or None

        pincode = raw_pincode if _PINCODE_RE.match(raw_pincode) else None
        if pincode:
            geocode_status = "direct"
        elif raw_address:
            geocode_status = "pending"
        else:
            geocode_status = "unresolvable"
            missing_location += 1

        revenue, rev_fail = _parse_numeric(raw.get(mapping.get("revenue", "")))
        rent, rent_fail = _parse_numeric(raw.get(mapping.get("rent", "")))
        capex, capex_fail = _parse_numeric(raw.get(mapping.get("capex", "")))
        numeric_parse_failures["revenue"] += rev_fail
        numeric_parse_failures["rent"] += rent_fail
        numeric_parse_failures["capex"] += capex_fail

        dedup_key = ((pincode or raw_address or "").lower(), (store_name or "").lower())
        if dedup_key in seen_keys and dedup_key != ("", ""):
            duplicate_count += 1
        seen_keys.add(dedup_key)

        mapped_headers = set(mapping.values())
        extra_fields = {k: v for k, v in raw.items() if k not in mapped_headers} or None

        to_insert.append(dict(
            store_name=store_name, raw_address=raw_address, pincode=pincode,
            geocode_status=geocode_status, revenue=revenue, rent=rent, capex=capex,
            extra_fields=extra_fields,
        ))

    quality_report = {
        "total_rows": len(raw_rows),
        "missing_location": missing_location,
        "duplicate_count": duplicate_count,
        "numeric_parse_failures": numeric_parse_failures,
    }

    _auth_db.create_customer_locations_bulk(user_id, upload["project_id"], upload_id, to_insert)
    needs_geocoding = any(r["geocode_status"] == "pending" for r in to_insert)
    status = "geocoding" if needs_geocoding else "ready"
    upload = _auth_db.update_customer_upload(
        upload_id, user_id, status=status, mapping=mapping, quality_report=quality_report,
    )
    if needs_geocoding:
        threading.Thread(target=_run_geocode_job, args=(upload_id, user_id), daemon=True).start()

    _auth_db.log_activity(user_id, "customer_data_upload_commit", target_type="customer_upload",
                           target_id=upload_id, metadata={"project_id": upload["project_id"]})
    return jsonify({"upload": {**upload, "raw_rows": None}})


@customer_data_bp.route("/uploads/<int:upload_id>/retry", methods=["POST"])
@require_login
def retry_upload(user_id, upload_id):
    upload = _auth_db.get_customer_upload(upload_id, user_id)
    if upload is None:
        return jsonify({"error": "not_found"}), 404
    pending = _auth_db.list_pending_geocode_locations(upload_id, user_id)
    if not pending:
        return jsonify({"error": "nothing_pending",
                         "detail": "No addresses are waiting on geocoding."}), 400
    updated_at = upload.get("updated_at")
    if updated_at is not None:
        # SQLite (local/dev only — Postgres in prod returns real tz-aware
        # values for DateTime(timezone=True)) hands back a naive datetime
        # despite the column type; treat a naive value as UTC, matching how
        # _now() always writes it, rather than crashing on the subtraction.
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - updated_at
        if age.total_seconds() < 300:
            return jsonify({"error": "still_working",
                             "detail": "This upload was updated less than 5 minutes ago — "
                                       "it may still be geocoding."}), 400
    _auth_db.update_customer_upload(upload_id, user_id, status="geocoding")
    threading.Thread(target=_run_geocode_job, args=(upload_id, user_id), daemon=True).start()
    return jsonify({"status": "retrying"})


@customer_data_bp.route("/uploads/<int:upload_id>", methods=["DELETE"])
@require_login
def delete_upload(user_id, upload_id):
    deleted = _auth_db.delete_customer_upload(upload_id, user_id)
    if not deleted:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"status": "ok"})


@customer_data_bp.route("/locations", methods=["GET"])
@require_login
def list_locations(user_id):
    project_id = request.args.get("project_id", type=int)
    locations = _auth_db.list_customer_locations(user_id, project_id)
    pincodes = sorted({loc["pincode"] for loc in locations if loc.get("pincode")})
    intel_by_pincode = {i["pincode"]: i for i in compute_location_intelligence_batch(pincodes)} \
        if pincodes else {}
    result = []
    for loc in locations:
        intel = intel_by_pincode.get(loc.get("pincode"))
        entry = dict(loc)
        entry["intelligence"] = None if intel is None else {
            "economic_score": intel["economic_score"],
            "risk": intel["risk"],
            "opportunity": intel.get("opportunity"),
            "benchmark_india": intel["benchmark"]["india"],
        }
        result.append(entry)
    return jsonify({"locations": result})


@customer_data_bp.route("/locations/<int:location_id>", methods=["DELETE"])
@require_login
def delete_location(user_id, location_id):
    deleted = _auth_db.delete_customer_location(location_id, user_id)
    if not deleted:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"status": "ok"})

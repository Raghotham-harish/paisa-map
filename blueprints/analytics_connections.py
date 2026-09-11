"""
analytics_connections.py — Phase 05B: OAuth authorization-code linking for a
project's own Google Analytics (GA4) and Search Console data.

This is genuinely new infrastructure, not an extension of blueprints/auth.py's
sign-in flow — that flow only ever verifies a Google ID token client-side
handed to us (no scopes, no access_token, no refresh_token). Linking GA4/
Search Console needs a real server-side authorization-code exchange, because
the resulting access/refresh tokens are what let the backend call Google's
APIs *on the business's behalf*, asynchronously, long after the browser tab
that granted consent is gone.

Flow, three legs:
  1. GET  .../authorize   — requires accepted consent (see /consent below);
                            returns a Google URL, frontend does a full
                            top-level navigation to it (not a fetch — the
                            consent screen must render in the top frame).
  2. GET  /api/analytics/oauth/callback — Google redirects back here with a
                            code; exchanged for tokens, encrypted at rest via
                            _token_crypto, then redirects into the workspace.
  3. GET  .../properties, POST .../select, GET .../report, DELETE (disconnect)
                          — normal API calls once connected.

Consent is recorded once per PROJECT (projects.analytics_consent_at), not per
provider — it's one business relationship's data-processing agreement,
regardless of how many Google products end up connected under it. See the
Phase 05B privacy/OAuth review for why: PaisaMap is a data *processor* here,
and a real delete-on-disconnect (not just token revocation) is part of that
review's own recommendation — see disconnect_project_connection's docstring.

Phase C5: a connection belongs to the company (org_id), not any one project —
projects each pick which of the company's connections they use via
project_connection_selections (see _auth_db.py's Organizations section).
"project's own" everywhere below now means "whichever connection this
project currently has selected," which may have been created by a
teammate's project.
"""

import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Blueprint, jsonify, redirect, request, session

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "paisamap-etl" / "etl"))
import _google_oauth  # noqa: E402
import _signals_data  # noqa: E402
import _token_crypto  # noqa: E402
from _google_oauth import GoogleOAuthError, normalize_city  # noqa: E402

from ._session import require_login, _auth_db

analytics_bp = Blueprint("analytics_connections", __name__, url_prefix="/api/projects/<int:project_id>/connections")
oauth_callback_bp = Blueprint("analytics_oauth_callback", __name__, url_prefix="/api/analytics")

PROVIDERS = ("google_analytics", "search_console")


def _redirect_uri():
    # Must match, character-for-character, an "Authorized redirect URI" on the
    # GCP OAuth client — registered by hand in the GCP console, not something
    # this code can set up itself.
    return request.host_url.rstrip("/") + "/api/analytics/oauth/callback"


def _ensure_aware(dt):
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _connection_public(conn):
    if conn is None:
        return None
    return {k: v for k, v in conn.items() if k not in ("access_token_encrypted", "refresh_token_encrypted")}


def _get_valid_access_token(project_id, provider, user_id):
    """Returns (access_token, connection) — refreshes via the stored refresh
    token if the cached access token is expired or about to be. Both the
    Postgres and local-SQLite paths are handled: token_expiry can come back
    tz-naive from SQLite (the same class of bug the Phase 05 upload work hit
    with geocoding timestamps), so naive values are treated as UTC rather than
    compared directly against an aware `now`."""
    conn = _auth_db.get_connection_for_project(project_id, user_id, provider, include_tokens=True)
    if conn is None:
        return None, None
    now = datetime.now(timezone.utc)
    expiry = _ensure_aware(conn.get("token_expiry"))
    if expiry is not None and expiry > now + timedelta(seconds=60):
        return _token_crypto.decrypt(conn["access_token_encrypted"]), conn

    refresh_enc = conn.get("refresh_token_encrypted")
    if not refresh_enc:
        _auth_db.mark_oauth_connection_error(
            conn["id"], "Access token expired and no refresh token was stored — reconnect required."
        )
        return None, conn
    try:
        refresh_token = _token_crypto.decrypt(refresh_enc)
        token_resp = _google_oauth.refresh_access_token(refresh_token)
        access_token = token_resp["access_token"]
    except (GoogleOAuthError, KeyError) as e:
        _auth_db.mark_oauth_connection_error(conn["id"], str(e))
        return None, conn

    new_expiry = now + timedelta(seconds=token_resp.get("expires_in", 3600))
    _auth_db.mark_oauth_connection_tokens(conn["id"], _token_crypto.encrypt(access_token), new_expiry)
    return access_token, conn


def get_project_digital_baseline(project_id, user_id):
    """Roadmap item "Custom signal generation": the shared join point reports.py
    calls to blend a project's connected GA4 data into its PDF/JSON report,
    alongside PaisaMap's own PPI signals (already computed separately via
    compute_location_intelligence_batch — this function only supplies the
    "their own data" half of the blend).

    Returns None whenever GA4 isn't usably connected (not connected, no
    property picked, or a live Google API error) — report generation must
    degrade to "no digital signals section" rather than fail outright, the
    same "don't crash on missing config" stance the rest of this module
    already takes. Not a Flask route; promoted here (rather than duplicated in
    reports.py) the same way compute_location_intelligence_batch was promoted
    out of intelligence.py for customer_data.py's reuse."""
    access_token, conn = _get_valid_access_token(project_id, "google_analytics", user_id)
    if conn is None or not conn.get("external_ref") or access_token is None:
        return None
    try:
        totals_raw = _google_oauth.run_ga4_ecommerce_totals(access_token, conn["external_ref"])
        city_raw = _google_oauth.run_ga4_report(access_token, conn["external_ref"], limit=50)
    except GoogleOAuthError:
        return None

    totals_values = (totals_raw.get("rows") or [{}])[0].get("metricValues", [])
    ecommerce = {
        "transactions": int(totals_values[0]["value"]) if len(totals_values) > 0 else 0,
        "purchase_revenue": float(totals_values[1]["value"]) if len(totals_values) > 1 else 0.0,
        "average_order_value": float(totals_values[2]["value"]) if len(totals_values) > 2 else 0.0,
    }
    city_signals = {}
    for r in city_raw.get("rows", []):
        key = normalize_city(r["dimensionValues"][0]["value"])
        if key is None:
            continue
        mv = r["metricValues"]
        city_signals[key] = {
            "sessions": int(mv[0]["value"]), "users": int(mv[1]["value"]),
            "conversions": float(mv[2]["value"]), "pageviews": int(mv[3]["value"]),
        }
    return {"external_ref": conn["external_ref"], "ecommerce": ecommerce, "city_signals": city_signals}


# ── Consent + status ─────────────────────────────────────────────────────────
@analytics_bp.route("/consent", methods=["POST"])
@require_login
def accept_consent(user_id, project_id):
    if _auth_db.get_project(project_id, user_id) is None:
        return jsonify({"error": "not_found"}), 404
    body = request.get_json(silent=True) or {}
    if not body.get("accept"):
        return jsonify({"error": "accept must be true"}), 400
    consented_at = _auth_db.set_analytics_consent(project_id, user_id)
    return jsonify({"analytics_consent_at": consented_at.isoformat() if consented_at else None})


@analytics_bp.route("", methods=["GET"])
@require_login
def list_connections(user_id, project_id):
    project = _auth_db.get_project(project_id, user_id)
    if project is None:
        return jsonify({"error": "not_found"}), 404
    # This project's own connection per provider (may be one it selected from
    # a teammate project rather than created itself), plus the company's
    # whole pool per provider so the frontend can offer "use a different one"
    # — see the Organizations/C5 sections of _auth_db.py.
    active = {
        p: _connection_public(_auth_db.get_connection_for_project(project_id, user_id, p))
        for p in PROVIDERS
    }
    pool = {p: _auth_db.list_org_connections(project_id, user_id, p) for p in PROVIDERS}
    return jsonify({
        "analytics_consent_at": project.get("analytics_consent_at"),
        "connections": [c for c in active.values() if c is not None],
        "active_by_provider": active,
        "pool_by_provider": pool,
    })


# ── Authorization-code flow ──────────────────────────────────────────────────
@analytics_bp.route("/<provider>/authorize", methods=["GET"])
@require_login
def authorize(user_id, project_id, provider):
    if provider not in PROVIDERS:
        return jsonify({"error": "unknown_provider"}), 404
    if _auth_db.get_project(project_id, user_id) is None:
        return jsonify({"error": "not_found"}), 404
    if not _auth_db.has_analytics_consent(project_id, user_id):
        return jsonify({"error": "consent_required",
                        "detail": "Accept the data-processing consent before connecting a provider."}), 400

    state = secrets.token_urlsafe(24)
    session["oauth_state"] = {
        "state": state, "project_id": project_id, "provider": provider, "user_id": user_id,
    }
    try:
        url = _google_oauth.build_authorize_url(provider, _redirect_uri(), state)
    except GoogleOAuthError as e:
        return jsonify({"error": "oauth_unavailable", "detail": str(e)}), 503
    return jsonify({"authorize_url": url})


@oauth_callback_bp.route("/oauth/callback", methods=["GET"])
@require_login
def oauth_callback(user_id):
    stored = session.pop("oauth_state", None)
    state = request.args.get("state")
    google_error = request.args.get("error")

    if not stored or stored.get("state") != state or stored.get("user_id") != user_id:
        return redirect("/workspace/connections?error=invalid_state")

    project_id, provider = stored["project_id"], stored["provider"]
    dest = f"/workspace/connections?project_id={project_id}"

    if google_error:
        return redirect(f"{dest}&error={google_error}")
    code = request.args.get("code")
    if not code:
        return redirect(f"{dest}&error=missing_code")
    if _auth_db.get_project(project_id, user_id) is None:
        return redirect(f"{dest}&error=not_found")

    try:
        token_resp = _google_oauth.exchange_code(code, _redirect_uri())
    except GoogleOAuthError:
        return redirect(f"{dest}&error=exchange_failed")

    access_token = token_resp.get("access_token")
    refresh_token = token_resp.get("refresh_token")
    if not access_token:
        return redirect(f"{dest}&error=exchange_failed")

    external_email = None
    try:
        external_email = _google_oauth.get_userinfo(access_token).get("email")
    except GoogleOAuthError:
        pass  # cosmetic only ("connected as x@gmail.com") — not worth failing the whole connect over

    expiry = datetime.now(timezone.utc) + timedelta(seconds=token_resp.get("expires_in", 3600))
    try:
        _auth_db.upsert_oauth_connection(
            project_id, user_id, provider,
            external_account_email=external_email,
            scopes=token_resp.get("scope"),
            access_token_encrypted=_token_crypto.encrypt(access_token),
            refresh_token_encrypted=_token_crypto.encrypt(refresh_token) if refresh_token else None,
            token_expiry=expiry,
        )
    except RuntimeError:
        # ANALYTICS_TOKEN_KEY missing — surfaces as a normal redirect-with-error
        # like every other failure mode here, not a 500, even though it's a
        # config problem rather than something the user did wrong.
        return redirect(f"{dest}&error=storage_unavailable")
    _auth_db.log_activity(user_id, "connection_connected", target_type="project", target_id=project_id,
                           metadata={"provider": provider})
    return redirect(f"{dest}&connected={provider}")


# ── Properties / sites + selection ──────────────────────────────────────────
@analytics_bp.route("/<provider>/properties", methods=["GET"])
@require_login
def list_properties(user_id, project_id, provider):
    if provider not in PROVIDERS:
        return jsonify({"error": "unknown_provider"}), 404
    access_token, conn = _get_valid_access_token(project_id, provider, user_id)
    if conn is None:
        return jsonify({"error": "not_connected"}), 404
    if access_token is None:
        return jsonify({"error": "reconnect_required", "detail": conn.get("last_error")}), 409
    try:
        if provider == "google_analytics":
            items = _google_oauth.list_ga4_properties(access_token)
        else:
            items = _google_oauth.list_gsc_sites(access_token)
    except GoogleOAuthError as e:
        return jsonify({"error": "google_api_error", "detail": str(e)}), 502
    return jsonify({"properties": items})


@analytics_bp.route("/<provider>/select", methods=["POST"])
@require_login
def select_property(user_id, project_id, provider):
    if provider not in PROVIDERS:
        return jsonify({"error": "unknown_provider"}), 404
    conn = _auth_db.get_connection_for_project(project_id, user_id, provider)
    if conn is None:
        return jsonify({"error": "not_connected"}), 404
    body = request.get_json(silent=True) or {}
    external_ref = (body.get("external_ref") or "").strip()
    if not external_ref:
        return jsonify({"error": "external_ref is required"}), 400
    # Updates the underlying connection by id, not (project_id, provider) —
    # if this is a shared company connection, every project using it sees
    # the same newly-chosen property/site, which is correct: one Google
    # account, one selected property, shared by whoever points at it.
    _auth_db.update_oauth_connection_ref(conn["id"], external_ref)
    return jsonify({"connection": _connection_public(_auth_db.get_connection_for_project(project_id, user_id, provider))})


@analytics_bp.route("/<provider>/use", methods=["POST"])
@require_login
def use_connection(user_id, project_id, provider):
    """Points this project at a different connection from its company's pool
    (see GET .../connections' pool_by_provider) instead of its own."""
    if provider not in PROVIDERS:
        return jsonify({"error": "unknown_provider"}), 404
    body = request.get_json(silent=True) or {}
    oauth_connection_id = body.get("oauth_connection_id")
    if not isinstance(oauth_connection_id, int):
        return jsonify({"error": "oauth_connection_id is required"}), 400
    conn = _auth_db.select_org_connection_for_project(project_id, user_id, provider, oauth_connection_id)
    if conn is None:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"connection": _connection_public(conn)})


# ── Report pull ──────────────────────────────────────────────────────────────
@analytics_bp.route("/<provider>/report", methods=["GET"])
@require_login
def get_report(user_id, project_id, provider):
    if provider not in PROVIDERS:
        return jsonify({"error": "unknown_provider"}), 404
    access_token, conn = _get_valid_access_token(project_id, provider, user_id)
    if conn is None:
        return jsonify({"error": "not_connected"}), 404
    if not conn.get("external_ref"):
        return jsonify({"error": "property_not_selected"}), 400
    if access_token is None:
        return jsonify({"error": "reconnect_required", "detail": conn.get("last_error")}), 409
    try:
        if provider == "google_analytics":
            raw = _google_oauth.run_ga4_report(access_token, conn["external_ref"])
            rows = [
                {
                    "city": r["dimensionValues"][0]["value"],
                    "sessions": int(r["metricValues"][0]["value"]),
                    "users": int(r["metricValues"][1]["value"]),
                    "conversions": float(r["metricValues"][2]["value"]),
                    "pageviews": int(r["metricValues"][3]["value"]),
                }
                for r in raw.get("rows", [])
            ]
        else:
            raw = _google_oauth.run_gsc_query(access_token, conn["external_ref"])
            rows = [
                {
                    "query": r["keys"][0], "clicks": r.get("clicks"),
                    "impressions": r.get("impressions"), "ctr": r.get("ctr"), "position": r.get("position"),
                }
                for r in raw.get("rows", [])
            ]
    except GoogleOAuthError as e:
        return jsonify({"error": "google_api_error", "detail": str(e)}), 502
    return jsonify({"provider": provider, "external_ref": conn["external_ref"], "rows": rows})


@analytics_bp.route("/google_analytics/ecommerce", methods=["GET"])
@require_login
def get_ecommerce_summary(user_id, project_id):
    """Purchase totals + top-selling items over the trailing 28 days — GA4-only
    (Search Console has no purchase data). Roadmap item "Transaction /
    ecommerce data ingestion": GA4 ecommerce events first, a POS/payment-export
    path can follow later if a business's GA4 coverage proves too thin."""
    access_token, conn = _get_valid_access_token(project_id, "google_analytics", user_id)
    if conn is None:
        return jsonify({"error": "not_connected"}), 404
    if not conn.get("external_ref"):
        return jsonify({"error": "property_not_selected"}), 400
    if access_token is None:
        return jsonify({"error": "reconnect_required", "detail": conn.get("last_error")}), 409
    try:
        totals_raw = _google_oauth.run_ga4_ecommerce_totals(access_token, conn["external_ref"])
        items_raw = _google_oauth.run_ga4_ecommerce_top_items(access_token, conn["external_ref"])
    except GoogleOAuthError as e:
        return jsonify({"error": "google_api_error", "detail": str(e)}), 502

    totals_row = (totals_raw.get("rows") or [{}])[0]
    totals_values = totals_row.get("metricValues", [])
    totals = {
        "transactions": int(totals_values[0]["value"]) if len(totals_values) > 0 else 0,
        "purchase_revenue": float(totals_values[1]["value"]) if len(totals_values) > 1 else 0.0,
        "average_order_value": float(totals_values[2]["value"]) if len(totals_values) > 2 else 0.0,
    }
    top_items = [
        {
            "item_name": r["dimensionValues"][0]["value"],
            "item_revenue": float(r["metricValues"][0]["value"]),
            "items_purchased": int(r["metricValues"][1]["value"]),
        }
        for r in items_raw.get("rows", [])
    ]
    return jsonify({
        "provider": "google_analytics", "external_ref": conn["external_ref"],
        "totals": totals, "top_items": top_items,
    })


# ── Location-tagging pipeline ────────────────────────────────────────────────
@analytics_bp.route("/google_analytics/location-tags", methods=["GET"])
@require_login
def get_location_tags(user_id, project_id):
    """Joins a project's own uploaded stores (Phase 05's customer_locations,
    already resolved to a pincode) against the connected GA4 property's
    city-level traffic — "their store address list × GA4's location
    dimension," per the roadmap. GA4 has no pincode dimension, so the join key
    is a best-effort normalized city name (see normalize_city) — this is
    explicitly approximate, not a guaranteed match, and every result says so
    per-store rather than silently treating a miss as zero traffic."""
    access_token, conn = _get_valid_access_token(project_id, "google_analytics", user_id)
    if conn is None:
        return jsonify({"error": "not_connected"}), 404
    if not conn.get("external_ref"):
        return jsonify({"error": "property_not_selected"}), 400
    if access_token is None:
        return jsonify({"error": "reconnect_required", "detail": conn.get("last_error")}), 409

    locations = [
        loc for loc in _auth_db.list_customer_locations(user_id, project_id) if loc.get("pincode")
    ]
    if not locations:
        return jsonify({
            "error": "no_customer_locations",
            "detail": "Upload store data with resolved pincodes first, on the Store Data page.",
        }), 400

    try:
        raw = _google_oauth.run_ga4_report(access_token, conn["external_ref"], limit=50)
    except GoogleOAuthError as e:
        return jsonify({"error": "google_api_error", "detail": str(e)}), 502

    ga4_by_city = {}
    for r in raw.get("rows", []):
        city_name = r["dimensionValues"][0]["value"]
        key = normalize_city(city_name)
        if key is None:
            continue
        mv = r["metricValues"]
        ga4_by_city[key] = {
            "city": city_name, "sessions": int(mv[0]["value"]), "users": int(mv[1]["value"]),
            "conversions": float(mv[2]["value"]), "pageviews": int(mv[3]["value"]),
        }

    geography = _signals_data.load_geography()
    tagged = []
    for loc in locations:
        district = (geography.get(loc["pincode"]) or {}).get("district")
        key = normalize_city(district)
        match = ga4_by_city.get(key) if key else None
        tagged.append({
            "id": loc["id"], "store_name": loc.get("store_name"), "pincode": loc["pincode"],
            "resolved_city": district, "matched": match is not None,
            "digital_signal": {
                "sessions": match["sessions"], "users": match["users"],
                "conversions": match["conversions"], "pageviews": match["pageviews"],
            } if match else None,
        })

    return jsonify({
        "external_ref": conn["external_ref"], "window_days": 28,
        "matched_count": sum(1 for t in tagged if t["matched"]),
        "total_count": len(tagged),
        "locations": tagged,
    })


# ── Disconnect ───────────────────────────────────────────────────────────────
@analytics_bp.route("/<provider>", methods=["DELETE"])
@require_login
def disconnect(user_id, project_id, provider):
    if provider not in PROVIDERS:
        return jsonify({"error": "unknown_provider"}), 404
    result = _auth_db.disconnect_project_connection(project_id, user_id, provider)
    if result is None:
        return jsonify({"error": "not_found"}), 404
    # Only revoke at Google + hard-delete the row when nothing else in the
    # company is still using this exact connection — see
    # disconnect_project_connection's docstring. Otherwise this project just
    # unlinked from a still-shared connection; there's nothing to revoke.
    if not result.get("unlinked_only"):
        for enc in (result.get("access_token_encrypted"), result.get("refresh_token_encrypted")):
            if enc:
                try:
                    _google_oauth.revoke_token(_token_crypto.decrypt(enc))
                except Exception:
                    pass
    _auth_db.log_activity(user_id, "connection_disconnected", target_type="project", target_id=project_id,
                           metadata={"provider": provider})
    return jsonify({"status": "ok"})

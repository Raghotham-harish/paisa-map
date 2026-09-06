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
review's own recommendation — see delete_oauth_connection's docstring.
"""

import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Blueprint, jsonify, redirect, request, session

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "paisamap-etl" / "etl"))
import _google_oauth  # noqa: E402
import _token_crypto  # noqa: E402
from _google_oauth import GoogleOAuthError  # noqa: E402

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
    conn = _auth_db.get_oauth_connection(project_id, provider, user_id, include_tokens=True)
    if conn is None:
        return None, None
    now = datetime.now(timezone.utc)
    expiry = _ensure_aware(conn.get("token_expiry"))
    if expiry is not None and expiry > now + timedelta(seconds=60):
        return _token_crypto.decrypt(conn["access_token_encrypted"]), conn

    refresh_enc = conn.get("refresh_token_encrypted")
    if not refresh_enc:
        _auth_db.mark_oauth_connection_error(
            project_id, provider, "Access token expired and no refresh token was stored — reconnect required."
        )
        return None, conn
    try:
        refresh_token = _token_crypto.decrypt(refresh_enc)
        token_resp = _google_oauth.refresh_access_token(refresh_token)
        access_token = token_resp["access_token"]
    except (GoogleOAuthError, KeyError) as e:
        _auth_db.mark_oauth_connection_error(project_id, provider, str(e))
        return None, conn

    new_expiry = now + timedelta(seconds=token_resp.get("expires_in", 3600))
    _auth_db.mark_oauth_connection_tokens(project_id, provider, _token_crypto.encrypt(access_token), new_expiry)
    return access_token, conn


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
    return jsonify({
        "analytics_consent_at": project.get("analytics_consent_at"),
        "connections": _auth_db.list_oauth_connections(project_id, user_id),
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
    if _auth_db.get_oauth_connection(project_id, provider, user_id) is None:
        return jsonify({"error": "not_connected"}), 404
    body = request.get_json(silent=True) or {}
    external_ref = (body.get("external_ref") or "").strip()
    if not external_ref:
        return jsonify({"error": "external_ref is required"}), 400
    conn = _auth_db.update_oauth_connection_ref(project_id, provider, user_id, external_ref)
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


# ── Disconnect ───────────────────────────────────────────────────────────────
@analytics_bp.route("/<provider>", methods=["DELETE"])
@require_login
def disconnect(user_id, project_id, provider):
    if provider not in PROVIDERS:
        return jsonify({"error": "unknown_provider"}), 404
    tokens = _auth_db.delete_oauth_connection(project_id, provider, user_id)
    if tokens is None:
        return jsonify({"error": "not_found"}), 404
    # Best-effort revoke at Google, then the row is already gone regardless —
    # a real delete, not a soft-disable, per the Phase 05B DPDP framing.
    for enc in (tokens.get("access_token_encrypted"), tokens.get("refresh_token_encrypted")):
        if enc:
            try:
                _google_oauth.revoke_token(_token_crypto.decrypt(enc))
            except Exception:
                pass
    _auth_db.log_activity(user_id, "connection_disconnected", target_type="project", target_id=project_id,
                           metadata={"provider": provider})
    return jsonify({"status": "ok"})

"""
api_keys.py — key management for Track 1's B2B data-API product
(GET /api/export with an X-API-Key header, see server.py + _api_keys.py).

require_login-gated, not plan-gated: any signed-in user, on any plan, can
create a key — a free user gets one too, useful as a stable identity even
without Pro (see _ops.py). The actual value of upgrading is that the SAME key
immediately gets elevated columns + a higher rate limit, since the plan is
resolved live on every request (_auth_db.get_api_key_by_hash) rather than
frozen at key creation — no separate "buy API access" purchase flow exists.

A key belongs to a COMPANY (api_keys.org_id, chosen at creation, default the
maker's own): that company's owners/admins — and its payer's — see and revoke it
via /api/organizations/<id>/api-keys, capacity is counted per company, and the key
stops working when its owner leaves the company. This blueprint is a person's
own keys; the company-wide view lives in organizations.py.
"""

from flask import Blueprint, request, jsonify

from ._session import require_login, _auth_db

api_keys_bp = Blueprint("api_keys", __name__, url_prefix="/api/developer/keys")


@api_keys_bp.route("", methods=["POST"])
@require_login
def create_key(user_id):
    import _api_keys
    body = request.get_json(silent=True) or {}
    label = (body.get("label") or "").strip() or None
    # The company the key belongs to (its admins can see and revoke it):
    # the one chosen in the switcher if the caller is a member, else their own.
    org_id, err = _auth_db.resolve_api_key_company(user_id, body.get("org_id"))
    if err:
        return jsonify(err), 404 if err["error"] == "not_found" else 400
    raw_key, key_hash, key_prefix = _api_keys.generate_key()
    record = _auth_db.create_api_key(user_id, key_hash, key_prefix, label, org_id=org_id)
    # The raw key is returned exactly once, here — nothing in this app stores
    # or logs it again, so a client that doesn't save it now has to revoke and
    # re-create rather than ever recover it.
    return jsonify({"api_key": record, "key": raw_key}), 201


@api_keys_bp.route("", methods=["GET"])
@require_login
def list_keys(user_id):
    return jsonify({"api_keys": _auth_db.list_api_keys(user_id)})


@api_keys_bp.route("/<int:key_id>", methods=["DELETE"])
@require_login
def revoke_key(user_id, key_id):
    revoked = _auth_db.revoke_api_key(key_id, user_id)
    if not revoked:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"status": "ok"})

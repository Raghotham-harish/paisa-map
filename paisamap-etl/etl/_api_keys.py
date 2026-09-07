"""
_api_keys.py — API-key generation/hashing and per-request resolution for
Track 1 (PaisaMap B2B data-API product).

An API key is a bearer secret, not a password a human re-enters — so unlike
_token_crypto.py's OAuth tokens (which this server must later decrypt to call
Google's API on the user's behalf), a key is never recovered after issuance,
only hash-compared. SHA-256 with no salt is correct here (unlike a human
password) because the raw key already carries secrets.token_urlsafe's full
entropy, not a guessable phrase.

resolve(request) is called from server.py's _ratelimit before_request hook,
which already runs on every /api/* request, and caches its result on flask.g
so a request never pays for the lookup twice — blueprints/_session.py's
get_effective_plan() reads the same cache instead of re-querying. As a side
effect, a successful resolve() also records usage (_auth_db.touch_api_key_usage) —
deliberately folded in here rather than a separate call, since the g-cache
guard is what already guarantees this runs at most once per request.

Deliberately out of scope here: CORS headers for browser-JS callers (the
realistic first use case for this product is server-to-server curl/Python,
not browser fetch — revisit if a pilot customer needs it) and any hard quota
enforcement (see _auth_db.py's touch_api_key_usage — visibility only, no
real per-tier numbers exist yet, that's a separate pricing decision).
"""

import hashlib
import secrets

from flask import g

try:
    import _auth_db
except ImportError:
    _auth_db = None

KEY_PREFIX = "pmk_"
_UNSET = object()


def generate_key():
    """Returns (raw_key, key_hash, key_prefix). raw_key is shown to the caller
    exactly once — nothing in this app stores or logs it again."""
    raw = KEY_PREFIX + secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return raw, key_hash, raw[:12]


def resolve(request):
    """Returns {"key_id", "user_id", "plan"} for a valid, non-revoked key
    presented in the X-API-Key header, or None (missing header, unknown key,
    revoked key, or DB unavailable — same fail-to-anonymous shape as
    get_effective_plan()). Cached on flask.g for the lifetime of the request,
    including the "no key" case, so repeat callers within one request never
    trigger a second lookup."""
    cached = getattr(g, "_resolved_api_key", _UNSET)
    if cached is not _UNSET:
        return cached
    result = None
    raw = request.headers.get("X-API-Key", "").strip()
    if raw and _auth_db is not None and _auth_db.enabled():
        key_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        try:
            result = _auth_db.get_api_key_by_hash(key_hash)
        except Exception:
            result = None
        if result:
            try:
                _auth_db.touch_api_key_usage(result["key_id"])
            except Exception:
                pass  # visibility only — never let this affect the actual request
    g._resolved_api_key = result
    return result

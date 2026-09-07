"""
_ops.py — lightweight operations layer for server.py: per-IP rate limiting,
in-process request/error counters, and an optional Sentry hook.

Deliberately stdlib-only (except the guarded `sentry_sdk` import). This is a
single-box deployment (one gunicorn/Flask process behind nginx on Lightsail),
so an in-process token-bucket limiter and in-process counters are the right
weight — Redis / flask-limiter / a metrics backend would be infrastructure this
app doesn't otherwise have. If PaisaMap ever runs more than one worker process,
revisit: these counters and buckets are per-process.

Nothing here changes a response unless RATELIMIT_ENABLED=1 is set in the
environment — deploying this module is inert until that flag is flipped, so it
can't surprise the live map. The limiter also fails OPEN: any bug in the check
lets the request through rather than 500ing.
"""

import os
import time
import threading
from collections import defaultdict


# ── Sentry (optional, fully guarded) ─────────────────────────────────────────
_sentry_ready = False


def init_sentry():
    """No-op unless BOTH `sentry-sdk` is installed AND SENTRY_DSN is set. Call
    once at startup. Returns True if Sentry was actually initialised."""
    global _sentry_ready
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return False
    try:
        import sentry_sdk
        sentry_sdk.init(
            dsn=dsn,
            environment=os.environ.get("SENTRY_ENV", "production"),
            traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0")),
            send_default_pii=False,
        )
        _sentry_ready = True
        return True
    except Exception as e:  # bad DSN, version mismatch, whatever — never crash boot
        print(f"[ops] Sentry init skipped: {e}", flush=True)
        return False


def capture_exception(exc):
    if _sentry_ready:
        try:
            import sentry_sdk
            sentry_sdk.capture_exception(exc)
        except Exception:
            pass


# ── In-process counters ─────────────────────────────────────────────────────
_counters = defaultdict(int)
_counters_lock = threading.Lock()
_started_at = time.time()


def incr(key, n=1):
    with _counters_lock:
        _counters[key] += n


def snapshot():
    with _counters_lock:
        data = dict(_counters)
    data["uptime_seconds"] = round(time.time() - _started_at, 1)
    return data


# ── Token-bucket rate limiter, keyed by client IP + route group ──────────────
_ENABLED = os.environ.get("RATELIMIT_ENABLED", "").strip() in ("1", "true", "yes")

# Multiplier applied to a group's base cap/refill when the request carries a
# valid Pro/Team-tier API key (see _api_keys.py) — a free-tier key gets the
# same caps as anonymous traffic in that group, unchanged. No real per-tier
# numbers exist yet for this product (that's a separate pricing decision), so
# this is a reasoned default, not a committed price — easy to retune via env.
_API_KEY_PRO_MULTIPLIER = float(os.environ.get("RATELIMIT_APIKEY_PRO_MULTIPLIER", "5"))

# route-group -> (capacity, refill_per_second). A bucket starts full; each
# request costs 1 token; it refills continuously. Generous by design — these
# guard against scraping and runaway loops, not against normal interactive use.
_LIMITS = {
    # Nominatim-proxying endpoints: OSM's usage policy is ~1 req/s per app, and
    # every call here goes out under our single server IP — the tightest group.
    "geo":     (int(os.environ.get("RATELIMIT_GEO_CAP", "30")),
                float(os.environ.get("RATELIMIT_GEO_RPS", "0.5"))),
    # Data pulls (/api/export, /api/enrich_stats) — heavier responses.
    "data":    (int(os.environ.get("RATELIMIT_DATA_CAP", "60")),
                float(os.environ.get("RATELIMIT_DATA_RPS", "1"))),
    # Everything else under /api/ (scores, config, health, status polling).
    "default": (int(os.environ.get("RATELIMIT_DEFAULT_CAP", "240")),
                float(os.environ.get("RATELIMIT_DEFAULT_RPS", "4"))),
}

_GROUP_BY_PREFIX = (
    ("/api/search",       "geo"),
    ("/api/reverse",      "geo"),
    ("/api/export",       "data"),
    ("/api/enrich_stats", "data"),
)

_buckets = {}            # (ip, group) -> [tokens, last_refill_ts]
_buckets_lock = threading.Lock()
_last_sweep = time.time()


def _group_for(path):
    for prefix, group in _GROUP_BY_PREFIX:
        if path.startswith(prefix):
            return group
    return "default"


def _sweep_locked(now):
    """Drop buckets that have sat full and idle for >1h so the dict can't grow
    without bound from one-off scanner IPs. Called opportunistically under lock."""
    global _last_sweep
    if now - _last_sweep < 600:
        return
    _last_sweep = now
    stale = [k for k, (_tok, ts) in _buckets.items() if now - ts > 3600]
    for k in stale:
        del _buckets[k]


def check(ip, path, api_key=None):
    """Returns (allowed: bool, retry_after_seconds: int). Fails OPEN — any
    exception is swallowed and the request is allowed.

    `api_key`, when given (a resolved {"key_id","plan",...} dict from
    _api_keys.resolve — see server.py's _ratelimit hook), buckets the request
    by key id instead of IP, so a customer's own quota isn't polluted by
    other traffic sharing their IP/NAT and vice versa. A pro/team-tier key's
    cap is a multiple of the group's base cap; a free-tier key gets the exact
    same cap as anonymous IP-based traffic in that group."""
    if not _ENABLED:
        return True, 0
    try:
        group = _group_for(path)
        cap, rps = _LIMITS[group]
        if api_key:
            key = ("apikey", api_key["key_id"])
            if api_key.get("plan") in ("pro", "team"):
                cap *= _API_KEY_PRO_MULTIPLIER
                rps *= _API_KEY_PRO_MULTIPLIER
        else:
            key = (ip or "?", group)
        now = time.time()
        with _buckets_lock:
            _sweep_locked(now)
            tokens, last = _buckets.get(key, (float(cap), now))
            tokens = min(cap, tokens + (now - last) * rps)
            if tokens >= 1.0:
                _buckets[key] = (tokens - 1.0, now)
                return True, 0
            _buckets[key] = (tokens, now)
            deficit = 1.0 - tokens
            return False, max(1, int(deficit / rps) + 1)
    except Exception:
        return True, 0


def enabled():
    return _ENABLED

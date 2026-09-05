"""
_geocode.py — plain, in-process geocoding function for batch use (customer
data upload's address→pincode resolution), mirroring the Nominatim call shape
`server.py`'s `/api/search` route already uses.

`/api/search` and `/api/reverse` are thin HTTP route handlers (read from
Flask's `request`, return a raw response tuple) — not reusable as plain
functions. This module extracts just the "call Nominatim, parse the first
result" logic so a background batch job can call it directly in a loop.

Throttling is deliberately NOT built into `geocode_address()` itself — the
caller (the batch job loop) applies `GEOCODE_THROTTLE_SEC` between calls,
since Nominatim's usage policy caps the public instance at ~1 req/sec and
only a multi-call batch needs pacing; a future single-lookup caller shouldn't
pay that cost.
"""

from __future__ import annotations

import urllib.request
import urllib.parse

GEOCODE_THROTTLE_SEC = 1.1

_NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"


def geocode_address(query: str) -> dict | None:
    """Best-effort forward geocode of a free-text address, scoped to India
    (matches /api/search's countrycodes=in). Returns
    {"lat", "lng", "pincode", "display_name"} for the top match, or None if
    the query is empty, Nominatim returns no results, or the request fails."""
    query = (query or "").strip()
    if not query:
        return None
    params = urllib.parse.urlencode({
        "format": "jsonv2", "limit": 1, "addressdetails": 1,
        "countrycodes": "in", "q": query,
    })
    url = f"{_NOMINATIM_SEARCH_URL}?{params}"
    req = urllib.request.Request(
        url, headers={"User-Agent": "PaisaMap-Server/1.0", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            import json
            results = json.loads(r.read())
    except Exception:
        return None
    if not results:
        return None
    top = results[0]
    try:
        lat = float(top["lat"])
        lng = float(top["lon"])
    except (KeyError, ValueError, TypeError):
        return None
    pincode = (top.get("address") or {}).get("postcode")
    return {
        "lat": lat, "lng": lng,
        "pincode": pincode.strip() if pincode else None,
        "display_name": top.get("display_name"),
    }

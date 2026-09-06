"""
_google_oauth.py — Google OAuth2 authorization-code flow + thin GA4 Data API /
Search Console API wrappers, built on stdlib urllib rather than
google-auth-oauthlib / google-api-python-client (neither is installed, and
this codebase's standing style avoids a new dependency for what a dozen lines
of urllib already covers — see e.g. customer_data.py's csv+openpyxl-only
upload path). auth.py already depends on google-auth for ID-token
verification; this module handles the other half of OAuth2 that ID-token
sign-in never needed: exchanging a code for an access/refresh token pair.

Scopes are deliberately narrow (Phase 05B's privacy/OAuth review verdict):
analytics.readonly + webmasters.readonly only, both Google's lighter
"sensitive" tier — no Workspace/Gmail/Drive scopes anywhere in this module.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"
USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"

GA4_ADMIN_ACCOUNT_SUMMARIES = "https://analyticsadmin.googleapis.com/v1beta/accountSummaries"
GA4_DATA_RUN_REPORT = "https://analyticsdata.googleapis.com/v1beta/{property}:runReport"
GSC_SITES_LIST = "https://www.googleapis.com/webmasters/v3/sites"
GSC_SEARCH_ANALYTICS = "https://www.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query"

SCOPES = {
    "google_analytics": "https://www.googleapis.com/auth/analytics.readonly",
    "search_console": "https://www.googleapis.com/auth/webmasters.readonly",
}


class GoogleOAuthError(Exception):
    pass


def _client_id():
    v = os.environ.get("GOOGLE_CLIENT_ID")
    if not v:
        raise GoogleOAuthError("GOOGLE_CLIENT_ID not configured")
    return v


def _client_secret():
    # New env var — the existing ID-token sign-in flow never needed a client
    # secret (implicit-style credential decode), but a server-side
    # authorization-code exchange does. Must be added to /etc/paisamap/db.env
    # and the corresponding redirect URI registered in the same GCP OAuth
    # client's "Authorized redirect URIs".
    v = os.environ.get("GOOGLE_CLIENT_SECRET")
    if not v:
        raise GoogleOAuthError("GOOGLE_CLIENT_SECRET not configured")
    return v


def build_authorize_url(provider, redirect_uri, state):
    scope = f"openid email {SCOPES[provider]}"
    params = {
        "client_id": _client_id(),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scope,
        "access_type": "offline",
        "prompt": "consent",  # force a refresh_token every time, not only on first-ever grant
        "state": state,
        "include_granted_scopes": "true",
    }
    return f"{AUTH_ENDPOINT}?{urllib.parse.urlencode(params)}"


def _post_form(url, data):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise GoogleOAuthError(f"{url} -> {e.code}: {e.read().decode(errors='replace')}")


def _get_json(url, access_token, params=None):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise GoogleOAuthError(f"{url} -> {e.code}: {e.read().decode(errors='replace')}")


def _post_json(url, access_token, payload):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise GoogleOAuthError(f"{url} -> {e.code}: {e.read().decode(errors='replace')}")


def exchange_code(code, redirect_uri):
    return _post_form(TOKEN_ENDPOINT, {
        "code": code, "client_id": _client_id(), "client_secret": _client_secret(),
        "redirect_uri": redirect_uri, "grant_type": "authorization_code",
    })


def refresh_access_token(refresh_token):
    return _post_form(TOKEN_ENDPOINT, {
        "refresh_token": refresh_token, "client_id": _client_id(),
        "client_secret": _client_secret(), "grant_type": "refresh_token",
    })


def revoke_token(token):
    try:
        _post_form(REVOKE_ENDPOINT, {"token": token})
    except GoogleOAuthError:
        pass  # already invalid/expired at Google's end — the caller's DB delete is what matters


def get_userinfo(access_token):
    return _get_json(USERINFO_ENDPOINT, access_token)


def list_ga4_properties(access_token):
    """Flattens Admin API accountSummaries -> [{property_id, display_name, account_name}]."""
    data = _get_json(GA4_ADMIN_ACCOUNT_SUMMARIES, access_token)
    out = []
    for account in data.get("accountSummaries", []):
        for prop in account.get("propertySummaries", []):
            out.append({
                "property_id": prop.get("property", "").replace("properties/", ""),
                "display_name": prop.get("displayName"),
                "account_name": account.get("displayName"),
            })
    return out


def list_gsc_sites(access_token):
    data = _get_json(GSC_SITES_LIST, access_token)
    return [
        {"site_url": s["siteUrl"], "permission_level": s.get("permissionLevel")}
        for s in data.get("siteEntry", [])
    ]


def _n_days_ago(n):
    return (date.today() - timedelta(days=n)).isoformat()


def run_ga4_report(access_token, property_id, days=28):
    """Sessions/users/conversions/pageviews by city over the trailing window —
    GA4 has no pincode dimension, so city is the finest free geography
    dimension available; pincode-level joining happens later (roadmap item
    "Location-tagging pipeline") via the business's own store address list."""
    payload = {
        "dateRanges": [{"startDate": f"{days}daysAgo", "endDate": "today"}],
        "dimensions": [{"name": "city"}],
        "metrics": [
            {"name": "sessions"}, {"name": "totalUsers"},
            {"name": "conversions"}, {"name": "screenPageViews"},
        ],
        "limit": 10,
        "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
    }
    url = GA4_DATA_RUN_REPORT.format(property=f"properties/{property_id}")
    return _post_json(url, access_token, payload)


def run_ga4_ecommerce_totals(access_token, property_id, days=28):
    """Account-wide purchase totals, no dimension — GA4's runReport returns a
    single row when no `dimensions` are requested, one value per requested
    metric in order."""
    payload = {
        "dateRanges": [{"startDate": f"{days}daysAgo", "endDate": "today"}],
        "metrics": [
            {"name": "transactions"}, {"name": "purchaseRevenue"}, {"name": "averagePurchaseRevenue"},
        ],
    }
    url = GA4_DATA_RUN_REPORT.format(property=f"properties/{property_id}")
    return _post_json(url, access_token, payload)


def run_ga4_ecommerce_top_items(access_token, property_id, days=28):
    """Top 10 items by revenue — the "what's actually selling" half of
    ecommerce ingestion, alongside the account-wide totals above."""
    payload = {
        "dateRanges": [{"startDate": f"{days}daysAgo", "endDate": "today"}],
        "dimensions": [{"name": "itemName"}],
        "metrics": [{"name": "itemRevenue"}, {"name": "itemsPurchased"}],
        "limit": 10,
        "orderBys": [{"metric": {"metricName": "itemRevenue"}, "desc": True}],
    }
    url = GA4_DATA_RUN_REPORT.format(property=f"properties/{property_id}")
    return _post_json(url, access_token, payload)


def run_gsc_query(access_token, site_url, days=28):
    """Top search queries driving traffic to the site over the trailing window
    — the "what people search before visiting/buying" signal the roadmap
    scoped this item for."""
    payload = {
        "startDate": _n_days_ago(days), "endDate": _n_days_ago(2),  # GSC data lags ~2 days
        "dimensions": ["query"], "rowLimit": 25,
    }
    url = GSC_SEARCH_ANALYTICS.format(site=urllib.parse.quote(site_url, safe=""))
    return _post_json(url, access_token, payload)

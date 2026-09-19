"""
test_billing_round4_http.py — billing-v2 round 4 over HTTP: company-attributed
API keys, website verification, connect-by-website, and the v2 plan bridge
(/api/auth/me, /api/export with a key).

    DATABASE_URL="sqlite:////tmp/billing_round4_http.sqlite" python3 tests/test_billing_round4_http.py

Plain script, throwaway sqlite. Run it under BILLING_SCOPE=wallet as well.
"""

import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "paisamap-etl", "etl"))

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/billing_round4_http_default.sqlite")
if "sqlite" not in os.environ["DATABASE_URL"]:
    raise SystemExit("Refusing to run: DATABASE_URL doesn't look like a throwaway sqlite file.")
if not os.environ.get("CUSTOMER_DATA_KEY"):
    from cryptography.fernet import Fernet
    os.environ["CUSTOMER_DATA_KEY"] = Fernet.generate_key().decode()
os.environ.setdefault("INVOICES_DIR", tempfile.mkdtemp(prefix="pm_invoices_"))

import _db
_db.enabled = lambda: False
import _auth_db as A
import _site_verify as V
from sqlalchemy import text

A.init_schema()
A.migrate_schema()
engine = A._require_engine()
WALLET = os.environ.get("BILLING_SCOPE") == "wallet"
passed = 0


def check(cond, msg):
    global passed
    assert cond, f"FAIL: {msg}"
    passed += 1


def sql(stmt, **params):
    with engine.begin() as conn:
        return conn.execute(text(stmt), params)


def person(tag):
    u = A.upsert_user(f"sub-{tag}", f"{tag}@example.com", tag, None)
    org = A.create_default_organization_for_user(u["id"], f"{tag} Co")
    return u["id"], org["id"]


notices = []
A._dispatch_link_notices = lambda n: notices.extend(n)

import server
app = server.app
app.testing = True


def call(uid, method, url, headers=None, **kw):
    c = app.test_client()
    if uid:
        with c.session_transaction() as sess:
            sess["user_id"] = uid
    return c.open(url, method=method, headers=headers or {}, **kw)


priya, org_ag = person("priya")
adira, _ = person("adira")
kushal, _ = person("kushal")
A.add_org_member(org_ag, priya, "adira@example.com", "admin")
A.add_org_member(org_ag, priya, "kushal@example.com", "member")
zed, org_zed = person("zed")

# ═══ 1. API keys over HTTP ══════════════════════════════════════════════════
K = "/api/developer/keys"
check(call(None, "POST", K, json={}).status_code == 401, "creating a key needs a login")
r = call(kushal, "POST", K, json={"org_id": org_ag, "label": "k1"})
key1 = r.get_json()
check(r.status_code == 201 and key1["api_key"]["org_id"] == org_ag and key1["api_key"]["org_name"] == "priya Co" and key1["key"].startswith("pmk_"),
      "a key is created in the chosen company and the raw key is returned once")
check("key_hash" not in key1["api_key"], "the hash never leaves the server")
check(call(kushal, "POST", K, json={"org_id": org_zed}).status_code == 404, "a company you're not in -> 404")
check(call(kushal, "POST", K, json={"org_id": "x"}).status_code == 400, "a junk company -> 400")
r = call(kushal, "POST", K, json={"label": "default company"})
check(r.get_json()["api_key"]["org_name"] == "kushal Co", "with none chosen, the maker's own primary company")
mine = call(kushal, "GET", K).get_json()["api_keys"]
check({k["label"] for k in mine} == {"k1", "default company"} and all("org_name" in k and "key_hash" not in k for k in mine), "own keys list with their company")

CK = f"/api/organizations/{org_ag}/api-keys"
check(call(None, "GET", CK).status_code == 401, "the company key list needs a login")
check(call(kushal, "GET", CK).status_code == 403, "a plain member -> 403")
check(call(zed, "GET", CK).status_code == 404, "a stranger -> 404")
r = call(adira, "GET", CK)
body = r.get_json()
check(r.status_code == 200 and [k["label"] for k in body["keys"]] == ["k1"] and body["keys"][0]["owner_name"] == "kushal" and body["org_name"] == "priya Co",
      "an admin lists the company's keys with who made each")
kid = body["keys"][0]["id"]
check(call(kushal, "DELETE", f"{CK}/{kid}").status_code == 403, "a plain member can't revoke")
check(call(zed, "DELETE", f"{CK}/{kid}").status_code == 404, "a stranger can't")
check(call(adira, "DELETE", f"{CK}/{kid}").status_code == 200, "an admin revokes")
check(call(adira, "DELETE", f"{CK}/{kid}").status_code == 404, "twice -> 404")
check(call(priya, "GET", CK).get_json()["keys"][0]["revoked_at"] is not None, "shown as revoked")

# ═══ 2. the plan bridge: /api/auth/me and /api/export ═══════════════════════
CSV = "/api/export?dataset=ppi&format=csv&columns=deposits_per_capita,ppi_ml"


def header(resp):
    return resp.data.decode().splitlines()[0].split(",")


def key_for(uid, org):
    return call(uid, "POST", K, json={"org_id": org}).get_json()["key"]


anon = call(None, "GET", CSV)
check(anon.status_code == 200 and "deposits_per_capita" not in header(anon), "anonymous: the Pro column is stripped")

me = call(priya, "GET", "/api/auth/me").get_json()["user"]
check(me["plan"] == "free" and me["tier"] == "free" and me["tier_label"] == "Free", "/api/auth/me for a free account: plan free, tier + label alongside")

sql("UPDATE users SET plan = 'pro' WHERE id = :u", u=priya)
me = call(priya, "GET", "/api/auth/me").get_json()["user"]
check(me["plan"] == "pro" and me["tier"] == "pro" and me["tier_label"] == "Pro (legacy)", "a legacy 'pro' account: unchanged for every existing front end, and labelled legacy")
kp = key_for(priya, org_ag)
check("deposits_per_capita" in header(call(None, "GET", CSV, headers={"X-API-Key": kp})), "a legacy-pro key gets the Pro column")
sql("UPDATE users SET plan = 'free' WHERE id = :u", u=priya)

# a v2 tier on the COMPANY: honoured only in wallet scope (that's the point of flipping it)
kk = key_for(kushal, org_ag)
for tier, cols, api_elevated, compat in (("v2_starter", True, False, "pro"), ("v2_growth", True, True, "pro"), ("v2_scale", True, True, "team")):
    sql("UPDATE organizations SET plan = :p WHERE id = :o", p=tier, o=org_ag)
    me = call(kushal, "GET", "/api/auth/me").get_json()["user"]
    got_cols = "deposits_per_capita" in header(call(None, "GET", CSV, headers={"X-API-Key": kk}))
    if WALLET:
        check(me["plan"] == compat and me["tier"] == tier, f"wallet scope: a member carries the company's {tier} (front ends see plan={compat})")
        check(got_cols == cols, f"wallet scope: {tier} -> Pro columns {cols}")
    else:
        check(me["plan"] == "free" and me["tier"] == "free", f"user scope: a company's {tier} changes nothing yet")
        check(not got_cols, f"user scope: {tier} doesn't unlock anything until wallet mode is on")
if WALLET:
    check(me["tier_label"] == "Scale", "the price-book label is shown")
sql("UPDATE organizations SET plan = 'free' WHERE id = :o", o=org_ag)

# ═══ 3. website verification over HTTP ══════════════════════════════════════
W = f"/api/organizations/{org_ag}/website-verification"
check(call(None, "GET", W).status_code == 401, "needs a login")
check(call(zed, "GET", W).status_code == 404, "a stranger -> 404")
check(call(priya, "POST", W).status_code == 409 and call(priya, "POST", W).get_json()["error"] == "no_website", "no website set -> 409 no_website")
r = call(priya, "PUT", f"/api/organizations/{org_ag}", json={"website_url": "https://www.priya-agency.in/"})
check(r.status_code == 200, "the website is saved through the normal company update")
r = call(priya, "POST", W)
v = r.get_json()
check(r.status_code == 200 and v["status"] == "pending" and v["domain"] == "priya-agency.in" and v["tag"].startswith('<meta name="paisamap-site-verification"'), "start -> pending with the tag")
check(call(kushal, "GET", W).get_json().get("token") is None, "a plain member never sees the token")
check(call(kushal, "POST", W).status_code == 403 and call(kushal, "POST", W + "/check").status_code == 403, "a plain member can't start or check")
check(call(zed, "POST", W + "/check").status_code == 404, "a stranger can't check")

real_meta = V.check_meta_tag
V.check_meta_tag = lambda d, t, fetch=None: (False, "We reached the site but didn't find the verification tag in its <head>.")
r = call(priya, "POST", W + "/check")
check(r.status_code == 200 and r.get_json()["verified"] is False and "didn't find" in r.get_json()["reason"], "a failed check answers 200 with verified:false and the reason")
r = call(priya, "POST", W + "/check")
check(r.status_code == 429 and r.get_json()["error"] == "too_soon" and r.get_json()["retry_after"] >= 1, "hammering the button -> 429 too_soon")
sql("UPDATE org_domains SET last_checked_at = datetime(last_checked_at, '-1 minute')")

# the Search Console composition (the route's checker), with Google mocked
import _google_oauth
from blueprints import analytics_connections as ac, organizations as orgs_bp
real = (A.list_org_search_console_connections, ac._access_token_for_connection, _google_oauth.list_gsc_sites)
A.list_org_search_console_connections = lambda org: [{"id": 1}, {"id": 2}, {"id": 3}]
tokens = {1: None, 2: "tok-2", 3: "tok-3"}
ac._access_token_for_connection = lambda conn: tokens[conn["id"]]
sites_by_tok = {"tok-2": [{"site_url": "sc-domain:priya-agency.in", "permission_level": "siteFullUser"}],
                "tok-3": [{"site_url": "sc-domain:priya-agency.in", "permission_level": "siteOwner"}]}
asked = []
def fake_sites(tok):
    asked.append(tok)
    return sites_by_tok[tok]
_google_oauth.list_gsc_sites = fake_sites
try:
    check(orgs_bp._search_console_owns(org_ag, "priya-agency.in") is True and asked == ["tok-2", "tok-3"],
          "skips a connection with no usable token, ignores a full-user account, accepts the one that OWNS the domain")
    tokens[3] = "tok-2"
    check(orgs_bp._search_console_owns(org_ag, "priya-agency.in") is False, "full-user access alone never verifies")
    def boom(tok):
        raise _google_oauth.GoogleOAuthError("down")
    _google_oauth.list_gsc_sites = boom
    check(orgs_bp._search_console_owns(org_ag, "priya-agency.in") is False, "a Google error is 'not proven', not a crash")
    tokens[3] = "tok-3"
    _google_oauth.list_gsc_sites = fake_sites
    r = call(priya, "POST", W + "/check")
    check(r.status_code == 200 and r.get_json()["verified"] and r.get_json()["method"] == "search_console", "the check route falls back to Search Console ownership")
finally:
    A.list_org_search_console_connections, ac._access_token_for_connection, _google_oauth.list_gsc_sites = real
    V.check_meta_tag = real_meta
check(call(kushal, "GET", W).get_json()["status"] == "verified", "the whole company sees it verified")
r = call(adira, "PATCH", W, json={"discoverable": False})
check(r.status_code == 200 and r.get_json()["discoverable"] is False, "an admin turns findability off")
check(call(adira, "PATCH", W, json={"discoverable": "no"}).status_code == 400, "a non-boolean -> 400")
check(call(kushal, "PATCH", W, json={"discoverable": True}).status_code == 403, "a plain member can't")
call(adira, "PATCH", W, json={"discoverable": True})

# ═══ 4. connect-by-website over HTTP ════════════════════════════════════════
# 'zed' becomes the company that WANTS to connect; priya's agency is the verified one.
FW = f"/api/organizations/{org_zed}/link-requests/find-website"
BW = f"/api/organizations/{org_zed}/link-requests/by-website"
check(call(None, "POST", FW, json={"website": "priya-agency.in"}).status_code == 401, "lookup needs a login")
check(call(priya, "POST", FW, json={"website": "priya-agency.in"}).status_code == 404, "you can't look up as a company you don't run")
r = call(zed, "POST", FW, json={"website": "https://WWW.priya-agency.in/x"})
check(r.status_code == 200 and r.get_json() == {"found": True, "domain": "priya-agency.in"}, "found: only found + domain")
check(call(zed, "POST", FW, json={"website": "nope.example"}).get_json()["found"] is False, "an unknown site -> found:false")
check(call(zed, "POST", FW, json={"website": "nope"}).status_code == 400, "junk -> 400")
check(call(zed, "POST", BW, json={"website": "nope.example"}).status_code == 404, "asking a site with no verified company -> 404 no_match")
notices.clear()
r = call(zed, "POST", BW, json={"website": "priya-agency.in", "note": "hi"})
check(r.status_code == 201 and r.get_json()["status"] == "sent", "asking a verified company -> 201")
check([n["to"] for n in notices] == ["priya@example.com"], "its owner is emailed")
out = call(zed, "GET", f"/api/organizations/{org_zed}/link-requests").get_json()["requests"]
check(out[0]["via"] == "website" and out[0]["target_email"] is None and out[0]["target_org_id"] is None and "priya co" not in str(out[0]).lower() and "priya@" not in str(out[0]).lower(),
      "the asker's list identifies nothing about the company")
inc = call(priya, "GET", "/api/organizations/link-requests/incoming").get_json()["requests"]
check(len(inc) == 1 and inc[0]["via"] == "website" and inc[0]["payer_name"] == "zed Co", "the company's owner sees it")
check(call(adira, "GET", "/api/organizations/link-requests/incoming").get_json()["requests"] == [], "an admin doesn't")
rid = inc[0]["id"]
check(call(priya, "POST", f"/api/organizations/link-requests/{rid}/approve", json={"org_id": org_zed}).status_code == 404, "approving with a company that isn't the target -> refused")
r = call(priya, "POST", f"/api/organizations/link-requests/{rid}/approve", json={"org_id": org_ag})
check(r.status_code == 200 and A.get_payer_org_id(org_ag) == org_zed, "the owner approves the company it was addressed to")

# rate limit over HTTP
for _ in range(A.WEBSITE_LOOKUPS_PER_DAY):
    call(adira, "POST", f"/api/organizations/{org_ag}/link-requests/find-website", json={"website": "nope.example"})
check(call(adira, "POST", f"/api/organizations/{org_ag}/link-requests/find-website", json={"website": "nope.example"}).status_code == 429, "past the daily allowance -> 429")

print(f"OK — {passed} checks passed")

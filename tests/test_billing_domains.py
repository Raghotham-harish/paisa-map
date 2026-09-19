"""
test_billing_domains.py — billing-v2: prove a website is yours, and ask a
company to connect by its website.

A company's website means nothing until it PROVES it controls it (a tag in the
page's head, or a Search Console account that is a verified OWNER). Only a
verified, findable company can be reached by another company typing its website,
and even then the answer is only yes/no — no name, owner or email — and nothing
links without the owner's approval.

    DATABASE_URL="sqlite:////tmp/billing_domains.sqlite" python3 tests/test_billing_domains.py

Plain script, throwaway sqlite; the network checks run against a local server
with the public-address rule switched off for it (the rule itself is tested).
"""

import http.server
import os
import socket
import sys
import tempfile
import threading

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "paisamap-etl", "etl"))

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/billing_domains_default.sqlite")
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


def take():
    out = list(notices)
    notices.clear()
    return out


# ═══ 1. what counts as "the same website" ═══════════════════════════════════
N = V.normalize_domain
for raw, want in [
    ("https://WWW.Acme.co.in/about?x=1", "acme.co.in"), ("acme.com", "acme.com"), ("  http://acme.com:8080/x ", "acme.com"),
    ("https://user:pw@acme.com/", "acme.com"), ("ACME.COM.", "acme.com"), ("shop.acme.com", "shop.acme.com"),
    ("https://www.bücher.example/", "xn--bcher-kva.example"), ("https://a-b.c-d.io", "a-b.c-d.io"),
]:
    check(N(raw) == want, f"{raw!r} -> {want!r} (got {N(raw)!r})")
for bad in (None, "", "  ", 5, "localhost", "http://localhost/", "127.0.0.1", "http://10.0.0.1/", "https://[::1]/",
            "acme", "acme.", "-acme.com", "acme-.com", "a b.com", "http://", "https://acme.com\nevil.com", "x" * 300 + ".com",
            "acme.123", "javascript:alert(1)", "http://.com", "1.2.3.4", "a..com"):
    check(N(bad) is None, f"{bad!r} is not accepted as a plain public hostname (got {N(bad)!r})")
check(N("ftp://acme.com") == "acme.com", "the scheme never counts toward identity (the fetch later refuses non-http)")

# ═══ 2. the address rule ════════════════════════════════════════════════════
for ip in ("127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.0.10", "169.254.169.254", "0.0.0.0", "100.64.0.1", "::1", "fe80::1", "fc00::1",
           "::ffff:127.0.0.1", "::ffff:10.0.0.1", "224.0.0.1", "240.0.0.1", "not-an-ip", ""):
    check(not V._is_public_ip(ip), f"{ip!r} is not a public address")
for ip in ("8.8.8.8", "1.1.1.1", "2606:4700:4700::1111", "::ffff:8.8.8.8"):
    check(V._is_public_ip(ip), f"{ip!r} is public")
try:
    V._resolve_public("localhost", 80)
    check(False, "localhost must be refused")
except V.VerifyFetchError:
    check(True, "a name that resolves to loopback is refused")
real_gai = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [(2, 1, 6, "", ("8.8.8.8", 80)), (2, 1, 6, "", ("10.0.0.5", 80))]
try:
    try:
        V._resolve_public("mixed.example", 80)
        check(False, "a mixed public+internal answer must be refused")
    except V.VerifyFetchError:
        check(True, "a hostile DNS answer mixing a public and an internal address is refused outright")
finally:
    socket.getaddrinfo = real_gai
socket.getaddrinfo = lambda *a, **k: (_ for _ in ()).throw(socket.gaierror())
try:
    try:
        V._resolve_public("nope.invalid", 80)
        check(False, "unresolvable")
    except V.VerifyFetchError:
        check(True, "an unresolvable name is a clean error")
finally:
    socket.getaddrinfo = real_gai

# ═══ 3. reading the tag ═════════════════════════════════════════════════════
T = "pm-verify-abc"
page = lambda head, body="": f"<!doctype html><html><head><title>x</title>{head}</head><body>{body}</body></html>"
tag = V.tag_snippet(T)
check(V.find_verification_tags(page(tag)) == [T], "the tag in the head is found")
check(V.find_verification_tags(page(f"<META NAME='Paisamap-Site-Verification' CONTENT='{T}' />")) == [T], "case, quotes and self-closing don't matter")
check(V.find_verification_tags(page(f'<meta content="{T}" name="paisamap-site-verification">')) == [T], "attribute order doesn't matter")
check(V.find_verification_tags(page("", tag)) == [], "a tag in the BODY doesn't count (that's where user content lives)")
check(V.find_verification_tags(page(f'<meta name="other" content="{T}">')) == [], "another meta name doesn't count")
check(V.find_verification_tags(f"<html><body>{tag}</body></html>") == [], "a page with NO head at all: a tag after <body> starts still doesn't count")
check(V.find_verification_tags(f"<html><head><title>x</title><body>{tag}") == [], "...even when the head is never closed")
check(V.find_verification_tags(page(tag + tag.replace(T, "pm-verify-zzz"))) == [T, "pm-verify-zzz"], "several tags are all reported")
check(V.find_verification_tags("<<<not html") == [] and V.find_verification_tags(None) == [], "garbage is just 'no tag'")
check(V.find_verification_tags(f"<html><head>{tag}") == [T], "an unclosed head still works")

# ═══ 4. the fetch, against a local server ═══════════════════════════════════
HITS = {"loop": 0}


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        route = self.path
        if route == "/ok":
            body = page(tag).encode()
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers(); self.wfile.write(body)
        elif route == "/redir-same":
            self.send_response(302); self.send_header("Location", "/ok"); self.end_headers()
        elif route == "/redir-away":
            self.send_response(302); self.send_header("Location", f"http://127.0.0.1:{PORT}/ok"); self.end_headers()
        elif route == "/redir-scheme":
            self.send_response(302); self.send_header("Location", "file:///etc/passwd"); self.end_headers()
        elif route == "/loop":
            HITS["loop"] += 1
            self.send_response(302); self.send_header("Location", "/loop"); self.end_headers()
        elif route == "/redir-nowhere":
            self.send_response(302); self.end_headers()
        elif route == "/big":
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
            self.wfile.write(b"<html><head>" + b"x" * (V.MAX_BODY_BYTES * 3) + tag.encode() + b"</head></html>")
        else:
            self.send_response(500); self.end_headers()


server = http.server.HTTPServer(("127.0.0.1", 0), H)
PORT = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()
V.ALLOWED_PORTS = (PORT,)
real_public = V._is_public_ip
V._is_public_ip = lambda ip: ip == "127.0.0.1"          # ONLY for this local server
socket.getaddrinfo = lambda host, port, *a, **k: [(2, 1, 6, "", ("127.0.0.1", port))]   # ("localhost" also has an ::1 the server isn't on)
hosts = {"localhost"}
base = f"http://localhost:{PORT}"
try:
    final, html = V.fetch_page(base + "/ok", hosts)
    check(V.find_verification_tags(html) == [T], "a normal page is fetched and read")
    final, html = V.fetch_page(base + "/redir-same", hosts)
    check(final.endswith("/ok") and V.find_verification_tags(html) == [T], "a redirect within the same host is followed")
    for path, why in (("/redir-away", "a redirect to another host"), ("/redir-scheme", "a redirect to file://"), ("/loop", "a redirect loop"),
                      ("/redir-nowhere", "a redirect with no Location"), ("/boom", "a 500")):
        try:
            V.fetch_page(base + path, hosts)
            check(False, f"{why} must be refused")
        except V.VerifyFetchError as e:
            check(str(e), f"{why} is refused with a plain-language reason")
    check(HITS["loop"] == V.MAX_REDIRECTS + 1, f"a redirect loop is followed at most MAX_REDIRECTS times ({HITS['loop']} requests made)")
    final, html = V.fetch_page(base + "/big", hosts)
    check(len(html) <= V.MAX_BODY_BYTES and not V.find_verification_tags(html), "an oversized page is cut off at the cap (a tag past it isn't seen)")
    V.ALLOWED_PORTS = (80, 443)
    try:
        V.fetch_page(base + "/ok", hosts)
        check(False, "a non-standard port must be refused")
    except V.VerifyFetchError:
        check(True, "only ports 80 and 443 are fetched")
    V.ALLOWED_PORTS = (PORT,)
    V._is_public_ip = real_public
    try:
        V.fetch_page(base + "/ok", hosts)
        check(False, "loopback must be refused by the real rule")
    except V.VerifyFetchError:
        check(True, "with the real address rule, a site on a loopback address is refused (no reaching our own internals)")
finally:
    V._is_public_ip = real_public
    V.ALLOWED_PORTS = (80, 443)
    socket.getaddrinfo = real_gai
    server.shutdown()

# check_meta_tag through an injected fetch
calls = []
def fetch_ok(url, allowed):
    calls.append((url, sorted(allowed))); return url, page(V.tag_snippet("pm-verify-real"))
check(V.check_meta_tag("acme.com", "pm-verify-real", fetch=fetch_ok) == (True, None), "the right tag verifies")
check(calls[0][0] == "https://acme.com/" and calls[0][1] == ["acme.com", "www.acme.com"], "it tries https on the bare domain first and only ever allows the domain and its www twin")
ok, why = V.check_meta_tag("acme.com", "pm-verify-other", fetch=fetch_ok)
check(not ok and "isn't this company's code" in why, "a tag with someone else's code is refused, and says so")
ok, why = V.check_meta_tag("acme.com", "pm-verify-real", fetch=lambda u, a: (u, page("")))
check(not ok and "didn't find" in why, "no tag: refused, and says so")
def fetch_second(url, allowed):
    if url.startswith("https://acme.com"):
        raise V.VerifyFetchError("down")
    return url, page(V.tag_snippet("pm-verify-real"))
check(V.check_meta_tag("acme.com", "pm-verify-real", fetch=fetch_second)[0], "if https on the bare domain fails it falls back (http, then www)")
def fetch_dead(url, allowed):
    raise V.VerifyFetchError("We couldn't reach that website.")
check(V.check_meta_tag("acme.com", "x", fetch=fetch_dead) == (False, "We couldn't reach that website."), "an unreachable site is a clean failure")

# Search Console
S = V.gsc_owner_of
sites = lambda *pairs: [{"site_url": u, "permission_level": p} for u, p in pairs]
check(S(sites(("sc-domain:acme.com", "siteOwner")), "acme.com"), "a domain property you OWN proves the domain")
check(S(sites(("sc-domain:acme.com", "siteOwner")), "shop.acme.com"), "...and its subdomains")
check(not S(sites(("sc-domain:acme.com", "siteOwner")), "notacme.com"), "...but not a domain that merely ends the same way")
check(S(sites(("https://www.acme.com/", "siteOwner")), "acme.com"), "a URL-prefix property you own proves its host (www or not)")
check(not S(sites(("https://www.acme.com/", "siteOwner")), "shop.acme.com"), "a URL-prefix property doesn't cover other subdomains")
for level in ("siteFullUser", "siteRestrictedUser", "siteUnverifiedUser", None):
    check(not S(sites(("sc-domain:acme.com", level)), "acme.com"), f"permission {level!r} proves nothing (an agency is routinely given it)")
check(not S(sites(("sc-domain:other.com", "siteOwner")), "acme.com") and not S([], "acme.com") and not S(None, "acme.com"), "owning a different site, or no sites, proves nothing")

# ═══ 5. the verification flow ═══════════════════════════════════════════════
priya, org_ag = person("priya")                    # agency owner
adira, _ = person("adira")                         # agency admin
kushal, _ = person("kushal")                       # agency plain member
A.add_org_member(org_ag, priya, "adira@example.com", "admin")
A.add_org_member(org_ag, priya, "kushal@example.com", "member")
acme_o1, org_acme = person("acme1")                # Acme's owner
acme_o2, _ = person("acme2")                       # a second owner
acme_ad, _ = person("acmeadmin")                   # an admin (not an owner)
A.add_org_member(org_acme, acme_o1, "acme2@example.com", "owner")
A.add_org_member(org_acme, acme_o1, "acmeadmin@example.com", "admin")
zed, org_zed = person("zed")                       # a stranger
rival, org_rival = person("rival")                 # someone else claiming Acme's site

check(A.start_domain_verification(acme_o1, org_acme) == {"error": "no_website"}, "you can't start verifying with no website set")
A.update_organization(org_acme, acme_o1, website_url="not a url")
check(A.start_domain_verification(acme_o1, org_acme) == {"error": "invalid_website"}, "a website that isn't a plain public hostname can't be verified")
v = A.get_domain_verification(acme_o1, org_acme)
check(v["invalid_website"] and v["status"] == "none" and v["domain"] is None, "the view flags an unusable website")
A.update_organization(org_acme, acme_o1, website_url="https://www.Acme-Foods.in/about")
v = A.get_domain_verification(acme_o1, org_acme)
check(v["status"] == "none" and v["domain"] == "acme-foods.in" and v["can_manage"] and "token" not in v, "before starting: status none, the domain shown, no token yet")
check(A.start_domain_verification(zed, org_acme) == {"error": "not_found"}, "a stranger is not_found")
A.add_org_member(org_acme, acme_o1, "kushal@example.com", "member")
check(A.start_domain_verification(kushal, org_acme) == {"error": "forbidden"}, "a plain member can't start it")
v = A.start_domain_verification(acme_ad, org_acme)
check(v["status"] == "pending" and v["token"].startswith("pm-verify-") and v["tag"] == V.tag_snippet(v["token"]), "an admin starts it: pending, with the token and the tag to paste")
tok1 = v["token"]
check(A.start_domain_verification(acme_o1, org_acme)["token"] == tok1, "starting again keeps the same token (nothing to re-paste)")
mv = A.get_domain_verification(kushal, org_acme)
check(mv["status"] == "pending" and not mv["can_manage"] and "token" not in mv and "tag" not in mv, "a plain member sees the status but never the token")
check(A.get_domain_verification(zed, org_acme) == {"error": "not_found"}, "a stranger gets not_found")

# checks
def meta_no(d, t): return False, "We reached the site but didn't find the verification tag in its <head>."
def meta_yes(d, t): return True, None
check(A.check_domain_verification(zed, org_acme, meta_check=meta_yes) == {"error": "not_found"}, "a stranger can't run the check")
check(A.check_domain_verification(kushal, org_acme, meta_check=meta_yes) == {"error": "forbidden"}, "a plain member can't")
check(A.check_domain_verification(acme_o1, org_ag, meta_check=meta_yes) == {"error": "not_found"}, "the wrong company is not_found")
check(A.check_domain_verification(priya, org_ag, meta_check=meta_yes) == {"error": "not_started"}, "checking before starting is refused")
r = A.check_domain_verification(acme_o1, org_acme, meta_check=meta_no)
check(r["verified"] is False and r["status"] == "pending" and "didn't find" in r["reason"], "a failed check stays pending and says why")
r2 = A.check_domain_verification(acme_o1, org_acme, meta_check=meta_yes)
check(r2["error"] == "too_soon" and 1 <= r2["retry_after"] <= A.DOMAIN_CHECK_MIN_INTERVAL + 1, "checks are throttled per company (the button can't be hammered)")
sql("UPDATE org_domains SET last_checked_at = datetime(last_checked_at, '-1 minute') WHERE org_id = :o", o=org_acme)
seen = []
def gsc_yes(org, d): seen.append((org, d)); return True
r3 = A.check_domain_verification(acme_o1, org_acme, meta_check=meta_no, gsc_check=gsc_yes)
check(r3["verified"] and r3["status"] == "verified" and r3["method"] == "search_console" and seen == [(org_acme, "acme-foods.in")], "Search Console ownership verifies when the tag isn't there")
check(r3["verified_at"] and A.get_domain_verification(kushal, org_acme)["status"] == "verified", "everyone in the company sees it as verified")
check(A.check_domain_verification(acme_o1, org_acme, meta_check=meta_no)["verified"] is True, "an already-verified company just reports so (no re-fetch)")
act = [a["action"] for a in A.list_activity(acme_o1)]
check("website_verified" in act, "verification is recorded in the activity log")

# a company that fails GSC with an exception still just fails cleanly
A.update_organization(org_rival, rival, website_url="acme-foods.in")
A.start_domain_verification(rival, org_rival)
def gsc_boom(o, d): raise RuntimeError("google down")
r = A.check_domain_verification(rival, org_rival, meta_check=meta_no, gsc_check=gsc_boom)
check(r["verified"] is False and r["status"] == "pending", "a Search Console outage is just 'not proven this way'")
sql("UPDATE org_domains SET last_checked_at = datetime(last_checked_at, '-1 minute') WHERE org_id = :o", o=org_rival)
r = A.check_domain_verification(rival, org_rival, meta_check=meta_no)
check("domain_taken" not in str(r) and r["status"] == "pending", "a company that has NOT proved control is never told the domain is held by someone else (no probing)")
sql("UPDATE org_domains SET last_checked_at = datetime(last_checked_at, '-1 minute') WHERE org_id = :o", o=org_rival)
r = A.check_domain_verification(rival, org_rival, meta_check=meta_yes)
check(r == {"error": "domain_taken"}, "a second company that DOES prove the same site is told it's already verified elsewhere")
check(A.get_domain_verification(rival, org_rival)["status"] == "pending" and A.get_domain_verification(acme_o1, org_acme)["status"] == "verified", "the first holder is untouched")
try:
    sql("UPDATE org_domains SET status = 'verified' WHERE org_id = :o", o=org_rival)
    check(False, "the unique index must stop two verified rows")
except Exception:
    check(True, "the database itself refuses two companies holding one verified domain (safety net for a race)")

# editing the website
A.update_organization(org_acme, acme_o1, name="Acme Foods Ltd")
check(A.get_domain_verification(acme_o1, org_acme)["status"] == "verified", "renaming the company keeps its verification")
A.update_organization(org_acme, acme_o1, website_url="http://acme-foods.in/contact")
check(A.get_domain_verification(acme_o1, org_acme)["status"] == "verified", "a different page, scheme or www on the SAME domain keeps it")
A.update_organization(org_acme, acme_o1, website_url="https://acme-foods.com")
v = A.get_domain_verification(acme_o1, org_acme)
check(v["status"] == "none" and v["domain"] == "acme-foods.com" and "token" not in v, "a DIFFERENT domain drops the verification (a proof only ever covers what it proved)")
check(sql("SELECT COUNT(*) FROM org_domains WHERE org_id = :o", o=org_acme).scalar() == 0, "...and the old row is gone")
A.update_organization(org_acme, acme_o1, website_url="acme-foods.in")
A.start_domain_verification(acme_o1, org_acme)
sql("UPDATE org_domains SET last_checked_at = NULL WHERE org_id = :o", o=org_acme)
check(A.check_domain_verification(acme_o1, org_acme, meta_check=meta_yes)["verified"], "the original domain can be re-proved")
# starting again after the website changed BEHIND our back (e.g. edited in the database) issues a fresh token
old_tok = A.get_domain_verification(acme_o1, org_acme)["token"]
sql("UPDATE organizations SET website_url = 'https://moved-acme.in' WHERE id = :o", o=org_acme)
check(A.get_domain_verification(acme_o1, org_acme)["status"] == "none", "a proof for the old domain isn't shown for the new one")
v = A.start_domain_verification(acme_o1, org_acme)
check(v["domain"] == "moved-acme.in" and v["token"] != old_tok and v["status"] == "pending", "starting again for a different domain gets a NEW token (an old proof can't be reused)")
check(sql("SELECT COUNT(*) FROM org_domains WHERE org_id = :o", o=org_acme).scalar() == 1, "...and replaces the old row rather than adding a second")
sql("UPDATE organizations SET website_url = 'https://acme-foods.in' WHERE id = :o", o=org_acme)
A.start_domain_verification(acme_o1, org_acme)
sql("UPDATE org_domains SET last_checked_at = NULL, status = 'verified', method = 'meta_tag' WHERE org_id = :o", o=org_acme)
# a stale row (website changed behind our back) counts for nothing
sql("UPDATE organizations SET website_url = 'https://elsewhere.io' WHERE id = :o", o=org_acme)
check(A.get_domain_verification(acme_o1, org_acme)["status"] == "none", "a verification row for a domain the company no longer claims is ignored")
check(A.find_company_by_website(priya, org_ag, "elsewhere.io") == {"found": False, "domain": "elsewhere.io"}, "...and can't be found by anyone")
sql("UPDATE organizations SET website_url = 'https://acme-foods.in' WHERE id = :o", o=org_acme)

# the website is changed WHILE a check is running: the result must not verify the new domain on the old proof
sql("UPDATE org_domains SET status = 'pending', method = NULL, verified_at = NULL, last_checked_at = NULL WHERE org_id = :o", o=org_acme)
def meta_swaps_site(d, t):
    sql("UPDATE organizations SET website_url = 'https://swapped-acme.in' WHERE id = :o", o=org_acme)
    return True, None
check(A.check_domain_verification(acme_o1, org_acme, meta_check=meta_swaps_site) == {"error": "website_changed"}, "a website edited mid-check is refused (website_changed), never verified")
check(sql("SELECT status FROM org_domains WHERE org_id = :o", o=org_acme).scalar() == "pending", "...and the row stays pending")
sql("UPDATE organizations SET website_url = 'https://acme-foods.in' WHERE id = :o", o=org_acme)
sql("UPDATE org_domains SET status = 'verified', method = 'meta_tag', verified_at = CURRENT_TIMESTAMP WHERE org_id = :o", o=org_acme)

# discoverable + removal
check(A.set_domain_discoverable(acme_o1, org_acme, "yes") == {"error": "invalid_value"} and A.set_domain_discoverable(acme_o1, org_acme, 1) == {"error": "invalid_value"}, "discoverable must be a real boolean")
check(A.set_domain_discoverable(kushal, org_acme, False) == {"error": "forbidden"}, "a plain member can't change it")
check(A.get_domain_verification(acme_o1, org_acme)["discoverable"] is True, "findable by default (with the choice shown to the owner)")

# ═══ 6. finding a company by its website ════════════════════════════════════
check(A.find_company_by_website(kushal, org_ag, "acme-foods.in") == {"error": "forbidden"}, "a plain member can't look companies up")
check(A.find_company_by_website(zed, org_ag, "acme-foods.in") == {"error": "not_found"}, "a stranger can't either")
check(A.find_company_by_website(priya, org_ag, "acme") == {"error": "invalid_website"}, "a junk website is refused")
r = A.find_company_by_website(priya, org_ag, "https://WWW.acme-foods.in/anything")
check(r == {"found": True, "domain": "acme-foods.in"}, "a verified, findable company is found — and the answer is ONLY found + the domain typed")
check(A.find_company_by_website(priya, org_ag, "nobody.example") == {"found": False, "domain": "nobody.example"}, "an unknown site is found:false")
check(A.find_company_by_website(priya, org_acme, "acme-foods.in") == {"error": "not_found"}, "you can't look up as a company you don't run")
check(A.find_company_by_website(acme_o1, org_acme, "acme-foods.in")["found"] is False, "a company doesn't find itself")
sql("UPDATE org_domains SET discoverable = 0 WHERE org_id = :o", o=org_acme)
check(A.find_company_by_website(priya, org_ag, "acme-foods.in")["found"] is False, "a company that opted out isn't found")
check(A.set_domain_discoverable(acme_ad, org_acme, True)["discoverable"] is True, "an admin turns it back on")
sql("UPDATE org_domains SET status = 'pending' WHERE org_id = :o", o=org_acme)
check(A.find_company_by_website(priya, org_ag, "acme-foods.in")["found"] is False, "an UNVERIFIED claim can't be found (nobody can match on an unproven website)")
sql("UPDATE org_domains SET status = 'verified' WHERE org_id = :o", o=org_acme)
# already linked / a payer is not offered
other_payer_id = A.create_organization(zed, "Zed Holdings")["id"]
sql("UPDATE organizations SET billing_org_id = :p WHERE id = :o", p=other_payer_id, o=org_acme)
check(A.find_company_by_website(priya, org_ag, "acme-foods.in")["found"] is False, "a company someone else already pays for isn't offered (and nothing says why)")
sql("UPDATE organizations SET billing_org_id = NULL WHERE id = :o", o=org_acme)
sql("UPDATE organizations SET billing_org_id = :p WHERE id = :o", p=org_acme, o=other_payer_id)
check(A.find_company_by_website(priya, org_ag, "acme-foods.in")["found"] is False, "a company that pays for others can't be paid for, so isn't offered")
sql("UPDATE organizations SET billing_org_id = NULL WHERE id = :o", o=other_payer_id)
check(A.find_company_by_website(priya, org_ag, "acme-foods.in")["found"] is True, "back to offerable once neither applies")
n_before = sql("SELECT COUNT(*) FROM activity_log WHERE user_id = :u AND action = 'website_lookup'", u=priya).scalar()
check(all(a["action"] != "website_lookup" for a in A.list_activity(priya)), "the internal lookup counter never appears in someone's activity feed")

# the daily allowance
for _ in range(A.WEBSITE_LOOKUPS_PER_DAY - n_before):
    check(A.find_company_by_website(priya, org_ag, "nobody.example").get("found") is False, "lookups within the allowance answer")
check(A.find_company_by_website(priya, org_ag, "acme-foods.in") == {"error": "rate_limited"}, "past the daily allowance a person is refused (no enumerating who uses PaisaMap)")
check(A.create_website_link_request(priya, org_ag, "acme-foods.in") == {"error": "rate_limited"}, "...and the request path can't be used to get around it")
check(A.find_company_by_website(adira, org_ag, "acme-foods.in")["found"] is True, "the allowance is per person")
sql("UPDATE activity_log SET created_at = datetime(created_at, '-2 days') WHERE user_id = :u AND action = 'website_lookup'", u=priya)
check(A.find_company_by_website(priya, org_ag, "acme-foods.in")["found"] is True, "and it resets after a day")

# ═══ 7. asking by website ═══════════════════════════════════════════════════
take()
check(A.create_website_link_request(kushal, org_ag, "acme-foods.in") == {"error": "forbidden"}, "a plain member can't send one")
check(A.create_website_link_request(adira, org_ag, "acme") == {"error": "invalid_website"}, "junk website refused")
check(A.create_website_link_request(adira, org_ag, "nobody.example") == {"error": "no_match"}, "no verified company there: no_match")
sql("UPDATE org_domains SET status = 'pending' WHERE org_id = :o", o=org_acme)
check(A.create_website_link_request(adira, org_ag, "acme-foods.in") == {"error": "no_match"}, "an unverified company is indistinguishable from none")
sql("UPDATE org_domains SET status = 'verified' WHERE org_id = :o", o=org_acme)
check(take() == [], "nothing is mailed for a request that matched nothing")
sent = A.create_website_link_request(adira, org_ag, "https://www.acme-foods.in/", "  we'd love to  cover your credits ")
check(set(sent) == {"status", "request_id"} and sent["status"] == "sent", "a matching request is sent")
mail = take()
check({m["to"] for m in mail} == {"acme1@example.com", "acme2@example.com"} and all(m["kind"] == "requested" for m in mail),
      "only the company's OWNERS are told (not its admin or members) — and only people who already have accounts")
check(all(m["payer_name"] == "priya Co" and m["note"] == "we'd love to cover your credits" for m in mail), "the mail names the asking company and carries the cleaned note")
again = A.create_website_link_request(priya, org_ag, "acme-foods.in", "second try")
check(again["request_id"] == sent["request_id"], "asking again refreshes the one open request instead of stacking another")

out = A.list_outgoing_link_requests(priya, org_ag)["requests"]
row = next(r for r in out if r["id"] == sent["request_id"])
check(row["via"] == "website" and row["target_domain"] == "acme-foods.in" and row["status"] == "pending", "the asker sees the request and the website it typed")
check(row["target_email"] is None and row["target_org_id"] is None and row["target_org_name"] is None,
      "...but never the company's email, id or name while it's pending (no way to learn who owns it)")
check("acme1@example.com" not in str(out) and "Acme" not in str(out) and "Foods" not in str(out) and str(org_acme) not in str(row.get("target_org_id")), "nothing in the whole listing identifies the owners")

# who can answer
inc_owner = A.list_incoming_link_requests(acme_o1)["requests"]
check(len(inc_owner) == 1 and inc_owner[0]["id"] == sent["request_id"] and inc_owner[0]["via"] == "website" and inc_owner[0]["payer_name"] == "priya Co",
      "an owner of the target company sees it in their inbox")
check([c["org_id"] for c in inc_owner[0]["companies"]] == [org_acme], "...and can only link the company it was addressed to (not another they own)")
second_co = A.create_organization(acme_o1, "Acme Cafes")["id"]
sql("UPDATE organizations SET billing_org_id = NULL WHERE id = :o", o=second_co)
check([c["org_id"] for c in A.list_incoming_link_requests(acme_o1)["requests"][0]["companies"]] == [org_acme], "even after they own a second linkable company")
check(len(A.list_incoming_link_requests(acme_o2)["requests"]) == 1, "the other owner sees it too")
check(A.list_incoming_link_requests(acme_ad)["requests"] == [], "an admin (not an owner) does not")
check(A.list_incoming_link_requests(kushal)["requests"] == [] and A.list_incoming_link_requests(zed)["requests"] == [], "members of other companies see nothing")
check(A.approve_link_request(acme_ad, sent["request_id"], org_acme) == {"error": "not_found"}, "a non-owner can't approve")
check(A.approve_link_request(zed, sent["request_id"], org_zed) == {"error": "not_found"}, "a stranger can't approve")
check(A.approve_link_request(priya, sent["request_id"], org_ag) == {"error": "not_found"}, "the asking company can't approve its own request")
check(A.approve_link_request(acme_o1, sent["request_id"], second_co) == {"error": "invalid_company"}, "approving with a DIFFERENT company of theirs is refused (and the request stays open)")
check(sql("SELECT status FROM credit_link_requests WHERE id = :i", i=sent["request_id"]).scalar() == "pending", "still pending after that")

# the target loses its verified website before answering
sql("UPDATE org_domains SET status = 'pending' WHERE org_id = :o", o=org_acme)
check(A.approve_link_request(acme_o1, sent["request_id"], org_acme) == {"error": "request_invalid"}, "if the company no longer holds that verified website, the request lapses instead of linking")
check(sql("SELECT status FROM credit_link_requests WHERE id = :i", i=sent["request_id"]).scalar() == "cancelled", "...and is cancelled")
sql("UPDATE org_domains SET status = 'verified' WHERE org_id = :o", o=org_acme)

# decline
s2 = A.create_website_link_request(adira, org_ag, "acme-foods.in")
take()
check(A.decline_link_request(acme_o2, s2["request_id"]) == {"status": "ok"}, "the other owner declines")
check({m["kind"] for m in take()} == {"declined"}, "the asking company's admins are told")
check(next(r for r in A.list_outgoing_link_requests(priya, org_ag)["requests"] if r["id"] == s2["request_id"])["target_org_name"] is None,
      "a declined website request still doesn't name the company")

# approve
s3 = A.create_website_link_request(adira, org_ag, "acme-foods.in", "please")
take()
check(A.approve_link_request(acme_o2, s3["request_id"], org_acme) == {"status": "ok", "org_id": org_acme}, "an owner approves")
check(sql("SELECT billing_org_id FROM organizations WHERE id = :o", o=org_acme).scalar() == org_ag, "the company is now paid for by the asker")
check({m["kind"] for m in take()} == {"approved"}, "the asking company's admins are told")
done = next(r for r in A.list_outgoing_link_requests(priya, org_ag)["requests"] if r["id"] == s3["request_id"])
check(done["status"] == "approved" and done["target_org_name"] == "Acme Foods Ltd" and done["target_org_id"] == org_acme, "only NOW does the asker see which company it was")
check(A.find_company_by_website(priya, org_ag, "acme-foods.in")["found"] is False, "a company that is already paid for is no longer offered")
check(A.create_website_link_request(priya, org_ag, "acme-foods.in") == {"error": "no_match"}, "...and can't be asked again")

# caps carry over from the email path
sql("UPDATE organizations SET billing_org_id = NULL WHERE id = :o", o=org_acme)
for i in range(A.LINK_REQUEST_MAX_PENDING):
    sql("INSERT INTO credit_link_requests (payer_org_id, requested_by, target_email, status, created_at, expires_at, via) "
        "VALUES (:p, :u, :e, 'pending', CURRENT_TIMESTAMP, datetime('now', '+5 days'), 'email')", p=org_ag, u=priya, e=f"cap{i}@example.com")
check(A.create_website_link_request(priya, org_ag, "acme-foods.in") == {"error": "too_many_pending"}, "the open-request cap applies to website requests too")
sql("DELETE FROM credit_link_requests WHERE target_email LIKE 'cap%'")

# deleting the target cancels what was aimed at it
s4 = A.create_website_link_request(priya, org_ag, "acme-foods.in")
sql("UPDATE organizations SET billing_org_id = NULL WHERE id = :o", o=second_co)
A.delete_organization(second_co, acme_o1)
check(sql("SELECT status FROM credit_link_requests WHERE id = :i", i=s4["request_id"]).scalar() == "pending", "deleting a DIFFERENT company leaves the request alone")
A.delete_organization(org_acme, acme_o1)
check(sql("SELECT status FROM credit_link_requests WHERE id = :i", i=s4["request_id"]).scalar() == "cancelled", "deleting the target company cancels requests aimed at it")
check(sql("SELECT COUNT(*) FROM org_domains WHERE org_id = :o", o=org_acme).scalar() == 0, "...and removes its verification")

# emailed requests are untouched by any of this
r_email = A.create_link_request(priya, org_ag, "zed@example.com")
inc = A.list_incoming_link_requests(zed)["requests"]
check(len(inc) == 1 and inc[0]["via"] == "email" and inc[0]["companies"], "an emailed request still reaches the person it was emailed to")
out = A.list_outgoing_link_requests(priya, org_ag)["requests"]
check(next(r for r in out if r["id"] == r_email["request_id"])["target_email"] == "zed@example.com", "...and the asker still sees the email they typed")

print(f"OK — {passed} checks passed")

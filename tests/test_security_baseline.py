"""
test_security_baseline.py — Phase H5: automated checks for the security
findings from the pen-test pass (see docs/PENTEST_CHECKLIST.md). Sibling of
test_cross_account_isolation.py; same conventions (plain script, no pytest,
throwaway sqlite DB, a failure is a real regression).

    DATABASE_URL="sqlite:////tmp/security_baseline.sqlite" \
      python3 tests/test_security_baseline.py

Covers:
  1. Route auth sweep — every route, hit unauthenticated, must be 401/404/503
     unless it is on the explicit PUBLIC allowlist below. A new route shipped
     without @require_login fails here instead of going live silently.
  2. /api/enrich input hardening (public, spawns a subprocess): pincode/lat/lng
     validation, place-name sanitising (markup, control chars, formula
     injection), source whitelist.
  3. /api/enrich resource caps: concurrent subprocesses and outstanding jobs.
  4. /api/status must not leak subprocess stderr.
  5. Static-file path traversal, session-cookie flags, response headers.
  6. Customer-upload parsers stop at the row cap instead of loading the whole
     file (zip-bomb style .xlsx / huge CSV).
  7. Razorpay webhook rejects a bad signature (skipped if razorpay isn't
     installed locally).
"""

import os
import re
import sys
import threading
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "paisamap-etl", "etl"))

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/security_baseline_default.sqlite")
if "sqlite" not in os.environ["DATABASE_URL"]:
    raise SystemExit("Refusing to run: DATABASE_URL doesn't look like a throwaway sqlite file.")
if not os.environ.get("CUSTOMER_DATA_KEY"):
    from cryptography.fernet import Fernet
    os.environ["CUSTOMER_DATA_KEY"] = Fernet.generate_key().decode()
os.environ["ENRICH_MAX_CONCURRENT"] = "2"
os.environ["ENRICH_MAX_OUTSTANDING"] = "5"

import _db
_db.enabled = lambda: False
import _auth_db
_auth_db.init_schema()
_auth_db.migrate_schema()

import server
app = server.app
app.testing = True

passed = 0


def check(cond, msg):
    global passed
    assert cond, f"FAIL: {msg}"
    passed += 1


# ── 1. Route auth sweep ──────────────────────────────────────────────────────
# Routes that are public BY DESIGN. Anything else answering an anonymous
# request with 2xx/400 is a missing @require_login. Adding to this list is a
# deliberate, reviewable act.
PUBLIC = {
    "/", "/privacy", "/terms", "/refund", "/contact", "/data/<path:fname>", "/assets/<path:fname>",
    "/api/auth/logout", "/api/auth/google", "/api/billing/pricing",
    "/api/billing/webhook", "/api/config", "/api/db_status", "/api/enrich",
    "/api/enrich_stats", "/api/export", "/api/health", "/api/metrics",
    "/api/reverse", "/api/search", "/api/signals/catalog",
    "/api/status/<pincode>", "/api/intelligence/compare",
    "/api/intelligence/score", "/api/projects/shared/<token>",
    "/api/reports/shared/<token>", "/api/organizations/invites/<token>",
    "/api/organizations/invites/<token>/decline",
}
NEVER_CALLED = {"/api/enrich", "/api/reverse", "/api/search"}  # subprocess / Nominatim

client = app.test_client()
unprotected = []
for rule in app.url_map.iter_rules():
    if rule.endpoint == "static" or rule.rule in NEVER_CALLED:
        continue
    url = re.sub(r"<(?:[a-z]+:)?[a-z_]+>", "1", rule.rule)
    for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
        code = client.open(url, method=method, json={} if method != "GET" else None).status_code
        if code in (401, 403, 404, 503):
            continue
        if rule.rule not in PUBLIC:
            unprotected.append((method, rule.rule, code))
check(not unprotected, f"routes answering anonymous requests but not on the PUBLIC allowlist: {unprotected}")

# ── 2. /api/enrich input hardening ───────────────────────────────────────────
calls = []
release = threading.Event()
real_run = server.subprocess.run


class _Done:
    returncode, stdout, stderr = 0, "PPI (ML): 100\n  household income: ₹50,000\n", ""


def fake_run(argv, **kw):
    calls.append(argv)
    release.wait(5)
    return _Done()


server.subprocess.run = fake_run
server._append_log = lambda *a, **k: None       # never touch the real CSVs
server._mirror_to_static = lambda: None


def enrich(**q):
    base = {"pincode": "560001", "lat": "12.97", "lng": "77.59", "name": "MG Road"}
    base.update(q)
    return client.get("/api/enrich", query_string=base)


def reset():
    release.set()
    time.sleep(0.15)
    server._jobs.clear()
    calls.clear()
    release.clear()


for bad, why in [
    ({"pincode": "56000"}, "5-digit pincode"),
    ({"pincode": "5600011"}, "7-digit pincode"),
    ({"pincode": "abcdef"}, "non-numeric pincode"),
    ({"pincode": "560001; rm -rf /"}, "shell metacharacters in pincode"),
    ({"pincode": "--help"}, "option-looking pincode"),
    ({"lat": "abc"}, "non-numeric lat"),
    ({"lat": "nan"}, "NaN lat"),
    ({"lat": "51.5", "lng": "-0.12"}, "London coordinates"),
    ({"lat": "0", "lng": "0"}, "null island"),
]:
    reset()
    r = enrich(**bad)
    check(r.status_code == 400, f"{why} must be rejected with 400, got {r.status_code}")
    check(not calls, f"{why} must not spawn a subprocess")

reset()
check(enrich(name="<img src=x onerror=alert(1)>Bandra").status_code == 200, "valid call accepted")
time.sleep(0.15)
name_arg = calls[0][-1]
check("<" not in name_arg and ">" not in name_arg, f"markup stripped from name, got {name_arg!r}")

for raw, want in [
    ("=HYPERLINK(\"http://evil\",\"x\")", "HYPERLINK(\"http://evil\",\"x\")"),
    ("+cmd|' /C calc'!A0", "cmd|' /C calc'!A0"),
    ("@SUM(1+1)", "SUM(1+1)"),
    ("-2+3", "2+3"),
    ("Line\nBreak\tTab\x00Nul", "Line Break Tab Nul"),
    ("वस्त्रापुर", "वस्त्रापुर"),                 # non-ASCII scripts survive
    ("St. Mary's & Sons", "St. Mary's & Sons"),  # apostrophe/ampersand survive
    ("x" * 500, "x" * 80),
    ("", "560001"),                              # empty falls back to the pincode
    ("   ", "560001"),
]:
    reset()
    enrich(name=raw)
    time.sleep(0.15)
    check(calls and calls[0][-1] == want, f"name {raw!r} -> {calls[0][-1] if calls else None!r}, want {want!r}")

reset()
enrich(source="<script>")
check(server._jobs["560001"]["source"] == "yah", "unknown source falls back to 'yah'")

# ── 3. Resource caps ─────────────────────────────────────────────────────────
reset()
running = {"now": 0, "peak": 0}
guard = threading.Lock()


def counting_run(argv, **kw):
    with guard:
        running["now"] += 1
        running["peak"] = max(running["peak"], running["now"])
    release.wait(5)
    with guard:
        running["now"] -= 1
    return _Done()


server.subprocess.run = counting_run
codes = [enrich(pincode=f"56000{i}").status_code for i in range(1, 8)]
time.sleep(0.3)
check(running["peak"] <= 2, f"concurrent subprocesses must stay <= ENRICH_MAX_CONCURRENT (2), peak {running['peak']}")
check(codes[:5] == [200] * 5, f"first 5 jobs accepted, got {codes}")
check(codes[5:] == [503, 503], f"jobs beyond ENRICH_MAX_OUTSTANDING (5) shed with 503, got {codes}")
r = enrich(pincode="560009")
check(r.headers.get("Retry-After"), "503 carries Retry-After")
check(enrich(pincode="560001").status_code == 200, "re-requesting an already-running pincode is still answered (idempotent)")
release.set()
time.sleep(0.4)
check(running["peak"] <= 2, "cap held while queued jobs drained")

# ── 4. /api/status must not leak stderr ──────────────────────────────────────
reset()


class _Failed:
    returncode = 1
    stdout = ""
    stderr = "Traceback (most recent call last):\n  File \"/home/ubuntu/paisa-map/paisamap-etl/etl/enrich_single.py\", line 9\nKeyError"


server.subprocess.run = lambda argv, **kw: _Failed()
enrich(pincode="560020")
time.sleep(0.3)
body = client.get("/api/status/560020").get_data(as_text=True)
check('"error"' in body, "failed job reports an error status")
check("/home/ubuntu" not in body and "Traceback" not in body, f"stderr/paths leaked via /api/status: {body[:200]}")
server.subprocess.run = real_run

# ── 5. Traversal, cookie flags, headers ──────────────────────────────────────
for u in ["/data/../server.py", "/data/%2e%2e/server.py", "/data/..%2fserver.py",
          "/assets/../server.py", "/assets/%2e%2e/deploy.sh", "/data/output/../../server.py"]:
    check(client.get(u).status_code == 404, f"path traversal must 404: {u}")

check(app.config["SESSION_COOKIE_HTTPONLY"] is True, "session cookie is HttpOnly")
check(app.config["SESSION_COOKIE_SAMESITE"] == "Lax", "session cookie is SameSite=Lax")
check(app.config["SESSION_COOKIE_NAME"] == "pm_session", "cookie name unchanged")
r = client.get("/api/health")
check(r.headers.get("X-Content-Type-Options") == "nosniff", "nosniff header")
check(r.headers.get("X-Frame-Options") == "SAMEORIGIN", "X-Frame-Options on API responses")
check("frame-ancestors" in r.headers.get("Content-Security-Policy", ""), "frame-ancestors CSP")
check(r.headers.get("Referrer-Policy"), "Referrer-Policy header")

# ── 6. Customer-upload parsing is bounded ────────────────────────────────────
import io
from blueprints import customer_data as cd

big_csv = ("name,pincode\n" + "s,560001\n" * 100_000).encode()
headers, rows = cd._parse_csv(big_csv)
check(len(rows) <= cd.MAX_UPLOAD_ROWS + 1, f"CSV parser must stop early, kept {len(rows)} rows")

blank_csv = ("name,pincode\n" + ",\n" * 200_000).encode()
headers, rows = cd._parse_csv(blank_csv)
check(rows == [], "blank-row padding yields no data rows (and terminates)")

try:
    import openpyxl
except ImportError:
    print("  (openpyxl not installed locally — xlsx bound check skipped)")
else:
    wb = openpyxl.Workbook(write_only=True)
    ws = wb.create_sheet()
    ws.append(["name", "pincode"])
    for _ in range(30_000):
        ws.append(["s", "560001"])
    buf = io.BytesIO()
    wb.save(buf)
    headers, rows = cd._parse_xlsx(buf.getvalue())
    check(len(rows) <= cd.MAX_UPLOAD_ROWS + 1, f"XLSX parser must stop early, kept {len(rows)} rows")

user = _auth_db.upsert_user("sec-baseline-sub", "sec@example.com", "Sec", None)
project = _auth_db.create_project(user["id"], "Sec Test", avg_ticket=500)
with client.session_transaction() as sess:
    sess["user_id"] = user["id"]
resp = client.post("/api/customer-data/uploads",
                   data={"project_id": str(project["id"]),
                         "file": (io.BytesIO(big_csv), "stores.csv")},
                   content_type="multipart/form-data")
check(resp.status_code == 400 and resp.get_json().get("error") == "too_many_rows",
      f"oversized upload rejected with too_many_rows, got {resp.status_code} {resp.get_json()}")
ok_csv = ("store_name,pincode\n" + "".join(f"S{i},560001\n" for i in range(50))).encode()
resp = client.post("/api/customer-data/uploads",
                   data={"project_id": str(project["id"]),
                         "file": (io.BytesIO(ok_csv), "stores.csv")},
                   content_type="multipart/form-data")
check(resp.status_code == 201 and resp.get_json()["upload"]["row_count"] == 50,
      f"a normal 50-row upload still works, got {resp.status_code}")
with client.session_transaction() as sess:
    sess.clear()

# ── 7. Webhook signature ─────────────────────────────────────────────────────
try:
    import razorpay  # noqa: F401
except ImportError:
    print("  (razorpay not installed locally — webhook signature check skipped)")
else:
    os.environ["RAZORPAY_WEBHOOK_SECRET"] = "test-secret"
    r = client.post("/api/billing/webhook", data=b'{"event":"payment.captured"}',
                    headers={"X-Razorpay-Signature": "deadbeef", "Content-Type": "application/json"})
    check(r.status_code == 400, f"bad webhook signature must be 400, got {r.status_code}")
    r = client.post("/api/billing/webhook", data=b'{"event":"payment.captured"}')
    check(r.status_code == 400, f"missing webhook signature must be 400, got {r.status_code}")
    del os.environ["RAZORPAY_WEBHOOK_SECRET"]

print(f"OK — {passed} security checks passed")

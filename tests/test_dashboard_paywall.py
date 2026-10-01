"""
test_dashboard_paywall.py — billing-v2 P6: the dashboard paywall.

DASHBOARD_PAYWALL=1 (wallet scope only) makes projects, forecast, expansion,
reports, store data and connections a trial/paid feature, decided by the plan of
the PAYING company behind the company the data belongs to. The map, saved
locations, scoring and the account pages stay open. Off, nothing changes.

One throwaway sqlite, Flask test client, no network:

    DATABASE_URL="sqlite:///$(mktemp -d)/t.sqlite" python3 tests/test_dashboard_paywall.py

A failure here means paid data reachable without a plan, or a free/paying
customer refused something they should have.
"""

import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "paisamap-etl", "etl"))

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/paywall_default.sqlite")
if "sqlite" not in os.environ["DATABASE_URL"]:
    raise SystemExit("Refusing to run: DATABASE_URL doesn't look like a throwaway sqlite file.")
if not os.environ.get("CUSTOMER_DATA_KEY"):
    from cryptography.fernet import Fernet
    os.environ["CUSTOMER_DATA_KEY"] = Fernet.generate_key().decode()
os.environ.setdefault("INVOICES_DIR", tempfile.mkdtemp(prefix="pm_invoices_"))
for k in ("BILLING_SUBSCRIPTIONS", "BILLING_LIVE_PURCHASES", "BILLING_SCOPE", "DASHBOARD_PAYWALL"):
    os.environ.pop(k, None)

import _db
_db.enabled = lambda: False
import _auth_db as A
from sqlalchemy import text

A.init_schema()
A.migrate_schema()
engine = A._require_engine()

import server

app = server.app
app.testing = True
passed = 0


def check(cond, msg):
    global passed
    assert cond, f"FAIL: {msg}"
    passed += 1


def sql(stmt, **params):
    with engine.begin() as conn:
        return conn.execute(text(stmt), params)


def person(tag):
    u = A.upsert_user(f"pw-{tag}", f"{tag}@example.com", tag, None)
    org = A.create_default_organization_for_user(u["id"], f"{tag} Co")
    return u["id"], org["id"]


def set_plan(org_id, plan):
    sql("UPDATE organizations SET plan=:p WHERE id=:i", p=plan, i=org_id)


def client_as(uid):
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = uid
    return c


def req(uid, method, path, **kw):
    return getattr(client_as(uid), method)(path, **kw)


def me(uid, org_id=None):
    path = "/api/auth/me" + (f"?org_id={org_id}" if org_id else "")
    return client_as(uid).get(path).get_json()["user"]


def is_paywalled(r):
    return r.status_code == 402 and (r.get_json() or {}).get("error") == "dashboard_plan_required"


# ── People and data (made while the paywall is off) ─────────────────────────
fay, org_f = person("fay")                  # free company
pam, org_p = person("pam")                  # paid company (Growth)
set_plan(org_p, "v2_growth")
A.add_org_member(org_p, pam, "fay@example.com", "member")   # fay is also a member of Pam's paid company
tia, org_t = person("tia")                  # on the free trial
set_plan(org_t, "v2_trial")
leo, org_l = person("leo")                  # legacy Pro (grandfathered)
set_plan(org_l, "pro")

proj_f = A.create_project(fay, "Fay free project", org_id=org_f)["id"]
proj_p = A.create_project(pam, "Pam paid project", org_id=org_p)["id"]
proj_fp = A.create_project(fay, "Fay in Pam's company", org_id=org_p)["id"]
proj_t = A.create_project(tia, "Tia trial project", org_id=org_t)["id"]
proj_l = A.create_project(leo, "Leo legacy project", org_id=org_l)["id"]
A.create_saved_location(fay, proj_f, "560038", name="Indiranagar")
up_f = A.create_customer_upload(fay, proj_f, "stores.csv", "csv", ["pincode", "revenue"], [["560038", "100"]])["id"]
_pdf = os.path.join(tempfile.mkdtemp(), "r.pdf")
open(_pdf, "wb").write(b"%PDF-1.4 test")
rep_f = A.create_report(fay, proj_f, "Fay report", status="ready", file_path=_pdf)["id"]


def project_routes(uid, pid):
    """(name, response) for every project-scoped dashboard route."""
    return [
        ("project GET", req(uid, "get", f"/api/projects/{pid}")),
        ("project PUT", req(uid, "put", f"/api/projects/{pid}", json={"name": "renamed"})),
        ("project duplicate", req(uid, "post", f"/api/projects/{pid}/duplicate")),
        ("project share", req(uid, "post", f"/api/projects/{pid}/share")),
        ("forecast", req(uid, "get", f"/api/forecast?project_id={pid}&budget=1000000")),
        ("expansion drivers", req(uid, "get", f"/api/expansion/drivers?project_id={pid}")),
        ("expansion surface", req(uid, "get", f"/api/expansion/surface?project_id={pid}")),
        ("expansion recommend", req(uid, "get", f"/api/expansion/recommend?project_id={pid}&budget=1000000")),
        ("report generate", req(uid, "post", "/api/reports", json={"project_id": pid})),
        ("store-data upload list", req(uid, "get", f"/api/customer-data/uploads?project_id={pid}")),
        ("store-data locations", req(uid, "get", f"/api/customer-data/locations?project_id={pid}")),
        ("connections status", req(uid, "get", f"/api/projects/{pid}/connections")),
        ("connections consent", req(uid, "post", f"/api/projects/{pid}/connections/consent", json={})),
    ]


# ═════════════════════════════════════════════════════════════════════════════
# 1. Off by default: nothing changes for anyone
# ═════════════════════════════════════════════════════════════════════════════
check(A.paywall_enabled() is False, "the paywall is off by default")
check(me(fay)["dashboard"] == {"allowed": True, "paywall": False, "plan": None}, "/me says: no paywall")
for name, r in project_routes(fay, proj_f):
    check(not is_paywalled(r), f"paywall off: free user's {name} is not refused ({r.status_code})")
os.environ["DASHBOARD_PAYWALL"] = "1"
check(A.paywall_enabled() is False, "the switch does nothing outside wallet scope (the plan lives on the company)")
check(not is_paywalled(req(fay, "get", f"/api/projects/{proj_f}")), "…so user scope stays open")
os.environ["BILLING_SCOPE"] = "wallet"
check(A.paywall_enabled() is True, "DASHBOARD_PAYWALL=1 + wallet scope -> on")

# ═════════════════════════════════════════════════════════════════════════════
# 2. On: a free company is refused every dashboard route
# ═════════════════════════════════════════════════════════════════════════════
d = me(fay)["dashboard"]
check(d["paywall"] is True and d["allowed"] is False and d["plan"] == "free", "/me: free company, dashboard locked")
for name, r in project_routes(fay, proj_f):
    check(is_paywalled(r), f"free company: {name} -> 402 dashboard_plan_required (got {r.status_code})")
check(is_paywalled(req(fay, "post", "/api/projects", json={"name": "new"})), "free: can't create a project")
check(sql("SELECT COUNT(*) FROM projects WHERE name='new'").scalar() == 0, "…and none was created")
check(is_paywalled(req(fay, "get", f"/api/customer-data/uploads/{up_f}")), "free: an upload's contents are refused")
check(is_paywalled(req(fay, "post", f"/api/customer-data/uploads/{up_f}/commit", json={})), "free: can't commit an upload")
check(is_paywalled(req(fay, "post", f"/api/customer-data/uploads/{up_f}/retry")), "free: can't retry geocoding")
check(is_paywalled(req(fay, "get", "/api/customer-data/uploads")), "free: the all-projects upload list is refused")
check(is_paywalled(req(fay, "get", f"/api/reports/{rep_f}/download")), "free: a report PDF download is refused")
check(is_paywalled(req(fay, "post", f"/api/reports/{rep_f}/share")), "free: can't share a report")
r = client_as(fay).post("/api/customer-data/uploads", data={"project_id": str(proj_f)},
                         content_type="multipart/form-data")
check(is_paywalled(r), "free: can't upload store data")

# What stays open on a free account.
r = req(fay, "get", "/api/locations")
check(r.status_code == 200, "saved locations list stays open")
r = req(fay, "post", "/api/locations", json={"pincode": "560001", "name": "MG Road", "project_id": proj_f})
check(not is_paywalled(r), f"saving a location stays open ({r.status_code})")
check(req(fay, "get", "/api/intelligence/score?pincode=560038").status_code in (200, 404), "scoring a pincode stays open")
check(req(fay, "get", "/api/projects").status_code == 200, "the project list (names only) stays open")
check(req(fay, "get", "/api/reports").status_code == 200, "the report list (titles only) stays open")
for path in ("/api/credits", "/api/billing/subscriptions", "/api/organizations", "/api/activity"):
    r = req(fay, "get", path)
    check(not is_paywalled(r), f"account page data stays open: {path} ({r.status_code})")
check(not is_paywalled(req(fay, "get", "/api/analytics/oauth/callback?state=x")), "the OAuth callback is never paywalled")

# Taking things away is never paywalled.
check(not is_paywalled(req(fay, "delete", f"/api/projects/{proj_f}/connections/google_analytics")),
      "disconnecting Google stays open")
check(not is_paywalled(req(fay, "delete", f"/api/projects/{proj_f}/share")), "un-sharing a project stays open")
check(not is_paywalled(req(fay, "delete", f"/api/reports/{rep_f}/share")), "un-sharing a report stays open")
check(not is_paywalled(req(fay, "post", f"/api/projects/{proj_f}/archive")), "archiving stays open")
check(not is_paywalled(req(fay, "post", f"/api/projects/{proj_f}/unarchive")), "unarchiving stays open")
check(req(fay, "delete", f"/api/customer-data/uploads/{up_f}").status_code == 200, "deleting store data stays open")

# ═════════════════════════════════════════════════════════════════════════════
# 3. Who is allowed, and on whose plan
# ═════════════════════════════════════════════════════════════════════════════
for uid, pid, who in ((pam, proj_p, "paid Growth"), (tia, proj_t, "trial"), (leo, proj_l, "legacy Pro")):
    check(me(uid)["dashboard"]["allowed"] is True, f"/me: {who} company may use the dashboard")
    for name, r in project_routes(uid, pid):
        check(not is_paywalled(r), f"{who}: {name} allowed ({r.status_code})")
check(req(tia, "post", "/api/projects", json={"name": "Tia second"}).status_code == 201, "trial: can create projects")

# The company the DATA belongs to decides, not the person.
check(not is_paywalled(req(fay, "get", f"/api/projects/{proj_fp}")),
      "fay's project inside Pam's paid company opens (that company pays)")
check(is_paywalled(req(fay, "get", f"/api/projects/{proj_f}")), "…while her own free company's project stays locked")
check(me(fay, org_p)["dashboard"]["allowed"] is True and me(fay, org_f)["dashboard"]["allowed"] is False,
      "/me follows the company picked in the switcher")
r = req(fay, "post", "/api/projects", json={"name": "Into Pam's", "org_id": org_p})
check(r.status_code == 201, "fay can create a project in the paid company she belongs to")

# Naming a paid company you DON'T belong to opens nothing.
zed, org_z = person("zed")
check(is_paywalled(req(zed, "post", "/api/projects", json={"name": "spoof", "org_id": org_p})),
      "a stranger naming someone else's paid company is still refused")
check(me(zed, org_p)["dashboard"]["allowed"] is False, "/me: a company you aren't in is ignored")
check(is_paywalled(req(zed, "get", f"/api/customer-data/uploads?org_id={org_p}")),
      "?org_id= of someone else's paid company doesn't open a list either")
check(not is_paywalled(req(fay, "get", f"/api/customer-data/uploads?org_id={org_p}")),
      "?org_id= of a paid company you DO belong to opens its list")

# A client company linked under a paying agency uses the agency's plan.
cli, org_c = person("client")
A.add_org_member(org_p, pam, "client@example.com", "admin")
check(A.set_org_payer(org_c, cli, org_p).get("status") == "ok", "client company linked under Pam's paid company")
proj_c = A.create_project(cli, "Client project", org_id=org_c)["id"]
check(not is_paywalled(req(cli, "get", f"/api/projects/{proj_c}")), "a linked client company inherits the payer's plan")
A.unlink_company(cli, org_c)
check(is_paywalled(req(cli, "get", f"/api/projects/{proj_c}")), "detached, it is on its own (free) again")

# ═════════════════════════════════════════════════════════════════════════════
# 4. Losing and regaining a plan: nothing is deleted
# ═════════════════════════════════════════════════════════════════════════════
set_plan(org_t, "free")                      # e.g. the trial lapsed
check(is_paywalled(req(tia, "get", f"/api/projects/{proj_t}")), "trial lapsed -> locked")
check(sql("SELECT COUNT(*) FROM projects WHERE id=:i", i=proj_t).scalar() == 1, "…but the project still exists")
set_plan(org_t, "v2_starter")                # subscribed
r = req(tia, "get", f"/api/projects/{proj_t}")
check(r.status_code == 200 and r.get_json()["project"]["id"] == proj_t, "subscribing opens it again, intact")

# Shared links made while paying keep working (they're already in other people's hands).
set_plan(org_p, "v2_growth")
tok = req(pam, "post", f"/api/projects/{proj_p}/share").get_json()["project"]["share_token"]
set_plan(org_p, "free")
check(app.test_client().get(f"/api/projects/shared/{tok}").status_code == 200,
      "a project share link made while paying still opens after the plan ends")
check(is_paywalled(req(pam, "post", f"/api/projects/{proj_p}/share")), "…but no new links can be made")

# Switching it back off restores everything at once.
os.environ.pop("DASHBOARD_PAYWALL")
check(not is_paywalled(req(fay, "get", f"/api/projects/{proj_f}")) and me(fay)["dashboard"]["paywall"] is False,
      "turning the switch off opens everything again")

print(f"OK — {passed} paywall checks passed")

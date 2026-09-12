"""
test_cross_account_isolation.py — Phase H2: an automated cross-account
isolation test suite. Every endpoint that takes a resource id must never let
an unrelated account read, write, or delete another account's data.

Run against a throwaway sqlite DB (never point DATABASE_URL at anything
real):

    DATABASE_URL="sqlite:////tmp/isolation_test.sqlite" \
      python3 tests/test_cross_account_isolation.py

Two isolation models exist in this codebase and both are covered:
  - Org-shared resources (projects, saved_locations, reports, customer
    uploads/locations, oauth connections): any member of the OWNING org has
    read access; write needs member+; delete needs admin/owner. A user with
    no relationship to that org — whether they belong to a different org
    entirely, or belong to none at all — must get a 404 on everything.
  - Strictly personal resources (api_keys, invoices): scoped to the literal
    creating user_id, not the org — a fellow admin/owner in the SAME org
    must be blocked exactly like a total stranger. This is the one place a
    reflexive "just check org membership" refactor would silently break
    isolation the wrong way, so it gets its own explicit checks below.

Each check is a plain assert with a descriptive message — a failure here is
a real isolation bug, not a flaky test, and should never be quieted with a
broader except clause.
"""

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "paisamap-etl", "etl"))

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/isolation_test_default.sqlite")
if "sqlite" not in os.environ["DATABASE_URL"]:
    raise SystemExit("Refusing to run: DATABASE_URL doesn't look like a throwaway sqlite file.")

import _db
_db.init_schema()
_db.bulk_upsert_pincodes([
    {"pincode": "560001", "name": "MG Road", "lat": 12.9758, "lng": 77.6045,
     "ppi_ml": 78, "est_monthly_income_hh": 95000, "est_monthly_spend_hh": 60000},
])

import _auth_db
_auth_db.init_schema()
_auth_db.migrate_schema()

import server
app = server.app
client = app.test_client()

CHECKS = {"passed": 0}


def check(label):
    CHECKS["passed"] += 1
    print(f"  ok  {label}")


def login(user_id):
    with client.session_transaction() as sess:
        sess.clear()
        sess["user_id"] = user_id


def logout():
    with client.session_transaction() as sess:
        sess.clear()


def signup(email, name, with_org=True):
    result = _auth_db.upsert_user(google_sub=f"sub-{email}", email=email, name=name, picture_url=None)
    uid = result["id"]
    if with_org and result["created"]:
        _auth_db.create_default_organization_for_user(uid, f"{name}'s Workspace")
    return uid


def assert_blocked(method, url, as_user, allow_statuses=(403, 404), **kwargs):
    """Logs in as `as_user` (or stays logged out if None) and asserts the
    response is one of `allow_statuses` — never 200/201/204."""
    if as_user is None:
        logout()
    else:
        login(as_user)
    resp = client.open(url, method=method, **kwargs)
    assert resp.status_code in allow_statuses, (
        f"{method} {url} as user={as_user} should be blocked "
        f"({allow_statuses}) but got {resp.status_code}: {resp.get_data(as_text=True)[:300]}"
    )
    return resp


def assert_allowed(method, url, as_user, allow_statuses=(200, 201), **kwargs):
    login(as_user)
    resp = client.open(url, method=method, **kwargs)
    assert resp.status_code in allow_statuses, (
        f"{method} {url} as user={as_user} should succeed "
        f"({allow_statuses}) but got {resp.status_code}: {resp.get_data(as_text=True)[:300]}"
    )
    return resp


# ── Set up two fully independent tenants + a no-org ghost ──────────────────
owner_a = signup("owner-a@iso-test.com", "OwnerA")
admin_a = signup("admin-a@iso-test.com", "AdminA")
member_a = signup("member-a@iso-test.com", "MemberA")
org_a = _auth_db.list_organizations(owner_a)[0]["id"]
_auth_db.add_org_member(org_a, owner_a, "admin-a@iso-test.com", "admin")
_auth_db.add_org_member(org_a, owner_a, "member-a@iso-test.com", "member")

owner_b = signup("owner-b@iso-test.com", "OwnerB")
org_b = _auth_db.list_organizations(owner_b)[0]["id"]

ghost = signup("ghost@iso-test.com", "Ghost", with_org=False)
assert _auth_db.list_organizations(ghost) == [], "ghost must have zero orgs for this to be a real test"

OUTSIDERS = [owner_b, ghost]  # both must be blocked identically everywhere below

_auth_db.grant_credits(owner_a, 200, "test_seed")

# ── Seed Org A's resources ──────────────────────────────────────────────────
project_a = _auth_db.create_project(owner_a, "Org A Project", org_id=org_a)["id"]
location_a = _auth_db.create_saved_location(owner_a, project_a, "560001", name="MG Road")[0]["id"]

login(owner_a)
r = client.post("/api/reports", json={"project_id": project_a, "title": "Org A Report"})
assert r.status_code == 201, f"report seed failed: {r.get_json()}"
report_a = r.get_json()["report"]["id"]

upload_a = _auth_db.create_customer_upload(
    owner_a, project_a, "stores.csv", "csv", ["name", "pincode"], [{"name": "S1", "pincode": "560001"}]
)["id"]
r = client.post(f"/api/customer-data/uploads/{upload_a}/commit",
                 json={"mapping": {"store_name": "name", "pincode": "pincode"}})
assert r.status_code == 200, f"commit seed failed: {r.get_json()}"
customer_locations_a = _auth_db.list_customer_locations(owner_a, project_a)
assert len(customer_locations_a) == 1, "expected the commit to create exactly one customer_location row"
customer_location_a = customer_locations_a[0]["id"]

_auth_db.upsert_oauth_connection(
    project_a, owner_a, "google_analytics", "orga@example.com", "scope",
    "enc-access-token", "enc-refresh-token", None,
)

invite_result = _auth_db.create_or_resend_org_invite(org_a, owner_a, "someone-new@iso-test.com", "member")
invite_a = invite_result["invite"]["id"]

login(owner_a)
r = client.post("/api/developer/keys", json={"label": "Org A key"})
assert r.status_code == 201, r.get_json()
api_key_a = r.get_json()["api_key"]["id"]

order_a = _auth_db.create_order(owner_a, "credit_pack", "razorpay_order_test_1", 10000, credit_pack_id="small")
invoice_a = _auth_db.create_invoice(
    order_a["id"], owner_a, "owner-a@iso-test.com", 10000, 1800, 11800, "Test credit pack"
)["id"]

print(f"\nSeeded: org_a={org_a} project_a={project_a} location_a={location_a} report_a={report_a} "
      f"upload_a={upload_a} customer_location_a={customer_location_a} invite_a={invite_a} "
      f"api_key_a={api_key_a} invoice_a={invoice_a}\n")

# ── Org-shared resources: total outsiders must be blocked entirely ─────────
for outsider in OUTSIDERS:
    tag = f"outsider={outsider}"

    assert_blocked("GET", f"/api/projects/{project_a}", outsider)
    check(f"GET project ({tag})")
    assert_blocked("PUT", f"/api/projects/{project_a}", outsider, json={"name": "hijacked"})
    check(f"PUT project ({tag})")
    assert_blocked("DELETE", f"/api/projects/{project_a}", outsider)
    check(f"DELETE project ({tag})")
    assert_blocked("POST", f"/api/projects/{project_a}/archive", outsider)
    check(f"POST project archive ({tag})")
    assert_blocked("POST", f"/api/projects/{project_a}/duplicate", outsider)
    check(f"POST project duplicate ({tag})")
    assert_blocked("POST", f"/api/projects/{project_a}/share", outsider)
    check(f"POST project share ({tag})")

    resp = assert_allowed("GET", "/api/projects", outsider)
    ids = [p["id"] for p in resp.get_json()["projects"]]
    assert project_a not in ids, f"project list leak to {tag}"
    check(f"GET project list excludes org A ({tag})")

    assert_blocked("PUT", f"/api/locations/{location_a}", outsider, json={"name": "hijacked"})
    check(f"PUT saved_location ({tag})")
    assert_blocked("DELETE", f"/api/locations/{location_a}", outsider)
    check(f"DELETE saved_location ({tag})")

    assert_blocked("GET", f"/api/reports/{report_a}/download", outsider)
    check(f"GET report download ({tag})")
    assert_blocked("POST", f"/api/reports/{report_a}/share", outsider)
    check(f"POST report share ({tag})")
    resp = assert_allowed("GET", "/api/reports", outsider)
    ids = [rr["id"] for rr in resp.get_json()["reports"]]
    assert report_a not in ids, f"report list leak to {tag}"
    check(f"GET report list excludes org A ({tag})")

    assert_blocked("GET", f"/api/customer-data/uploads/{upload_a}", outsider)
    check(f"GET customer upload ({tag})")
    assert_blocked("DELETE", f"/api/customer-data/uploads/{upload_a}", outsider)
    check(f"DELETE customer upload ({tag})")
    assert_blocked("DELETE", f"/api/customer-data/locations/{customer_location_a}", outsider)
    check(f"DELETE customer location ({tag})")

    assert_blocked("GET", f"/api/projects/{project_a}/connections", outsider)
    check(f"GET connections ({tag})")
    assert_blocked("DELETE", f"/api/projects/{project_a}/connections/google_analytics", outsider)
    check(f"DELETE connection ({tag})")
    assert_blocked("POST", f"/api/projects/{project_a}/connections/google_analytics/select",
                   outsider, json={"external_ref": "hijacked"})
    check(f"POST connection select ({tag})")
    assert_blocked("POST", f"/api/projects/{project_a}/connections/consent", outsider, json={"accept": True})
    check(f"POST analytics consent ({tag})")

    assert_blocked("GET", f"/api/organizations/{org_a}", outsider)
    check(f"GET organization ({tag})")
    assert_blocked("GET", f"/api/organizations/{org_a}/members", outsider)
    check(f"GET org members ({tag})")
    assert_blocked("POST", f"/api/organizations/{org_a}/members", outsider, json={"email": "x@x.com"})
    check(f"POST add org member ({tag})")
    assert_blocked("DELETE", f"/api/organizations/{org_a}/members/{member_a}", outsider)
    check(f"DELETE org member ({tag})")
    assert_blocked("GET", f"/api/organizations/{org_a}/invites", outsider)
    check(f"GET org invites ({tag})")
    assert_blocked("DELETE", f"/api/organizations/{org_a}/invites/{invite_a}", outsider)
    check(f"DELETE org invite ({tag})")
    assert_blocked("GET", f"/api/organizations/{org_a}/audit-log", outsider)
    check(f"GET org audit log ({tag})")

# ── Strictly personal resources: even a FELLOW ORG MEMBER must be blocked ──
# (owner_a's own admin/member teammates, not just outsiders — this is the
# one place org-sharing must NOT apply, see module docstring)
for peer in (admin_a, member_a):
    tag = f"org-A-peer={peer}"

    assert_blocked("DELETE", f"/api/developer/keys/{api_key_a}", peer)
    check(f"DELETE teammate's api key ({tag})")
    resp = assert_allowed("GET", "/api/developer/keys", peer)
    ids = [k["id"] for k in resp.get_json()["api_keys"]]
    assert api_key_a not in ids, f"api key list leak to {tag}"
    check(f"GET api key list excludes owner's keys ({tag})")

    assert_blocked("GET", f"/api/billing/invoices/{invoice_a}/download", peer)
    check(f"GET teammate's invoice download ({tag})")
    resp = assert_allowed("GET", "/api/billing/invoices", peer)
    ids = [i["id"] for i in resp.get_json()["invoices"]]
    assert invoice_a not in ids, f"invoice list leak to {tag}"
    check(f"GET invoice list excludes owner's invoices ({tag})")

# Also confirm outsiders (no org relationship at all) are blocked the same way
for outsider in OUTSIDERS:
    assert_blocked("DELETE", f"/api/developer/keys/{api_key_a}", outsider)
    check(f"DELETE api key (outsider={outsider})")
    assert_blocked("GET", f"/api/billing/invoices/{invoice_a}/download", outsider)
    check(f"GET invoice download (outsider={outsider})")

# ── Public share-token routes: unauthenticated, but must reveal nothing more
#    than the deliberately-thin public shape, and must 404 on a bad token ──
login(owner_a)
r = client.post(f"/api/projects/{project_a}/share")
token = r.get_json()["project"]["share_token"]
logout()
r = client.get(f"/api/projects/shared/{token}")
assert r.status_code == 200
body = r.get_json()["project"]
assert "org_id" not in body and "user_id" not in body, "public project view leaked an internal id"
check("public project share view omits internal ids")
r = client.get("/api/projects/shared/not-a-real-token")
assert r.status_code == 404
check("public project share view 404s on a bad token")

login(owner_a)
client.delete(f"/api/projects/{project_a}/share")

# ── Positive controls: a real member of Org A must actually have access ────
# (proves the blocks above are role-based, not a blanket bug)
assert_allowed("GET", f"/api/projects/{project_a}", member_a)
check("positive: member reads org A's project")
assert_allowed("PUT", f"/api/projects/{project_a}", member_a, json={"description": "member edited this"})
check("positive: member edits org A's project")
assert_blocked("DELETE", f"/api/projects/{project_a}", member_a)
check("positive: member is correctly blocked from deleting (D2 policy)")
assert_allowed("DELETE", f"/api/projects/{project_a}", admin_a, allow_statuses=(200,))
check("positive: admin CAN delete org A's project (D2 policy)")

print(f"\nALL {CHECKS['passed']} CROSS-ACCOUNT ISOLATION CHECKS PASSED")

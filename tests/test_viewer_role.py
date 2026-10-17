"""
test_viewer_role.py — P5 round 2: the free, read-only 'viewer' role (pricing decision #10).

A viewer sees a company's projects, locations and reports, but can't create or
change them, spend the company's credits, or hold a company API key; and never
uses a seat. Every write is refused in the data layer (ReadOnlyRole → 403
read_only), so routes can't forget the check.

    python3 tests/test_viewer_role.py

Plain script, throwaway sqlite.
"""

import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "paisamap-etl", "etl"))

os.environ["DATABASE_URL"] = os.environ.get("DATABASE_URL") or \
    "sqlite:///" + os.path.join(tempfile.mkdtemp(prefix="pm_viewer_"), "viewer.sqlite")
if "sqlite" not in os.environ["DATABASE_URL"]:
    raise SystemExit("Refusing to run: DATABASE_URL doesn't look like a throwaway sqlite file.")
if not os.environ.get("CUSTOMER_DATA_KEY"):
    from cryptography.fernet import Fernet
    os.environ["CUSTOMER_DATA_KEY"] = Fernet.generate_key().decode()
os.environ.pop("BILLING_SCOPE", None)
os.environ["SEAT_LIMITS"] = "1"
os.environ.setdefault("SECRET_KEY", "test")

import _db
_db.enabled = lambda: False
import _auth_db as A
import _seats as S
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


def person(tag, company=True):
    u = A.upsert_user(f"sub-{tag}", f"{tag}@example.com", tag, None)
    if not company:
        return u["id"], None
    return u["id"], A.create_default_organization_for_user(u["id"], f"{tag} Co")["id"]


def refused(fn, *a, **k):
    try:
        fn(*a, **k)
    except A.ReadOnlyRole:
        return True
    return False


def err(r):
    return r.get("error") if isinstance(r, dict) else None


own, co = person("owner")
sql("UPDATE organizations SET plan = 'v2_starter' WHERE id = :o", o=co)          # 3 seats
m1, _ = person("m1", company=False)
m2, _ = person("m2", company=False)
check(err(A.add_org_member(co, own, "m1@example.com")) is None, "member 2")
check(err(A.add_org_member(co, own, "m2@example.com")) is None, "member 3 — account full")
vw, vw_own = person("viewer")                                                      # has their own company too

# ── a viewer is free: joins a full account, uses no seat ─────────────────────
check(err(A.add_org_member(co, own, "viewer@example.com", role="viewer")) is None, "viewer joins a full account")
check(A.get_org_role(co, vw) == "viewer", "stored as viewer")
check(S.view_for(co)["used_seats"] == 3, "viewer uses no seat")
inv = A.create_or_resend_org_invite(co, own, "v2@example.com", role="viewer")
check(err(inv) is None and inv["invite"]["role"] == "viewer", "viewer invite at a full account")
check(S.view_for(co)["used_seats"] == 3, "pending viewer invite holds no seat")
check(err(A.create_or_resend_org_invite(co, own, "m3@example.com")) == "seat_limit", "member invite still refused")
v2 = A.upsert_user("sub-v2", "v2@example.com", "v2", None)["id"]
tok = sql("SELECT token FROM org_invites WHERE email = 'v2@example.com'").scalar()
check(err(A.accept_org_invite(tok, v2)) is None and A.get_org_role(co, v2) == "viewer", "accepted as a viewer")

# ── reads work ───────────────────────────────────────────────────────────────
proj = A.create_project(own, "Expansion", org_id=co)
loc, _ = A.create_saved_location(own, proj["id"], "560038", name="Indiranagar")
rep = A.create_report(own, proj["id"], "Report")
got = A.get_project(proj["id"], vw)
check(got is not None and got["role"] == "viewer", "viewer reads the project (role 'viewer')")
check(any(l["id"] == loc["id"] for l in A.list_saved_locations(vw, proj["id"])), "viewer reads its locations")
check(A.get_report(rep["id"], vw) is not None, "viewer reads its report")
check(len(A.list_org_members(co, vw) or []) == 5, "viewer sees the member list")

# ── every write is refused ───────────────────────────────────────────────────
check(refused(A.update_project, proj["id"], vw, name="X"), "update project")
check(refused(A.archive_project, proj["id"], vw), "archive project")
check(refused(A.create_project, vw, "Mine", org_id=co), "create a project in the company")
check(refused(A.duplicate_project, proj["id"], vw), "duplicate a project into the company")
check(refused(A.create_saved_location, vw, proj["id"], "110003"), "save a location")
check(refused(A.update_saved_location, loc["id"], vw, notes="x"), "edit a location")
check(not A.delete_saved_location(loc["id"], vw), "delete a location (owner/admin only)")
check(not A.delete_project(proj["id"], vw), "delete the project (owner/admin only)")
check(refused(A.create_report, vw, proj["id"], "Mine"), "create a report")
check(refused(A.set_report_share_token, rep["id"], vw, "tok"), "share a report")
check(refused(A.create_customer_upload, vw, proj["id"], "f.csv", "csv", ["a"], [["1"]]), "upload store data")
up = A.create_customer_upload(own, proj["id"], "o.csv", "csv", ["a"], [["1"]])
check(refused(A.update_customer_upload, up["id"], vw, error="tampered"), "change an upload")
check(refused(A.create_customer_locations_bulk, vw, proj["id"], 1, [{"geocode_status": "pending"}]), "bulk store rows")
check(refused(A.upsert_oauth_connection, proj["id"], vw, "ga4", "x@example.com", "s", "a", "r", None),
      "connect analytics")
check(refused(A.select_org_connection_for_project, proj["id"], vw, "ga4", 1), "use a teammate's connection")
check(A.resolve_api_key_company(vw, co) == (None, {"error": "read_only"}), "company API key")
check(refused(A.spend_credits, vw, 5, "forecast", org_id=co), "spend the company's credits")
check(A.get_project(proj["id"], own)["name"] == "Expansion", "nothing changed")

# A member still can.
check(A.update_project(proj["id"], m1, name="Renamed")["name"] == "Renamed", "a member can edit")

# ── their OWN company is unaffected ──────────────────────────────────────────
mine = A.create_project(vw, "My own", org_id=vw_own)
check(mine is not None and mine["role"] == "owner", "a viewer elsewhere is owner of their own company")
check(not refused(A.create_saved_location, vw, mine["id"], "110003"), "…and can save there")
check(A.resolve_api_key_company(vw, vw_own)[1] is None, "…and hold an API key there")

# ── promotion needs a seat; demotion frees one ───────────────────────────────
check(err(A.update_org_member_role(co, own, vw, "member")) == "seat_limit", "viewer → member at a full account")
check(err(A.update_org_member_role(co, own, m2, "viewer")) is None, "member → viewer")
check(S.view_for(co)["used_seats"] == 2, "…frees a seat")
check(err(A.update_org_member_role(co, own, vw, "member")) is None, "now viewer → member fits")
check(A.update_project(proj["id"], vw, name="By ex-viewer") is not None, "and can edit")

# ── HTTP: 403 read_only with a plain message ─────────────────────────────────
import server  # noqa: E402
c = server.app.test_client()
with c.session_transaction() as s:
    s["user_id"] = v2
r = c.put(f"/api/projects/{proj['id']}", json={"name": "Nope"})
check(r.status_code == 403 and r.get_json()["error"] == "read_only" and "viewer" in r.get_json()["message"].lower(),
      f"PUT project → 403 read_only ({r.status_code})")
r = c.post("/api/locations", json={"pincode": "400006", "project_id": proj["id"]})
check(r.status_code == 403 and r.get_json()["error"] == "read_only", f"POST location → 403 ({r.status_code})")
r = c.post("/api/developer/keys", json={"org_id": co})
check(r.status_code == 403 and r.get_json()["error"] == "read_only", f"POST api key → 403 ({r.status_code})")
r = c.get(f"/api/projects/{proj['id']}")
check(r.status_code in (200, 402), f"GET project is not refused as read_only ({r.status_code})")

print(f"test_viewer_role: {passed} checks passed")

"""
test_seat_limits.py — P5: seat and company limits per paying account.

Owner decisions (15 Oct 2026): seats are counted across the whole paying account
(a person in two of its companies is one seat); a pending invite holds a seat;
viewers are free. Limits = the tier's seats/companies + bought extras
(`wallet_addons`). Nobody is ever removed: an account over its limit only stops
adding. Enforced only with SEAT_LIMITS=1 and only for v2 dashboard tiers.

    DATABASE_URL="sqlite:////tmp/seat_limits.sqlite" python3 tests/test_seat_limits.py

Plain script, throwaway sqlite.
"""

import os
import sys
import tempfile
from datetime import timedelta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "paisamap-etl", "etl"))

os.environ["DATABASE_URL"] = os.environ.get("DATABASE_URL") or \
    "sqlite:///" + os.path.join(tempfile.mkdtemp(prefix="pm_seats_"), "seats.sqlite")
if "sqlite" not in os.environ["DATABASE_URL"]:
    raise SystemExit("Refusing to run: DATABASE_URL doesn't look like a throwaway sqlite file.")
if not os.environ.get("CUSTOMER_DATA_KEY"):
    from cryptography.fernet import Fernet
    os.environ["CUSTOMER_DATA_KEY"] = Fernet.generate_key().decode()
os.environ.pop("BILLING_SCOPE", None)
os.environ.pop("SEAT_LIMITS", None)

import _db
_db.enabled = lambda: False
import _auth_db as A
import _seats as S
from sqlalchemy import text

A.init_schema()
A.migrate_schema()
engine = A._require_engine()
A._dispatch_link_notices = lambda n: None
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
    org = A.create_default_organization_for_user(u["id"], f"{tag} Co")
    return u["id"], org["id"]


def plan(org_id, p):
    sql("UPDATE organizations SET plan = :p WHERE id = :o", p=p, o=org_id)


def extras(org_id, seats=0, companies=0):
    sql("DELETE FROM wallet_addons WHERE wallet_org_id = :o", o=org_id)
    sql("INSERT INTO wallet_addons (wallet_org_id, extra_seats, extra_companies, updated_at) "
        "VALUES (:o, :s, :c, CURRENT_TIMESTAMP)", o=org_id, s=seats, c=companies)


def view(org_id):
    return S.view_for(org_id)


def link(org_id, payer_id):
    """The shared linking rule (direct link and an approved link request both use it)."""
    with engine.begin() as conn:
        return A._apply_payer(conn, org_id, payer_id) or {}


def err(result):
    return result.get("error") if isinstance(result, dict) else None


# ── off by default: nothing changes until SEAT_LIMITS=1 ─────────────────────
own, co = person("owner")
plan(co, "v2_starter")                                  # 3 seats, 1 company
people = [person(f"p{i}", company=False)[0] for i in range(6)]
for i in range(4):
    check(err(A.add_org_member(co, own, f"p{i}@example.com")) is None, f"flag off: add p{i} (4 people on a 3-seat tier)")
check(view(co) is None, "flag off: no limits view")
for i in range(4):
    A.remove_org_member(co, own, people[i])

os.environ["SEAT_LIMITS"] = "1"

# ── seats: Starter = 3 (owner + 2) ───────────────────────────────────────────
v = view(co)
check(v["tier"] == "starter" and v["seats"] == 3 and v["companies"] == 1, f"Starter limits {v}")
check(v["used_seats"] == 1 and v["used_companies"] == 1, "owner holds 1 seat, 1 company")
check(err(A.add_org_member(co, own, "p0@example.com")) is None, "seat 2")
check(err(A.add_org_member(co, own, "p1@example.com")) is None, "seat 3")
r = A.add_org_member(co, own, "p2@example.com")
check(err(r) == "seat_limit" and r["limit"] == 3 and r["used"] == 3 and r["extra_paise"] == 90_000,
      f"4th person refused with the price of an extra seat: {r}")

# A pending invite holds a seat; resending it doesn't take a second one.
A.remove_org_member(co, own, people[1])
inv = A.create_or_resend_org_invite(co, own, "newbie@example.com")
check(err(inv) is None, "invite takes seat 3")
check(view(co)["used_seats"] == 3 and view(co)["used_pending_invites"] == 1, "pending invite counted")
check(err(A.create_or_resend_org_invite(co, own, "newbie@example.com")) is None, "resend: same seat, allowed")
check(err(A.add_org_member(co, own, "p1@example.com")) == "seat_limit", "invite's seat isn't free for someone else")
check(err(A.create_or_resend_org_invite(co, own, "other@example.com")) == "seat_limit", "a second invite is refused")

# An expired invite frees its seat.
sql("UPDATE org_invites SET expires_at = :e WHERE email = 'newbie@example.com'", e=A._now() - timedelta(days=1))
check(view(co)["used_seats"] == 2, "expired invite no longer counted")
check(err(A.add_org_member(co, own, "p1@example.com")) is None, "so its seat can be used")
A.remove_org_member(co, own, people[1])
sql("UPDATE org_invites SET expires_at = :e WHERE email = 'newbie@example.com'", e=A._now() + timedelta(days=3))

# Accepting converts the invite's seat rather than taking another.
newbie = A.upsert_user("sub-newbie", "newbie@example.com", "newbie", None)["id"]
token = sql("SELECT token FROM org_invites WHERE email = 'newbie@example.com' AND status = 'pending'").scalar()
check(err(A.accept_org_invite(token, newbie)) is None, "invitee accepts at a full account (their seat)")
check(view(co)["used_seats"] == 3 and view(co)["used_pending_invites"] == 0, "3 members, no invites")

# Revoking an invite frees the seat.
A.remove_org_member(co, own, newbie)
inv2 = A.create_or_resend_org_invite(co, own, "late@example.com")
check(view(co)["used_seats"] == 3, "invite holds the seat")
A.revoke_org_invite(co, own, inv2["invite"]["id"])
check(view(co)["used_seats"] == 2, "revoked invite frees it")

# A downgrade after the invite: accepting is refused, the invite stays pending.
inv3 = A.create_or_resend_org_invite(co, own, "down@example.com")
check(err(inv3) is None, "invite (seat 3)")
sql("INSERT INTO org_members (org_id, user_id, role, created_at) VALUES (:o, :u, 'member', CURRENT_TIMESTAMP)",
    o=co, u=people[3])                                   # someone added outside the app → over limit
down = A.upsert_user("sub-down", "down@example.com", "down", None)["id"]
tok3 = sql("SELECT token FROM org_invites WHERE email = 'down@example.com'").scalar()
check(err(A.accept_org_invite(tok3, down)) == "seat_limit", "over the limit: accepting is refused")
check(sql("SELECT status FROM org_invites WHERE email = 'down@example.com'").scalar() == "pending",
      "…and the invite stays pending (nothing lost)")
check(sql("SELECT count(*) FROM org_members WHERE org_id = :o", o=co).scalar() == 3 and view(co)["used_seats"] == 4,
      "nobody is removed when an account is over its limit (3 members + 1 invite on 3 seats)")
sql("DELETE FROM org_members WHERE org_id = :o AND user_id = :u", o=co, u=people[3])
A.revoke_org_invite(co, own, sql("SELECT id FROM org_invites WHERE email = 'down@example.com'").scalar())

# Bought extras raise the limit.
extras(co, seats=2)
v = view(co)
check(v["seats"] == 5 and v["extra_seats"] == 2, f"3 + 2 extra = 5 {v}")
check(err(A.add_org_member(co, own, "p1@example.com")) is None and err(A.add_org_member(co, own, "p2@example.com")) is None,
      "two more fit after buying 2 seats")
check(err(A.add_org_member(co, own, "p4@example.com")) is None, "the 5th fits")
check(err(A.add_org_member(co, own, "p5@example.com")) == "seat_limit", "and the 6th is refused")

# ── companies: Starter = 1 ───────────────────────────────────────────────────
r = A.create_organization(own, "Second Co")
check(err(r) == "company_limit" and r["limit"] == 1 and r["extra_paise"] == 250_000, f"2nd company refused {r}")
extras(co, seats=2, companies=1)
second = A.create_organization(own, "Second Co")
check(err(second) is None and second["id"], "after buying one, the 2nd company is created under the account")
check(view(co)["used_companies"] == 2, "2 companies counted")

# A person in two companies of the account is ONE seat.
check(err(A.add_org_member(second["id"], own, "p0@example.com")) is None, "p0 joins the second company too")
check(view(co)["used_seats"] == 5, "…still 5 seats (counted once across the account)")
check(err(A.add_org_member(second["id"], own, "p5@example.com")) == "seat_limit",
      "a new person in the second company still counts against the account")

# ── linking a client company brings its people into the payer's count ───────
ag_own, agency = person("agency")
plan(agency, "v2_scale")                                # 12 seats, 3 companies
cl_own, client = person("client")
for i in range(3):
    sql("INSERT INTO org_members (org_id, user_id, role, created_at) VALUES (:o, :u, 'member', CURRENT_TIMESTAMP)",
        o=client, u=person(f"cl{i}", company=False)[0])
check(err(link(client, agency)) is None, "agency links the client (4 people) — fits")
check(view(agency)["used_seats"] == 5 and view(agency)["used_companies"] == 2, "agency: 1 + 4 seats, 2 companies")
check(view(client)["tier"] == "scale", "the client reads its payer's limits")
big_own, big = person("big")
for i in range(8):
    sql("INSERT INTO org_members (org_id, user_id, role, created_at) VALUES (:o, :u, 'member', CURRENT_TIMESTAMP)",
        o=big, u=person(f"big{i}", company=False)[0])
r = link(big, agency)
check(err(r) == "seat_limit" and r["would_use"] == 14, f"linking 9 more people would make 14 > 12: {r}")
check(sql("SELECT billing_org_id FROM organizations WHERE id = :o", o=big).scalar() is None, "…and nothing was linked")
small_own, small = person("small")
check(err(link(small, agency)) is None, "a 1-person company fits (3rd company)")
tiny_own, tiny = person("tiny")
check(err(link(tiny, agency)) == "company_limit", "4th company refused")

# ── not limited: free, legacy, signal plans; unlimited Enterprise ────────────
f_own, free_co = person("freeco")
for i in range(5):
    check(err(A.add_org_member(free_co, f_own, f"p{i}@example.com")) is None, f"free plan: not limited ({i})")
check(view(free_co) is None, "free: no limits view")
plan(free_co, "pro")
check(view(free_co) is None, "legacy pro: not limited here")
plan(free_co, "v2_signals_pro")
check(view(free_co) is None, "signal plan: not a dashboard tier, not limited")
plan(free_co, "v2_enterprise")
v = view(free_co)
check(v["seats"] is None and v["companies"] is None, "Enterprise: unlimited")
check(err(A.create_organization(f_own, "E2")) is None, "Enterprise: extra company fine")

# Trial: 2 seats, 1 company.
t_own, trial_co = person("trialco")
plan(trial_co, "v2_trial")
check(err(A.add_org_member(trial_co, t_own, "p0@example.com")) is None, "trial seat 2")
check(err(A.add_org_member(trial_co, t_own, "p1@example.com")) == "seat_limit", "trial seat 3 refused")

# ── the table may not exist yet (deploy before the schema step) ──────────────
sql("DROP TABLE wallet_addons")
v = view(co)
check(v["extra_seats"] == 0 and v["seats"] == 3, "no wallet_addons table → no extras, no crash")
check(err(A.add_org_member(co, own, "p5@example.com")) == "seat_limit", "…limits still apply")
A._get_tables()["wallet_addons"].create(engine)

# ── HTTP: 402 with the details the UI needs ──────────────────────────────────
os.environ.setdefault("SECRET_KEY", "test")
import server  # noqa: E402
client_http = server.app.test_client()
with client_http.session_transaction() as s:
    s["user_id"] = own
r = client_http.post(f"/api/organizations/{co}/members", json={"email": "p5@example.com"})
check(r.status_code == 402 and r.get_json()["error"] == "seat_limit", f"POST member → 402 {r.status_code}")
r = client_http.post("/api/organizations", json={"name": "Third Co"})
check(r.status_code == 402 and r.get_json()["error"] == "company_limit", f"POST company → 402 {r.status_code}")

print(f"test_seat_limits: {passed} checks passed")

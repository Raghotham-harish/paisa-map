"""
test_billing_trial.py — billing-v2 P4: the no-card 7-day free trial.

No card and no Razorpay: starting it gives the PAYING company the 'trial' plan
(the dashboard) + TRIAL_CREDITS that expire with it. Subscribing at any time
outranks it. At the end: still on 'trial' -> back to Free; on a paid plan ->
'converted'. One trial per company and per person, ever (UNIQUE constraints).

Two layers, one throwaway sqlite, no network:
  1. _trial.py: eligibility, start (incl. the UNIQUE race), credits + expiry,
     the sweep (reminder once, lapse to Free once, conversion, never clobbering a
     plan someone else set, healing missed credits).
  2. HTTP: POST /api/billing/trial (dark gate, owner/admin only, refusals),
     GET /subscriptions trial info, subscribing during a trial, the
     authentication-only payment guard on /subscriptions/verify.

    DATABASE_URL="sqlite:///$(mktemp -d)/t.sqlite" python3 tests/test_billing_trial.py

A failure here is a real regression in a money/access path.
"""

import hashlib
import hmac
import os
import sys
import tempfile
from datetime import timedelta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "paisamap-etl", "etl"))

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/billing_trial_default.sqlite")
if "sqlite" not in os.environ["DATABASE_URL"]:
    raise SystemExit("Refusing to run: DATABASE_URL doesn't look like a throwaway sqlite file.")
if not os.environ.get("CUSTOMER_DATA_KEY"):
    from cryptography.fernet import Fernet
    os.environ["CUSTOMER_DATA_KEY"] = Fernet.generate_key().decode()
os.environ.setdefault("INVOICES_DIR", tempfile.mkdtemp(prefix="pm_invoices_"))
for k in ("BILLING_SUBSCRIPTIONS", "BILLING_LIVE_PURCHASES", "BILLING_SCOPE", "DASHBOARD_PAYWALL"):
    os.environ.pop(k, None)
os.environ["RAZORPAY_KEY_ID"] = "rzp_test_trial"
os.environ["RAZORPAY_KEY_SECRET"] = "trial_secret"
os.environ["RAZORPAY_WEBHOOK_SECRET"] = "whsec_trial"
os.environ["BILLING_SCOPE"] = "wallet"

import _db
_db.enabled = lambda: False
import _auth_db as A
import _pricing
import _subscriptions as S
import _trial as T
import _email
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
import razorpay

A.init_schema()
A.migrate_schema()
engine = A._require_engine()

import server
from blueprints import billing as B

app = server.app
app.testing = True
passed = 0

sent = []
_email.send_trial_notice = lambda to, org, kind, ends, amount, url: sent.append((to, kind)) or True


def check(cond, msg):
    global passed
    assert cond, f"FAIL: {msg}"
    passed += 1


def sql(stmt, **params):
    with engine.begin() as conn:
        return conn.execute(text(stmt), params)


def person(tag):
    u = A.upsert_user(f"trial-{tag}", f"{tag}@example.com", tag, None)
    org = A.create_default_organization_for_user(u["id"], f"{tag} Co")
    return u["id"], org["id"]


def plan_of(org_id):
    return sql("SELECT plan FROM organizations WHERE id=:i", i=org_id).scalar()


def balance(uid, org_id):
    return A.get_credit_balance(uid, org_id=org_id)


def ends_of(org_id):
    return S._aware(T.for_org(org_id)["ends_at"])


def mine(notices, org_name):
    return [n for n in notices if n["org_name"] == org_name]


# ═════════════════════════════════════════════════════════════════════════════
# 1. _trial.py
# ═════════════════════════════════════════════════════════════════════════════
check(_pricing.TRIAL_DAYS == 7 and _pricing.TRIAL_CREDITS == 500, "trial = 7 days, 500 credits (decision #9)")
check(_pricing.is_dashboard_tier(_pricing.plan_id_v2("trial")), "the trial plan includes the dashboard")
check(_pricing.entitlements(_pricing.plan_id_v2("trial")) == {"pro_columns": True, "api_elevated": False},
      "trial: all signals, no raised API limit")
check(S.tables_ready() is True, "the trials table is part of the subscription tables")

alice, org_a = person("alice")
check(T.unavailable(org_a, alice) is None, "a fresh company may start a trial")
check(T.for_org(org_a) is None and T.public_view(None) is None, "no trial yet")
g_user, g_org = person("hasplan")
sql("UPDATE organizations SET plan='v2_growth' WHERE id=:i", i=g_org)
check(T.unavailable(g_org, g_user) == "has_plan", "a company already on a dashboard plan can't")
check(T.start(g_org, g_user) == {"error": "has_plan"} and plan_of(g_org) == "v2_growth",
      "…and start() refuses it without touching the plan")
l_user, l_org = person("legacy")
sql("UPDATE organizations SET plan='pro' WHERE id=:i", i=l_org)
check(T.unavailable(l_org, l_user) == "has_plan", "a legacy Pro company counts as having a plan")

# Start.
row = T.start(org_a, alice)["trial"]
check(plan_of(org_a) == "v2_trial", "starting gives the company the trial plan — no card, no Razorpay")
check(balance(alice, org_a) == 500, "…and 500 trial credits")
check(S._aware(row["ends_at"]) - S._aware(row["started_at"]) == timedelta(days=7), "it runs exactly 7 days")
lot = sql("SELECT amount, source_ref FROM credit_lots WHERE billing_org_id=:o", o=org_a).first()
check(lot.amount == 500 and lot.source_ref == f"trial:{row['id']}", "the credits are one lot keyed to the trial")
check(sql("SELECT COUNT(*) FROM orders WHERE org_id=:o", o=org_a).scalar() == 0, "no order, no invoice: nothing was sold")
view = T.public_view(T.for_org(org_a))
check(view["state"] == "running" and view["outcome"] is None and view["credits"] == 500, "the view: running")

# One per company, one per person — in code and in the database.
check(T.start(org_a, alice) == {"error": "trial_used"} and balance(alice, org_a) == 500, "a second start does nothing")
bob, org_b = person("bob")
A.add_org_member(org_a, alice, "bob@example.com", "admin")
check(T.unavailable(org_a, bob) == "trial_used", "one per company, whoever asks")
z_user, z_org = person("zoya")
check(T.unavailable(z_org, alice) == "trial_used", "one per person: another company doesn't reset it")
check(T.start(z_org, alice) == {"error": "trial_used"} and plan_of(z_org) == "free", "…start() refuses it too")
for vals, what in (({"org_id": org_a, "user_id": None}, "company"), ({"org_id": z_org, "user_id": alice}, "person")):
    try:
        with engine.begin() as conn:
            conn.execute(A._get_tables()["trials"].insert().values(
                started_at=S._now(), ends_at=S._now() + timedelta(days=7), **vals))
        check(False, f"the database must refuse a second trial for the same {what}")
    except IntegrityError:
        check(True, f"the database itself refuses a second trial for the same {what}")
# The race: two starts that both pass the code check — the UNIQUE constraint decides.
r_user, r_org = person("racer")
_real_used, _real_plan = T._used, T._plan_of
T._used = lambda conn, o, u: False          # both racers pass every code check…
T._plan_of = lambda conn, o: "free"
check("trial" in T.start(r_org, r_user), "first racer wins")
check(T.start(r_org, r_user) == {"error": "trial_used"}, "the second hits the constraint and gets trial_used, not a 500")
T._used, T._plan_of = _real_used, _real_plan
check(balance(r_user, r_org) == 500, "…and only one set of credits went out")

# A company that has paid before can't take a trial.
p_user, p_org = person("paidbefore")
S.create_local(p_org, p_user, "growth", "monthly", "sub_PAID", "plan_PAID", 1_200_000)
S.apply_charge("sub_PAID", "pay_PAID", 1_200_000, current_end=S._now() + timedelta(days=30), paid_count=1)
S.record_state("sub_PAID", status="cancelled", ends_at=S._now())
S.sweep_ended()
check(plan_of(p_org) == "free" and T.unavailable(p_org, p_user) == "trial_used",
      "a company that has paid before can't take a trial after cancelling")

# ── The sweep ────────────────────────────────────────────────────────────────
sent.clear()
ends_a = ends_of(org_a)
check(mine(T.sweep(now=ends_a - timedelta(hours=30)), "alice Co") == [] and sent == [], "nothing to do mid-trial")
n = mine(T.sweep(now=ends_a - timedelta(hours=23)), "alice Co")
check([x["kind"] for x in n] == ["trial_ending"], "a day before the end: one 'subscribe to keep access' notice")
check(sorted(n[0]["to"]) == ["alice@example.com", "bob@example.com"], "…to the company's owner and admins")
check(mine(T.sweep(now=ends_a - timedelta(hours=22)), "alice Co") == [], "…and only once")
check(plan_of(org_a) == "v2_trial", "mid-trial the plan stays")
n = mine(T.sweep(now=ends_a + timedelta(seconds=1)), "alice Co")
check(plan_of(org_a) == "free" and [x["kind"] for x in n] == ["trial_lapsed"],
      "at the end, still on the trial -> back to Free with one notice")
check(T.for_org(org_a)["outcome"] == "lapsed" and T.public_view(T.for_org(org_a))["state"] == "ended",
      "recorded as lapsed")
check(mine(T.sweep(now=ends_a + timedelta(hours=1)), "alice Co") == [], "…only once")
check(A.expire_due_credits(now=ends_a + timedelta(minutes=1)) >= 0 and balance(alice, org_a) == 0,
      "the trial credits expire with it")
check(A.account_standing(alice, org_a) is None, "a lapsed trial is never 'past due' or locked")

# Conversion: subscribing during the trial outranks it; at the end nothing is undone.
c_user, c_org = person("conv")
T.start(c_org, c_user)
S.create_local(c_org, c_user, "starter", "monthly", "sub_C", "plan_C", 500_000)
S.apply_charge("sub_C", "pay_C", 500_000, current_end=S._now() + timedelta(days=30), paid_count=1)
check(plan_of(c_org) == "v2_starter", "subscribing during the trial takes over at once")
check(balance(c_user, c_org) == 1500, "…the plan's 1,000 credits arrive on top of the trial's 500")
ends_c = ends_of(c_org)
check(mine(T.sweep(now=ends_c - timedelta(hours=23)), "conv Co") == [],
      "no 'trial ends' nag for a company that already subscribed")
check(mine(T.sweep(now=ends_c + timedelta(seconds=1)), "conv Co") == [] and plan_of(c_org) == "v2_starter",
      "at the end a subscribed company keeps its plan, no email")
check(T.for_org(c_org)["outcome"] == "converted", "recorded as converted")

# A plan set by hand during the trial is never clobbered.
e_user, e_org = person("erin")
T.start(e_org, e_user)
sql("UPDATE organizations SET plan='v2_scale' WHERE id=:i", i=e_org)
T.sweep(now=ends_of(e_org) + timedelta(seconds=1))
check(plan_of(e_org) == "v2_scale" and T.for_org(e_org)["outcome"] == "converted",
      "the end only takes back a plan that is still exactly 'trial'")

# Crash between "trial started" and "credits granted" heals on the next sweep.
h_user, h_org = person("heal")
_real_grant = T._grant_credits
T._grant_credits = lambda row: None
T.start(h_org, h_user)
T._grant_credits = _real_grant
check(plan_of(h_org) == "v2_trial" and balance(h_user, h_org) == 0, "(simulated crash: trial on, no credits)")
T.sweep()
check(balance(h_user, h_org) == 500, "the next sweep grants the missing credits")
T.sweep()
check(balance(h_user, h_org) == 500, "…once")

# ═════════════════════════════════════════════════════════════════════════════
# 2. HTTP
# ═════════════════════════════════════════════════════════════════════════════
class _Plan:
    def create(self, d):
        return {"id": "plan_rp_k"}


class _Sub:
    def create(self, d):
        return {"id": "sub_rp_k"}

    def fetch(self, sid):
        return FakeRP.sub_entities[sid]


class _Pay:
    def fetch(self, pid):
        return FakeRP.payments[pid]


class FakeRP:
    payments = {}
    sub_entities = {}
    plan = _Plan()
    subscription = _Sub()
    payment = _Pay()
    utility = razorpay.Client(auth=("rzp_test_trial", "trial_secret")).utility


B._client = lambda: FakeRP()


def client_as(uid):
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = uid
    return c


def post(uid, path, body):
    return client_as(uid).post(f"/api/billing{path}", json=body)


def get_subs(uid, org_id):
    return client_as(uid).get(f"/api/billing/subscriptions?org_id={org_id}").get_json()


def sign(sid, pid):
    return hmac.new(b"trial_secret", f"{pid}|{sid}".encode(), hashlib.sha256).hexdigest()


k_user, k_org = person("kiran")
m_user, _ = person("meera")
A.add_org_member(k_org, k_user, "meera@example.com", "member")

r = post(k_user, "/trial", {})
check(r.status_code == 403 and r.get_json()["error"] == "subscriptions_unavailable" and plan_of(k_org) == "free",
      "dark by default: no trial until subscriptions are switched on")
os.environ["BILLING_SUBSCRIPTIONS"] = "1"
j = get_subs(k_user, k_org)
check(j["trial"] == {"available": True, "reason": None, "days": 7, "credits": 500} and j["current_trial"] is None,
      "GET: a fresh company may start the 7-day, 500-credit trial")
check(post(m_user, "/trial", {"org_id": k_org}).status_code == 403 and plan_of(k_org) == "free",
      "a plain member can't start the company's trial")
check(app.test_client().post("/api/billing/trial", json={}).status_code in (401, 403), "signed out: refused")
r = post(k_user, "/trial", {})
check(r.status_code == 201 and r.get_json()["trial"]["state"] == "running", "the owner starts it — no checkout")
check(plan_of(k_org) == "v2_trial" and balance(k_user, k_org) == 500, "trial plan + 500 credits")
j = get_subs(k_user, k_org)
check(j["trial"]["available"] is False and j["trial"]["reason"] == "trial_used"
      and j["current_trial"]["state"] == "running", "GET: trial used, current one running")
r = post(k_user, "/trial", {})
check(r.status_code == 409 and r.get_json() == {"error": "trial_not_available", "reason": "trial_used"},
      "a second start: 409 trial_used")
check(sql("SELECT COUNT(*) FROM activity_log WHERE action='trial_start' AND user_id=:u", u=k_user).scalar() == 1,
      "the start is in the activity log, once")

# Subscribing during the trial is allowed (the trial ranks below every paid tier).
r = post(k_user, "/subscriptions", {"tier": "starter"})
check(r.status_code == 201, "a company on its trial can subscribe straight away")

# The verify guard: Razorpay saying nothing is paid yet means the payment only
# authenticated the mandate — it is never recorded as a charge.
FakeRP.payments["pay_auth"] = {"id": "pay_auth", "status": "captured", "amount": 500}
FakeRP.sub_entities["sub_rp_k"] = {"id": "sub_rp_k", "status": "authenticated", "paid_count": 0}
r = post(k_user, "/subscriptions/verify", {"razorpay_subscription_id": "sub_rp_k", "razorpay_payment_id": "pay_auth",
                                            "razorpay_signature": sign("sub_rp_k", "pay_auth")})
check(r.get_json()["status"] == "pending" and plan_of(k_org) == "v2_trial", "an authentication-only payment grants nothing")
check(sql("SELECT COUNT(*) FROM orders WHERE razorpay_order_id='sub_charge_pay_auth'").scalar() == 0,
      "…and records no paid order")
FakeRP.payments["pay_real"] = {"id": "pay_real", "status": "captured", "amount": 500_000}
FakeRP.sub_entities["sub_rp_k"] = {"id": "sub_rp_k", "status": "active", "paid_count": 1,
                                   "current_start": 1_800_000_000, "current_end": 1_802_592_000}
r = post(k_user, "/subscriptions/verify", {"razorpay_subscription_id": "sub_rp_k", "razorpay_payment_id": "pay_real",
                                            "razorpay_signature": sign("sub_rp_k", "pay_real")})
check(r.get_json()["status"] == "active" and plan_of(k_org) == "v2_starter", "the real charge converts to Starter")
check(get_subs(k_user, k_org)["current_trial"]["state"] == "running",
      "the trial row still shows until its end (then the sweep marks it converted)")

# A company that already has a plan isn't offered a trial.
j = get_subs(g_user, g_org)
check(j["trial"]["available"] is False and j["trial"]["reason"] == "has_plan", "GET: on a plan -> no trial offer")

print(f"OK — {passed} trial checks passed")

"""
test_billing_trial.py — billing-v2 P4: the 7-day free trial.

A trial is a Starter subscription whose first charge Razorpay schedules
TRIAL_DAYS out (start_at). Authenticating the card/mandate grants the 'trial'
plan + TRIAL_CREDITS; the day-8 charge grants Starter; no charge -> back to Free.

Two layers, one throwaway sqlite, no network:
  1. _subscriptions.py: eligibility (one trial per company and per person), the
     grant (only on an authenticated, unpaid trial; idempotent; heals a crash),
     the sweep (reminder once, conversion, lapse to Free, cancel keeps the trial
     to its end, never clobbers a plan someone else set), credits expire with it.
  2. blueprints/billing.py over HTTP with a fake Razorpay: start_at sent, the
     authentication payment is NEVER treated as a charge, verify/webhook start the
     trial, cancel, the day-8 charge, and re-subscribing after a lapsed trial.

    DATABASE_URL="sqlite:///$(mktemp -d)/t.sqlite" python3 tests/test_billing_trial.py

A failure here is a real regression in a money path.
"""

import hashlib
import hmac
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

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
for k in ("BILLING_SUBSCRIPTIONS", "BILLING_LIVE_PURCHASES", "BILLING_SCOPE"):
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
import _email
from sqlalchemy import text
import razorpay

A.init_schema()
A.migrate_schema()
engine = A._require_engine()

import server
from blueprints import billing as B

app = server.app
app.testing = True
UTC = timezone.utc
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


def row(sid):
    return S.get_by_razorpay_id(sid)


_n = [0]


def new_trial(org_id, user_id, ends=None, tier="starter"):
    _n[0] += 1
    sid = f"sub_TR{_n[0]}"
    S.create_local(org_id, user_id, tier, "monthly", sid, f"plan_TR{_n[0]}",
                   _pricing.subscription_amount_paise(tier, charge_gst=False),
                   trial_ends_at=ends or S.trial_ends_at())
    return sid


def authenticate(sid):
    return S.record_state(sid, status="authenticated")


def charge(sid, pid, when_end=None):
    return S.apply_charge(sid, pid, _pricing.subscription_amount_paise("starter", charge_gst=False),
                          current_start=S._now(), current_end=when_end or S._now() + timedelta(days=30),
                          paid_count=1)


# ═════════════════════════════════════════════════════════════════════════════
# 1. _subscriptions.py
# ═════════════════════════════════════════════════════════════════════════════
check(_pricing.TRIAL_DAYS == 7 and _pricing.TRIAL_CREDITS == 500, "trial = 7 days, 500 credits (decision #9)")
check(_pricing.TRIAL_CONVERTS_TO == "starter" and _pricing.subscription_error("starter") is None,
      "the trial converts to self-serve Starter")
check(_pricing.is_dashboard_tier(_pricing.plan_id_v2("trial")), "the trial plan includes the dashboard")
check(_pricing.entitlements(_pricing.plan_id_v2("trial")) == {"pro_columns": True, "api_elevated": False},
      "trial: all signals, no raised API limit")

now = S._now()
end = S.trial_ends_at(now)
check(end - now.replace(microsecond=0) == timedelta(days=7) and end.microsecond == 0,
      "the trial ends exactly TRIAL_DAYS out, in whole seconds (Razorpay start_at is epoch seconds)")

# Eligibility.
alice, org_a = person("alice")
check(S.trial_unavailable(org_a, alice) is None, "a fresh company may start a trial")
g_user, g_org = person("hasplan")
sql("UPDATE organizations SET plan='v2_growth' WHERE id=:i", i=g_org)
check(S.trial_unavailable(g_org, g_user) == "has_plan", "a company already on a dashboard plan can't")
l_user, l_org = person("legacy")
sql("UPDATE organizations SET plan='pro' WHERE id=:i", i=l_org)
check(S.trial_unavailable(l_org, l_user) == "has_plan", "a legacy Pro company counts as having a plan")

# The grant: only for an authenticated, unpaid trial.
sid_a = new_trial(org_a, alice)
check(S.start_trial(sid_a).get("error") == "not_authenticated" and plan_of(org_a) == "free",
      "a checkout that hasn't authenticated grants nothing")
check(S.trial_unavailable(org_a, alice) is None, "an unfinished trial checkout doesn't use up the trial")
authenticate(sid_a)
res = S.start_trial(sid_a)
check(res["started"] is True and plan_of(org_a) == "v2_trial", "authentication grants the trial plan")
check(balance(alice, org_a) == 500, "…and 500 trial credits")
lot = sql("SELECT amount, expires_at, kind, source_ref FROM credit_lots WHERE billing_org_id=:o", o=org_a).first()
check(lot is not None and lot.amount == 500 and lot.source_ref == f"trial:{row(sid_a)['id']}",
      "the credits are one lot keyed to the trial")
check(S._aware(datetime.fromisoformat(str(lot.expires_at))) == S._aware(row(sid_a)["trial_ends_at"]), "the trial credits expire when the trial ends")
check(sql("SELECT COUNT(*) FROM orders WHERE org_id=:o", o=org_a).scalar() == 0, "a trial creates no paid order")
res2 = S.start_trial(sid_a)
check(res2["started"] is False and balance(alice, org_a) == 500, "a second authentication (redelivery) grants nothing more")
check(S.trial_unavailable(org_a, alice) == "trial_used", "the company's trial is now used")

# One per person too: alice can't get another trial through a second company.
z_user, z_org = person("zoya")
check(S.trial_unavailable(z_org, alice) == "trial_used", "one trial per person: another company doesn't reset it")
# …and a colleague can't start a second one for the same company.
bob, org_b = person("bob")
A.add_org_member(org_a, alice, "bob@example.com", "admin")
check(S.trial_unavailable(org_a, bob) == "trial_used", "one trial per company, whoever asks")
check(S.trial_unavailable(org_b, bob) is None, "bob's own company is still eligible")

# A paid subscription in the past also uses up the trial.
p_user, p_org = person("paidbefore")
S.create_local(p_org, p_user, "growth", "monthly", "sub_PAID", "plan_PAID", 1_200_000)
S.apply_charge("sub_PAID", "pay_PAID", 1_200_000, current_end=S._now() + timedelta(days=30), paid_count=1)
check(S.trial_unavailable(p_org, p_user) == "trial_used", "a paying company can't")
S.record_state("sub_PAID", status="cancelled", ends_at=S._now())
S.sweep_ended()
check(plan_of(p_org) == "free" and S.trial_unavailable(p_org, p_user) == "trial_used",
      "a company that has paid before can't take a trial after cancelling")

# Not a trial / too late / company already has a plan at grant time.
n_user, n_org = person("nottrial")
S.create_local(n_org, n_user, "starter", "monthly", "sub_NT", "plan_NT", 500_000)
authenticate("sub_NT")
check(S.start_trial("sub_NT").get("error") == "not_a_trial" and plan_of(n_org) == "free",
      "an ordinary subscription is never turned into a trial")
check(S.start_trial("sub_nope").get("error") == "unknown_subscription", "unknown id -> error")
late_user, late_org = person("late")
sid_late = new_trial(late_org, late_user, ends=S._now() - timedelta(minutes=1))
authenticate(sid_late)
check(S.start_trial(sid_late).get("error") == "trial_over" and plan_of(late_org) == "free",
      "an authentication that arrives after the trial would have ended grants nothing")
h_user, h_org = person("handset")
sid_h = new_trial(h_org, h_user)
authenticate(sid_h)
sql("UPDATE organizations SET plan='v2_growth' WHERE id=:i", i=h_org)
check(S.start_trial(sid_h).get("error") == "trial_not_available" and plan_of(h_org) == "v2_growth"
      and balance(h_user, h_org) == 0, "a company given a plan meanwhile is not dropped to 'trial' nor given credits")

# A crash between "trial started" and "credits granted" heals on the next call.
c_user, c_org = person("crash")
sid_c = new_trial(c_org, c_user)
authenticate(sid_c)
sql("UPDATE subscriptions SET trial_started_at=:t WHERE razorpay_subscription_id=:s", t=S._now(), s=sid_c)
check(balance(c_user, c_org) == 0, "(simulated crash: stamped, no credits)")
check(S.start_trial(sid_c)["started"] is False and balance(c_user, c_org) == 500,
      "the next authentication delivery grants the missing credits, once")

# ── The sweep ────────────────────────────────────────────────────────────────
sent.clear()
ends_a = S._aware(row(sid_a)["trial_ends_at"])
check(S.trial_sweep(now=ends_a - timedelta(hours=30)) == [] and sent == [], "nothing to do mid-trial")
n = S.trial_sweep(now=ends_a - timedelta(hours=23))
mine = [x for x in n if x["org_name"] == "alice Co"]
check([x["kind"] for x in mine] == ["trial_ending"], "a day before the charge: one 'trial ends tomorrow' notice")
check(sorted(mine[0]["to"]) == ["alice@example.com", "bob@example.com"], "…to the company's owner and admins")
check(all(x["org_name"] == "crash Co" for x in n if x not in mine), "(the other running trial gets its own)")
before_sent = len(sent)
check(S.trial_sweep(now=ends_a - timedelta(hours=22)) == [] and len(sent) == before_sent, "…and only once")

# Conversion: the day-8 charge grants Starter and its credits.
charge(sid_a, "pay_A8")
check(plan_of(org_a) == "v2_starter", "the day-8 charge converts the trial to Starter")
check(sql("SELECT COUNT(*) FROM orders WHERE razorpay_order_id='sub_charge_pay_A8'").scalar() == 1, "one paid order")
check(S.trial_sweep(now=ends_a + timedelta(hours=1)) == [] and row(sid_a)["trial_ended_at"] is not None,
      "the sweep marks a converted trial ended, sending nothing")
check(not [x for x in S.trial_sweep(now=ends_a + timedelta(days=2)) if x["org_name"] == "alice Co"]
      and plan_of(org_a) == "v2_starter",
      "a converted trial is never taken back to Free")
check(A.expire_due_credits(now=ends_a + timedelta(minutes=1)) >= 0 and balance(alice, org_a) == 1000,
      "after the trial ends its leftover credits expire; Starter's 1,000 remain")

# Lapse: no charge by end + grace -> Free, one notice, no dunning.
b_sid = new_trial(org_b, bob)
authenticate(b_sid)
S.start_trial(b_sid)
ends_b = S._aware(row(b_sid)["trial_ends_at"])
S.record_state(b_sid, status="pending")                     # Razorpay: the first charge failed
grace = timedelta(hours=_pricing.TRIAL_GRACE_HOURS)
sent.clear()
S.trial_sweep(now=ends_b + grace - timedelta(minutes=1))
check(plan_of(org_b) == "v2_trial", "within the grace window the trial stays (the charge may still land)")
n = S.trial_sweep(now=ends_b + grace + timedelta(minutes=1))
check(plan_of(org_b) == "free" and [x["kind"] for x in n] == ["trial_lapsed"],
      "no charge after the grace window -> back to Free, with one notice")
check(S.trial_sweep(now=ends_b + grace + timedelta(hours=2)) == [], "…only once")
check(row(b_sid)["past_due_since"] is None and A.account_standing(bob, org_b) is None,
      "a failed FIRST charge is not past due: nothing is locked (decision #2: drop to Free)")
check(S.get_live(org_b) is not None and S.public_view(row(b_sid))["trial"]["state"] == "lapsed",
      "the Billing page sees a lapsed trial")
# Razorpay's retry succeeds later: they paid, so they get Starter.
charge(b_sid, "pay_B_retry")
check(plan_of(org_b) == "v2_starter", "a late retry that goes through grants Starter")

# Cancel during the trial: keeps the trial to its end, then Free, no notice.
d_user, d_org = person("dana")
d_sid = new_trial(d_org, d_user)
authenticate(d_sid)
S.start_trial(d_sid)
ends_d = S._aware(row(d_sid)["trial_ends_at"])
S.record_state(d_sid, status="cancelled", ends_at=ends_d)
S.sweep_ended(now=ends_d - timedelta(days=2))
S.trial_sweep(now=ends_d - timedelta(days=2))
check(plan_of(d_org) == "v2_trial", "a cancelled trial keeps the dashboard until the trial's end")
sent.clear()
check(not [x for x in S.trial_sweep(now=ends_d - timedelta(hours=23)) if x["org_name"] == "dana Co"]
      and row(d_sid)["trial_reminded_at"] is None, "no 'ends tomorrow, you'll be charged' mail after cancelling")
S.sweep_ended(now=ends_d + timedelta(seconds=1))
n = S.trial_sweep(now=ends_d + timedelta(seconds=1))
check(plan_of(d_org) == "free" and n == [] and sent == [],
      "at the end it goes back to Free, without a 'payment failed' mail")

# A plan someone set by hand during the trial is never clobbered by the lapse.
e_user, e_org = person("erin")
e_sid = new_trial(e_org, e_user)
authenticate(e_sid)
S.start_trial(e_sid)
sql("UPDATE organizations SET plan='v2_growth' WHERE id=:i", i=e_org)
S.trial_sweep(now=S._aware(row(e_sid)["trial_ends_at"]) + grace + timedelta(minutes=1))
check(plan_of(e_org) == "v2_growth", "the lapse only takes back a plan that is still exactly 'trial'")

# ═════════════════════════════════════════════════════════════════════════════
# 2. HTTP
# ═════════════════════════════════════════════════════════════════════════════
os.environ["BILLING_SUBSCRIPTIONS"] = "1"


class _Plan:
    def create(self, data):
        FakeRP.plan_creates += 1
        return {"id": f"plan_rp_{FakeRP.plan_creates}"}


class _Subscription:
    def create(self, data):
        FakeRP.sub_creates += 1
        FakeRP.last_sub_payload = data
        return {"id": f"sub_rp_{FakeRP.sub_creates}"}

    def fetch(self, sid):
        return FakeRP.sub_entities[sid]

    def cancel(self, sid, data):
        FakeRP.cancels.append((sid, data))
        return {"id": sid}


class _Payment:
    def fetch(self, pid):
        return FakeRP.payments[pid]


class FakeRP:
    plan_creates = 0
    sub_creates = 0
    cancels = []
    last_sub_payload = None
    sub_entities = {}
    payments = {}
    plan = _Plan()
    subscription = _Subscription()
    payment = _Payment()
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


def sign_sub(sid, pid):
    return hmac.new(b"trial_secret", f"{pid}|{sid}".encode(), hashlib.sha256).hexdigest()


def webhook(payload):
    raw = json.dumps(payload)
    sig = hmac.new(b"whsec_trial", raw.encode(), hashlib.sha256).hexdigest()
    return app.test_client().post("/api/billing/webhook", data=raw,
                                  headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"})


def epoch(days=0):
    return int((datetime.now(UTC) + timedelta(days=days)).timestamp())


check(B.subscriptions_state()["enabled"] is True, "subscriptions open for the HTTP tests")

k_user, k_org = person("kiran")
m_user, _ = person("meera")
A.add_org_member(k_org, k_user, "meera@example.com", "member")
j = get_subs(k_user, k_org)
check(j["trial"]["available"] is True and j["trial"]["days"] == 7 and j["trial"]["credits"] == 500
      and j["trial"]["tier"] == "starter", "GET says a fresh company may start the 7-day trial")
check(j["trial"]["amount_paise"] == B._sub_amount("starter"), "…and what Starter will cost on day 8")
check(post(m_user, "/subscriptions", {"tier": "starter", "trial": True, "org_id": k_org}).status_code == 403,
      "a plain member can't start the company's trial")
r = post(k_user, "/subscriptions", {"tier": "growth", "trial": True})
check(r.status_code == 400 and r.get_json()["error"] == "trial_tier", "the trial only leads into Starter")
check(FakeRP.sub_creates == 0, "refusals never reach Razorpay")

before = datetime.now(UTC)
r = post(k_user, "/subscriptions", {"tier": "starter", "trial": True})
check(r.status_code == 201, "the owner starts a trial checkout")
sid_k = r.get_json()["razorpay_subscription_id"]
start_at = FakeRP.last_sub_payload.get("start_at")
check(start_at is not None and abs(start_at - int((before + timedelta(days=7)).timestamp())) <= 5,
      "Razorpay is told to take the first charge 7 days out (start_at)")
check(FakeRP.last_sub_payload["notes"].get("trial") == "1", "the Razorpay subscription is labelled a trial")
check(int(S._aware(row(sid_k)["trial_ends_at"]).timestamp()) == start_at,
      "the local trial end is exactly the scheduled charge time")
check(r.get_json()["trial_ends_at"] is not None, "Checkout is told when the trial ends")
check(plan_of(k_org) == "free", "starting the checkout grants nothing")

# THE money trap: the authentication payment (a token amount, refunded) must never count as a charge.
FakeRP.payments["pay_auth_k"] = {"id": "pay_auth_k", "status": "captured", "amount": 500, "order_id": "order_auth_k"}
FakeRP.sub_entities[sid_k] = {"id": sid_k, "status": "authenticated", "paid_count": 0, "start_at": start_at}
r = post(k_user, "/subscriptions/verify", {"razorpay_subscription_id": sid_k, "razorpay_payment_id": "pay_auth_k",
                                            "razorpay_signature": sign_sub(sid_k, "pay_auth_k")})
check(r.status_code == 200 and r.get_json()["status"] == "trial", "verify after authenticating starts the trial")
check(plan_of(k_org) == "v2_trial" and balance(k_user, k_org) == 500, "trial plan + 500 credits")
check(sql("SELECT COUNT(*) FROM orders WHERE org_id=:o", o=k_org).scalar() == 0
      and sql("SELECT COUNT(*) FROM invoices i JOIN orders o ON o.id=i.order_id WHERE o.org_id=:o", o=k_org).scalar() == 0,
      "the authentication payment created no paid order and no invoice")
check(r.get_json()["subscription"]["trial"]["state"] == "running", "the view says the trial is running")
r = post(k_user, "/subscriptions/verify", {"razorpay_subscription_id": sid_k, "razorpay_payment_id": "pay_auth_k",
                                            "razorpay_signature": sign_sub(sid_k, "pay_auth_k")})
check(balance(k_user, k_org) == 500, "verifying twice changes nothing")

# The same trap on an ORDINARY subscription: an authenticated, unpaid one is pending, never a charge.
o_user, o_org = person("ordinary")
r = post(o_user, "/subscriptions", {"tier": "starter"})
sid_o = r.get_json()["razorpay_subscription_id"]
check("start_at" not in FakeRP.last_sub_payload and row(sid_o)["trial_ends_at"] is None,
      "an ordinary subscription is charged now, not scheduled")
FakeRP.payments["pay_auth_o"] = {"id": "pay_auth_o", "status": "captured", "amount": 500}
FakeRP.sub_entities[sid_o] = {"id": sid_o, "status": "authenticated", "paid_count": 0}
r = post(o_user, "/subscriptions/verify", {"razorpay_subscription_id": sid_o, "razorpay_payment_id": "pay_auth_o",
                                            "razorpay_signature": sign_sub(sid_o, "pay_auth_o")})
check(r.get_json()["status"] == "pending" and plan_of(o_org) == "free" and balance(o_user, o_org) == 0,
      "an authentication-only payment on a normal subscription grants nothing")

# Second trial: refused for the company and for the person.
j = get_subs(k_user, k_org)
check(j["trial"]["available"] is False and j["subscription"]["trial"]["state"] == "running",
      "GET: trial used, and the current one is shown as running")
r = post(k_user, "/subscriptions", {"tier": "starter", "trial": True})
check(r.status_code == 409 and r.get_json()["error"] == "already_subscribed", "can't start another while one runs")

# Webhook path: subscription.authenticated starts a trial when the browser never came back.
w_user, w_org = person("webhookonly")
r = post(w_user, "/subscriptions", {"tier": "starter", "trial": True})
sid_w = r.get_json()["razorpay_subscription_id"]
r = webhook({"event": "subscription.authenticated", "created_at": epoch(0),
             "payload": {"subscription": {"entity": {"id": sid_w, "status": "authenticated", "paid_count": 0}}}})
check(r.status_code == 200 and plan_of(w_org) == "v2_trial" and balance(w_user, w_org) == 500,
      "the authenticated webhook starts the trial on its own")
webhook({"event": "subscription.authenticated", "created_at": epoch(0),
         "payload": {"subscription": {"entity": {"id": sid_w, "status": "authenticated", "paid_count": 0}}}})
check(balance(w_user, w_org) == 500, "a redelivered webhook grants nothing more")
check(get_subs(w_user, w_org)["trial"]["reason"] == "trial_used", "…and the person can't start another anywhere")
# One per person: as an admin of someone else's fresh company, they still can't start another.
y_user, y_org = person("yusuf")
A.add_org_member(y_org, y_user, "webhookonly@example.com", "admin")
r = post(w_user, "/subscriptions", {"tier": "starter", "trial": True, "org_id": y_org})
check(r.status_code == 409 and r.get_json().get("reason") == "trial_used",
      "one trial per person: admin of a second company, still refused")
check(get_subs(y_user, y_org)["trial"]["available"] is True, "…while that company's own owner may start one")

# Day 8: Razorpay charges; subscription.charged converts.
FakeRP.payments.clear()
pay = {"id": "pay_w8", "status": "captured", "amount": B._sub_amount("starter"), "order_id": "order_w8"}
r = webhook({"event": "subscription.charged", "created_at": epoch(0),
             "payload": {"subscription": {"entity": {"id": sid_w, "status": "active", "paid_count": 1,
                                                     "current_start": epoch(0), "current_end": epoch(30)}},
                         "payment": {"entity": pay}}})
check(r.status_code == 200 and plan_of(w_org) == "v2_starter", "the day-8 charge converts the trial to Starter")
check(sql("SELECT COUNT(*) FROM invoices i JOIN orders o ON o.id=i.order_id WHERE o.razorpay_order_id='sub_charge_pay_w8'"
          ).scalar() == 1, "…with an invoice for the real charge")
check(row(sid_w)["trial_ended_at"] is not None, "the webhook's sweep marks the trial converted")

# Cancel during the trial: Razorpay cancels now (nothing to wait for), the trial runs to its end.
FakeRP.cancels.clear()
r = post(k_user, "/subscriptions/cancel", {})
check(r.status_code == 200 and FakeRP.cancels == [(sid_k, {"cancel_at_cycle_end": 0})],
      "cancelling a trial cancels at Razorpay immediately, so day 8 is never charged")
check(row(sid_k)["status"] == "cancelled" and plan_of(k_org) == "v2_trial",
      "…but the company keeps the trial until it ends")
check(S._aware(row(sid_k)["ends_at"]) == S._aware(row(sid_k)["trial_ends_at"]), "the view shows it ending at the trial's end")

# A lapsed trial: subscribing again replaces the old (still-retrying) Razorpay subscription.
x_user, x_org = person("xavi")
r = post(x_user, "/subscriptions", {"tier": "starter", "trial": True})
sid_x = r.get_json()["razorpay_subscription_id"]
FakeRP.payments["pay_auth_x"] = {"id": "pay_auth_x", "status": "captured", "amount": 500}
FakeRP.sub_entities[sid_x] = {"id": sid_x, "status": "authenticated", "paid_count": 0}
post(x_user, "/subscriptions/verify", {"razorpay_subscription_id": sid_x, "razorpay_payment_id": "pay_auth_x",
                                       "razorpay_signature": sign_sub(sid_x, "pay_auth_x")})
S.record_state(sid_x, status="halted")
S.trial_sweep(now=S._aware(row(sid_x)["trial_ends_at"]) + grace + timedelta(minutes=1))
check(plan_of(x_org) == "free" and get_subs(x_user, x_org)["subscription"]["trial"]["state"] == "lapsed",
      "a halted trial lapses to Free and the page says so")
check(get_subs(x_user, x_org)["standing"] is None, "…without the past-due / locked banner")
r = post(x_user, "/subscriptions", {"tier": "starter", "trial": True})
check(r.status_code == 409 and r.get_json()["error"] == "trial_not_available", "no second trial after a lapse")
FakeRP.cancels.clear()
r = post(x_user, "/subscriptions", {"tier": "starter"})
check(r.status_code == 201 and FakeRP.cancels == [(sid_x, {"cancel_at_cycle_end": 0})],
      "subscribing after a lapse cancels the old mandate at Razorpay first, then starts a fresh one")
check(row(sid_x)["status"] == "cancelled" and "start_at" not in FakeRP.last_sub_payload,
      "…the new one is an ordinary, charge-now subscription")

print(f"OK — {passed} trial checks passed")

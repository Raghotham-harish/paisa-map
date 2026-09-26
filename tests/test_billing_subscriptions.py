"""
test_billing_subscriptions.py — billing-v2 P2: recurring subscriptions.

Two layers, one throwaway sqlite, no network:
  1. _subscriptions.py (DB state machine): grant only on a real charge, one paid
     order per payment however many times it is delivered, stale/ended events
     can't rewrite state, a plan is granted/revoked without clobbering a plan
     someone else assigned, one live subscription per paying company.
  2. blueprints/billing.py (HTTP): the dark-by-default gate, who may subscribe,
     create/verify/cancel, and the subscription.* webhook end to end — with a
     fake Razorpay client and real HMAC signatures.

    DATABASE_URL="sqlite:////tmp/billing_subs.sqlite" python3 tests/test_billing_subscriptions.py

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

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/billing_subs_default.sqlite")
if "sqlite" not in os.environ["DATABASE_URL"]:
    raise SystemExit("Refusing to run: DATABASE_URL doesn't look like a throwaway sqlite file.")
if not os.environ.get("CUSTOMER_DATA_KEY"):
    from cryptography.fernet import Fernet
    os.environ["CUSTOMER_DATA_KEY"] = Fernet.generate_key().decode()
os.environ.setdefault("INVOICES_DIR", tempfile.mkdtemp(prefix="pm_invoices_"))
for k in ("BILLING_SUBSCRIPTIONS", "BILLING_LIVE_PURCHASES", "BILLING_SCOPE"):
    os.environ.pop(k, None)
os.environ["RAZORPAY_KEY_ID"] = "rzp_test_subs"
os.environ["RAZORPAY_KEY_SECRET"] = "subs_secret"
os.environ["RAZORPAY_WEBHOOK_SECRET"] = "whsec_subs"

import _db
_db.enabled = lambda: False
import _auth_db as A
import _pricing
import _subscriptions as S
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


def plan_of(org_id):
    return sql("SELECT plan FROM organizations WHERE id=:i", i=org_id).scalar()


def sub_row(sid):
    return S.get_by_razorpay_id(sid)


def orders_for(payment_id):
    return sql("SELECT COUNT(*) FROM orders WHERE razorpay_order_id=:k", k=f"sub_charge_{payment_id}").scalar()


_n = [0]


def new_sub(org_id, user_id, tier="growth"):
    _n[0] += 1
    sid = f"sub_T{_n[0]}"
    S.create_local(org_id, user_id, tier, "monthly", sid, f"plan_T{_n[0]}",
                   _pricing.subscription_amount_paise(tier, charge_gst=False))
    return sid


def epoch(days=0):
    return int((datetime.now(UTC) + timedelta(days=days)).timestamp())


# ═════════════════════════════════════════════════════════════════════════════
# 1. _subscriptions.py — the state machine
# ═════════════════════════════════════════════════════════════════════════════
alice, org_a = person("alice")
bob, org_b = person("bob")
carol, org_c = person("carol")
dan, org_d = person("dan")
erin, org_e = person("erin")

# Schema: both tables exist, the live-uniqueness index exists.
idx = {r[1] for r in sql("PRAGMA index_list(subscriptions)").fetchall()}
check("uq_subscriptions_live_org" in idx, "partial unique index on live subscriptions exists")
check(sql("SELECT COUNT(*) FROM razorpay_plans").scalar() == 0, "razorpay_plans table exists")

# Eligibility: only monthly Starter/Growth are self-serve.
check(_pricing.subscription_error("starter") is None and _pricing.subscription_error("growth") is None,
      "starter and growth are self-serve")
check(_pricing.subscription_error("scale") == "contact_sales", "scale is sales-assisted")
check(_pricing.subscription_error("pro") == "contact_sales", "pro is sales-assisted")
check(_pricing.subscription_error("enterprise") == "contact_sales", "enterprise is sales-assisted")
check(_pricing.subscription_error("trial") == "invalid_tier", "the free trial is not a subscription")
check(_pricing.subscription_error("nope") == "invalid_tier", "unknown tier rejected")
check(_pricing.subscription_error("growth", "annual") == "cycle_not_self_serve", "annual is not self-serve")
check(_pricing.subscription_list_paise("growth") == 1_200_000, "growth list price is Rs 12,000 (ex-GST)")
check(_pricing.subscription_amount_paise("growth", charge_gst=False) == 1_200_000, "unregistered: charged the list price")
check(_pricing.subscription_amount_paise("growth", charge_gst=True) == 1_416_000, "registered: Rs 12,000 + GST = Rs 14,160")
for t in _pricing.TIERS:
    if _pricing.subscription_error(t) is None:
        check(_pricing.subscription_amount_paise(t, charge_gst=True) <= _pricing.UPI_AUTOPAY_MAX_PAISE,
              f"{t}: the charge fits under the UPI Autopay cap even with GST")
try:
    _pricing.subscription_amount_paise("growth")
    check(False, "charge_gst must be passed explicitly")
except TypeError:
    check(True, "charge_gst must be passed explicitly")
refused = False
try:
    _pricing.subscription_amount_paise("scale", charge_gst=True)
except AssertionError:
    refused = True
check(refused, "the charge amount for a non-self-serve tier refuses")

_saved = _pricing.TIERS["growth"]["price_paise"]
# The cap is judged on the price WITH GST (Rs 12,711.86 + GST = Rs 14,999.99 fits;
# one paisa more is Rs 15,000.01 and doesn't) — even while GST isn't charged yet.
_pricing.TIERS["growth"]["price_paise"] = 1_271_187
check(_pricing.subscription_error("growth") == "exceeds_autopay_cap", "a charge over the cap once GST is added is refused")
_pricing.TIERS["growth"]["price_paise"] = 1_271_186
check(_pricing.subscription_error("growth") is None, "the largest list price that fits with GST is allowed")
_pricing.TIERS["growth"]["price_paise"] = _pricing.UPI_AUTOPAY_MAX_PAISE
check(_pricing.subscription_error("growth") == "exceeds_autopay_cap", "a list price at the cap is refused (GST would push it over)")
_pricing.TIERS["growth"]["price_paise"] = _saved

# Nothing is granted before a charge.
s1 = new_sub(org_a, alice)
check(sub_row(s1)["status"] == "created", "new subscription starts 'created'")
S.record_state(s1, status="authenticated", event_ts=S.ts(epoch(-3)))
check(plan_of(org_a) == "free", "authenticated alone grants no plan")
S.record_state(s1, status="active", event_ts=S.ts(epoch(-2)))
check(plan_of(org_a) == "free", "an 'active' status event alone grants no plan")

# A real charge grants the plan, records one paid order, sets the period.
r = S.apply_charge(s1, "pay_1", 1_200_000, current_start=S.ts(epoch(-1)), current_end=S.ts(epoch(29)),
                   paid_count=1, event_ts=S.ts(epoch(-1)), razorpay_order_id="order_rp_1")
check(r["newly_paid"] is True, "first delivery of a charge is newly paid")
check(plan_of(org_a) == "v2_growth", "a charge grants the company the v2 tier")
check(orders_for("pay_1") == 1, "one local order per payment")
o = r["order"]
check(o["status"] == "paid" and o["amount_paise"] == 1_200_000 and o["kind"] == "plan_upgrade",
      "the order is paid, for the charged amount")
check(o["target_plan"] == "v2_growth" and o["org_id"] == org_a and o["billing_org_id"] == org_a,
      "the order is stamped with the plan and the paying company")
check(sub_row(s1)["paid_count"] == 1 and sub_row(s1)["status"] == "active", "paid_count and status updated")

# Redelivery / the other path (verify vs webhook) is a no-op.
r2 = S.apply_charge(s1, "pay_1", 1_200_000, current_start=S.ts(epoch(-1)), current_end=S.ts(epoch(29)),
                    paid_count=1, event_ts=S.ts(epoch(-1)), razorpay_order_id="order_rp_1")
check(r2["newly_paid"] is False, "a redelivered charge is not newly paid")
check(orders_for("pay_1") == 1, "a redelivered charge creates no second order")
check(sub_row(s1)["paid_count"] == 1, "a redelivered charge doesn't bump paid_count")

# Redelivery with NO paid_count in the payload (the fallback counter) still doesn't double-count.
cnt_user, cnt_org = person("counter")
sc = new_sub(cnt_org, cnt_user)
S.apply_charge(sc, "pay_cnt", 1_200_000, current_end=S.ts(epoch(29)))
S.apply_charge(sc, "pay_cnt", 1_200_000, current_end=S.ts(epoch(29)))
S.apply_charge(sc, "pay_cnt", 1_200_000, current_end=S.ts(epoch(29)))
check(sub_row(sc)["paid_count"] == 1, "three deliveries of one payment (no paid_count given) count once")
S.apply_charge(sc, "pay_cnt2", 1_200_000, current_end=S.ts(epoch(59)))
check(sub_row(sc)["paid_count"] == 2, "a second payment counts once more")

# Renewal: a new payment extends the period, one more order.
r3 = S.apply_charge(s1, "pay_2", 1_200_000, current_start=S.ts(epoch(29)), current_end=S.ts(epoch(59)),
                    paid_count=2, event_ts=S.ts(epoch(29)))
check(r3["newly_paid"] and sub_row(s1)["paid_count"] == 2, "a renewal is a new paid order")
check(S._aware(sub_row(s1)["current_end"]) > datetime.now(UTC) + timedelta(days=50), "renewal extends the period")

# Stale events can't rewrite newer state.
res = S.record_state(s1, status="halted", event_ts=S.ts(epoch(-30)))
check(res.get("stale") is True and sub_row(s1)["status"] == "active", "an older event is ignored")
# epoch(29) equals the renewal's own stamp, so it is not older: it applies.
res = S.record_state(s1, status="cancelled", event_ts=S.ts(epoch(29)))
check(sub_row(s1)["status"] == "cancelled", "an event at least as new as the last one applies")
check(res["subscription"]["ends_at"] is not None, "ending a subscription stamps ends_at")

# An ended subscription is never resurrected by a stale 'active'.
res = S.record_state(s1, status="active", event_ts=S.ts(epoch(40)))
check(res.get("ignored_status") == "active" and sub_row(s1)["status"] == "cancelled",
      "an ended subscription is not resurrected")

# Sweep: access stays until the paid period is over, then the plan is revoked.
check(S.sweep_ended() == 0 and plan_of(org_a) == "v2_growth",
      "sweep doesn't revoke while the paid period is still running")
check(S.sweep_ended(now=datetime.now(UTC) + timedelta(days=90)) == 1 and plan_of(org_a) == "free",
      "sweep revokes once the paid period is over")
check(S.sweep_ended(now=datetime.now(UTC) + timedelta(days=91)) == 0, "sweep is idempotent")
check(sub_row(s1)["plan_revoked_at"] is not None, "revocation is stamped")

# A live-unique clash: a second live subscription for one company is a duplicate.
s2 = new_sub(org_b, bob, "starter")
S.apply_charge(s2, "pay_b1", 500_000, current_start=S.ts(epoch(-1)), current_end=S.ts(epoch(29)), paid_count=1)
check(plan_of(org_b) == "v2_starter" and S.get_live(org_b)["razorpay_subscription_id"] == s2, "org B is live on Starter")
s3 = new_sub(org_b, bob, "growth")
res = S.record_state(s3, status="active", event_ts=S.ts(epoch(0)))
check(res.get("duplicate") is True and sub_row(s3)["status"] == "duplicate",
      "a second live subscription for one company is marked duplicate")
r = S.apply_charge(s3, "pay_b_dup", 1_200_000, current_end=S.ts(epoch(29)), paid_count=1)
check(r["order"]["status"] == "paid", "a charge on a duplicate is still recorded (the money is real)")
check(sub_row(s3)["status"] == "duplicate" and plan_of(org_b) == "v2_starter",
      "…but it never upgrades the plan (a HIGHER tier duplicate must not grant)")
check(S.get_live(org_b)["razorpay_subscription_id"] == s2, "the original stays the live one")

# The database itself refuses two live subscriptions for one company (the backstop
# behind the check in code) — but not a live one next to ended/abandoned ones.
from sqlalchemy.exc import IntegrityError
raised = False
try:
    sql("INSERT INTO subscriptions (org_id, tier, cycle, razorpay_subscription_id, razorpay_plan_id, status, "
        "amount_paise, paid_count, cancel_at_period_end, created_at, updated_at) VALUES "
        "(:o, 'growth', 'monthly', 'sub_raw_2', 'p', 'active', 1, 0, 0, :n, :n)", o=org_b, n=datetime.now(UTC))
except IntegrityError:
    raised = True
check(raised, "the unique index rejects a second live subscription for one company")
sql("INSERT INTO subscriptions (org_id, tier, cycle, razorpay_subscription_id, razorpay_plan_id, status, "
    "amount_paise, paid_count, cancel_at_period_end, created_at, updated_at) VALUES "
    "(:o, 'growth', 'monthly', 'sub_raw_3', 'p', 'cancelled', 1, 0, 0, :n, :n)", o=org_b, n=datetime.now(UTC))
check(sub_row("sub_raw_3") is not None, "…but an ended one next to a live one is fine")

# Grant never downgrades a higher v2 plan set by hand; revoke never clobbers one.
sql("UPDATE organizations SET plan='v2_scale' WHERE id=:i", i=org_c)
s4 = new_sub(org_c, carol, "starter")
S.apply_charge(s4, "pay_c1", 500_000, current_end=S.ts(epoch(29)), paid_count=1)
check(plan_of(org_c) == "v2_scale", "granting Starter doesn't downgrade a company already on Scale")
S.record_state(s4, status="cancelled", event_ts=S.ts(epoch(1)))
S.sweep_ended(now=datetime.now(UTC) + timedelta(days=90))
check(plan_of(org_c) == "v2_scale", "revoking Starter doesn't touch a plan that is no longer Starter")

sql("UPDATE organizations SET plan='v2_pro' WHERE id=:i", i=org_d)
s5 = new_sub(org_d, dan, "growth")
S.apply_charge(s5, "pay_d1", 1_200_000, current_end=S.ts(epoch(29)), paid_count=1)
sql("UPDATE organizations SET plan='v2_starter' WHERE id=:i", i=org_e)
s6 = new_sub(org_e, erin, "growth")
S.apply_charge(s6, "pay_e1", 1_200_000, current_end=S.ts(epoch(29)), paid_count=1)
check(plan_of(org_e) == "v2_growth", "granting Growth upgrades a company on Starter")
sql("UPDATE organizations SET plan='v2_scale' WHERE id=:i", i=org_e)     # sales moves them up meanwhile
S.record_state(s6, status="cancelled", event_ts=S.ts(epoch(1)))
S.sweep_ended(now=datetime.now(UTC) + timedelta(days=90))
check(plan_of(org_e) == "v2_scale", "revoke leaves a plan sales assigned in the meantime alone")

# Halted keeps the plan (soft-lock is P13) and records when.
s7_user, s7_org = person("frank")
s7 = new_sub(s7_org, s7_user)
S.apply_charge(s7, "pay_f1", 1_200_000, current_end=S.ts(epoch(29)), paid_count=1)
S.record_state(s7, status="halted", event_ts=S.ts(epoch(2)))
check(sub_row(s7)["status"] == "halted" and sub_row(s7)["halted_at"] is not None, "halted is recorded")
check(plan_of(s7_org) == "v2_growth" and S.get_live(s7_org) is not None, "halted keeps the plan and stays live")
S.apply_charge(s7, "pay_f2", 1_200_000, current_end=S.ts(epoch(59)), paid_count=2, event_ts=S.ts(epoch(3)))
check(sub_row(s7)["status"] == "active" and sub_row(s7)["halted_at"] is None, "a successful retry clears halted")

# An abandoned checkout doesn't block a new attempt.
g_user, g_org = person("gina")
first = new_sub(g_org, g_user)
second = new_sub(g_org, g_user, "starter")
check(sub_row(first)["status"] == "abandoned" and sub_row(second)["status"] == "created",
      "a new attempt replaces an unfinished checkout")
check(S.get_live(g_org) is None, "an unfinished checkout is not live")

# Unknown subscription is an error, not a crash.
check(S.record_state("sub_nope", status="active").get("error") == "unknown_subscription", "unknown id -> error")
check(S.apply_charge("sub_nope", "pay_x", 1).get("error") == "unknown_subscription", "charge on unknown -> error")

# ═════════════════════════════════════════════════════════════════════════════
# 2. HTTP — gate, create, verify, cancel, webhook
# ═════════════════════════════════════════════════════════════════════════════
os.environ["BILLING_SCOPE"] = "wallet"

h_user, h_org = person("hana")          # owner of a fresh company
m_user, _ = person("mira")
A.add_org_member(h_org, h_user, "mira@example.com", "member")
ad_user, _ = person("adil")
A.add_org_member(h_org, h_user, "adil@example.com", "admin")
x_user, x_org = person("xavier")         # a stranger


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
    utility = razorpay.Client(auth=("rzp_test_subs", "subs_secret")).utility


B._client = lambda: FakeRP()


def client_as(uid):
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = uid
    return c


def post(uid, path, body):
    return client_as(uid).post(f"/api/billing{path}", json=body)


def sign_sub(sid, pid):
    return hmac.new(b"subs_secret", f"{pid}|{sid}".encode(), hashlib.sha256).hexdigest()


def webhook(payload, secret="whsec_subs", sig=None):
    raw = json.dumps(payload)
    sig = sig if sig is not None else hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return app.test_client().post("/api/billing/webhook", data=raw,
                                  headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"})


def sub_event(event, sid, *, status="active", payment=None, created_at=None, **entity):
    ent = {"id": sid, "status": status, **entity}
    body = {"event": event, "created_at": created_at or epoch(0), "payload": {"subscription": {"entity": ent}}}
    if payment:
        body["payload"]["payment"] = {"entity": payment}
    return body


# Gate: dark unless deliberately opened, and only in wallet scope.
def state_reason():
    return B.subscriptions_state()["reason"]


check(state_reason() == "not_enabled", "subscriptions are dark by default")
r = post(h_user, "/subscriptions", {"tier": "growth"})
check(r.status_code == 403 and r.get_json()["error"] == "subscriptions_unavailable", "create refused while dark")
check(FakeRP.sub_creates == 0, "a refused create never contacts Razorpay")
os.environ["BILLING_SUBSCRIPTIONS"] = "1"
os.environ.pop("BILLING_SCOPE")
check(state_reason() == "wallet_scope_required", "needs wallet scope: paying for a plan nobody reads is worse than none")
check(post(h_user, "/subscriptions", {"tier": "growth"}).status_code == 403, "create refused outside wallet scope")
os.environ["BILLING_SCOPE"] = "wallet"
os.environ["RAZORPAY_KEY_ID"] = "rzp_live_subs"
check(state_reason() == "purchases_not_open", "live keys need the deliberate purchases switch too")
check(post(h_user, "/subscriptions", {"tier": "growth"}).status_code == 403, "create refused on live keys by default")
os.environ["BILLING_LIVE_PURCHASES"] = "1"
check(B.subscriptions_state()["enabled"] is True, "live keys + purchases switch -> open")
os.environ.pop("BILLING_LIVE_PURCHASES")
os.environ["RAZORPAY_KEY_ID"] = "rzp_test_subs"
check(B.subscriptions_state() == {"enabled": True, "reason": None}, "test keys + flag + wallet scope -> open")
check(app.test_client().get("/api/billing/pricing").get_json()["subscriptions"]["enabled"] is True,
      "the public pricing payload reports the state")

# Before the apply script has run the tables don't exist: reads say "none", webhooks are
# acked as unknown, nobody can START a subscription (the payment would have nowhere to go).
_real_ready = S.tables_ready
S._ready = False
S.tables_ready = lambda: False
check(B.subscriptions_state() == {"enabled": False, "reason": "schema_not_applied"},
      "no tables -> subscriptions can't be started")
before = FakeRP.sub_creates
check(post(h_user, "/subscriptions", {"tier": "growth"}).status_code == 403 and FakeRP.sub_creates == before,
      "…and a refused create never contacts Razorpay")
r = client_as(h_user).get(f"/api/billing/subscriptions?org_id={h_org}")
check(r.status_code == 200 and r.get_json()["subscription"] is None, "reading with no tables answers 'none', not a 500")
check(post(h_user, "/subscriptions/cancel", {}).status_code == 404, "cancel with no tables -> nothing to cancel")
check(webhook(sub_event("subscription.charged", "sub_any", payment={"id": "pay_nt", "status": "captured", "amount": 1})
              ).get_json()["status"] == "unknown_subscription", "a subscription webhook with no tables is acked")
S.tables_ready = _real_ready
check(S.tables_ready() is True, "with the tables present the readiness check passes")
check(B.subscriptions_state()["enabled"] is True, "…and subscriptions open again")

# Validation and permissions.
check(post(h_user, "/subscriptions", {"tier": "scale"}).get_json()["error"] == "contact_sales", "scale -> contact_sales")
check(post(h_user, "/subscriptions", {"tier": "growth", "cycle": "annual"}).get_json()["error"] == "cycle_not_self_serve",
      "annual -> refused")
check(post(h_user, "/subscriptions", {"tier": "bogus"}).status_code == 400, "unknown tier -> 400")
check(post(h_user, "/subscriptions", {"tier": "growth", "org_id": "abc"}).status_code in (400, 403),
      "junk org_id refused")
check(post(m_user, "/subscriptions", {"tier": "growth", "org_id": h_org}).status_code == 403,
      "a plain member can't subscribe for the company")
check(post(x_user, "/subscriptions", {"tier": "growth", "org_id": h_org}).status_code == 403,
      "a stranger can't subscribe for someone else's company")
check(FakeRP.sub_creates == 0, "none of those refusals reached Razorpay")
r = app.test_client().post("/api/billing/subscriptions", json={"tier": "growth"})
check(r.status_code in (401, 403), "signed-out create refused")

# An admin of a LINKED CLIENT company can't buy a plan for the agency that pays for it:
# the plan lives on the payer, so only the payer's own owner/admin may subscribe.
ag_user, org_ag = person("agency")
cl_user, org_cl = person("client")
A.add_org_member(org_ag, ag_user, "client@example.com", "admin")            # client's owner also admins the agency
check(A.set_org_payer(org_cl, cl_user, org_ag).get("status") == "ok", "client company linked under the agency")
cl2_user, _ = person("client2")
A.add_org_member(org_cl, cl_user, "client2@example.com", "admin")           # admin of the CLIENT only
before = FakeRP.sub_creates
r = post(cl2_user, "/subscriptions", {"tier": "growth", "org_id": org_cl})
check(r.status_code == 403 and r.get_json()["error"] == "not_allowed",
      "an admin of a linked client company can't subscribe the paying agency")
check(FakeRP.sub_creates == before, "…and Razorpay was never contacted")
A.add_org_member(org_ag, ag_user, "client2@example.com", "member")          # ...even as a plain MEMBER of the payer
r = post(cl2_user, "/subscriptions", {"tier": "growth", "org_id": org_cl})
check(r.status_code == 403 and FakeRP.sub_creates == before,
      "a mere member of the paying company can't subscribe it either")
r = post(cl_user, "/subscriptions", {"tier": "growth", "org_id": org_cl})
check(r.status_code == 201, "someone who is admin of the payer can subscribe, naming the client")
check(S.get_by_razorpay_id(r.get_json()["razorpay_subscription_id"])["org_id"] == org_ag,
      "the subscription attaches to the PAYING company, not the client")

# The exact-tier boundary: already on Growth by hand -> can't buy Growth; on Starter -> may buy Growth.
g_own, g_org2 = person("growthhand")
sql("UPDATE organizations SET plan='v2_growth' WHERE id=:i", i=g_org2)
check(post(g_own, "/subscriptions", {"tier": "growth"}).get_json()["error"] == "plan_managed_directly",
      "already on exactly this tier by hand -> told to talk to us")
check(post(g_own, "/subscriptions", {"tier": "starter"}).get_json()["error"] == "plan_managed_directly",
      "a lower tier than the one held is refused too")
s_own, s_org2 = person("starterhand")
sql("UPDATE organizations SET plan='v2_starter' WHERE id=:i", i=s_org2)
check(post(s_own, "/subscriptions", {"tier": "growth"}).status_code == 201, "on Starter by hand -> may buy Growth")

# Create (as the owner), then a second attempt.
r = post(h_user, "/subscriptions", {"tier": "growth"})
check(r.status_code == 201, "owner creates a subscription")
j = r.get_json()
sid_h = j["razorpay_subscription_id"]
check(j["amount_paise"] == (1_416_000 if B._seller_gstin() else 1_200_000) and j["tier"] == "growth" and j["razorpay_key_id"] == "rzp_test_subs",
      "create returns what Checkout needs")
check(FakeRP.last_sub_payload["total_count"] == _pricing.SUBSCRIPTION_TOTAL_COUNT
      and FakeRP.last_sub_payload["notes"]["org_id"] == str(h_org), "the Razorpay subscription carries the company in its notes")
check(FakeRP.plan_creates == 1, "the Razorpay plan was created once")
r = post(ad_user, "/subscriptions", {"tier": "growth", "org_id": h_org})
check(r.status_code == 201 and FakeRP.plan_creates == 1, "an admin may subscribe; the plan id is cached, not re-created")
check(sub_row(sid_h)["status"] == "abandoned", "the unfinished first checkout is replaced")
sid_h = r.get_json()["razorpay_subscription_id"]
check(plan_of(h_org) == "free", "creating a subscription grants nothing")

# Verify: bad signature, wrong person, unsettled payment, then the real thing.
FakeRP.payments["pay_h1"] = {"id": "pay_h1", "status": "captured", "amount": 1_200_000, "order_id": "order_rp_h1"}
FakeRP.sub_entities[sid_h] = {"id": sid_h, "status": "active", "current_start": epoch(0), "current_end": epoch(30),
                              "paid_count": 1}
check(post(h_user, "/subscriptions/verify", {"razorpay_subscription_id": sid_h, "razorpay_payment_id": "pay_h1",
                                              "razorpay_signature": "deadbeef"}).status_code == 400,
      "a bad signature is refused")
check(post(h_user, "/subscriptions/verify", {"razorpay_subscription_id": sid_h}).status_code == 400, "missing fields")
good = {"razorpay_subscription_id": sid_h, "razorpay_payment_id": "pay_h1",
        "razorpay_signature": sign_sub(sid_h, "pay_h1")}
check(post(x_user, "/subscriptions/verify", good).status_code == 404, "a stranger with a valid signature gets nothing")
check(post(m_user, "/subscriptions/verify", good).status_code == 404, "a plain member can't verify it either")
check(plan_of(h_org) == "free", "no refused verify granted anything")
FakeRP.payments["pay_h0"] = {"id": "pay_h0", "status": "authorized", "amount": 1_200_000}
r = post(h_user, "/subscriptions/verify", {"razorpay_subscription_id": sid_h, "razorpay_payment_id": "pay_h0",
                                            "razorpay_signature": sign_sub(sid_h, "pay_h0")})
check(r.status_code == 200 and r.get_json()["status"] == "pending" and plan_of(h_org) == "free",
      "an unsettled payment grants nothing (the webhook finishes it)")
r = post(h_user, "/subscriptions/verify", good)
check(r.status_code == 200 and r.get_json()["status"] == "active", "the real verify succeeds")
check(plan_of(h_org) == "v2_growth", "verify grants the plan")
check(orders_for("pay_h1") == 1, "verify recorded exactly one paid order")
inv = sql("SELECT COUNT(*), MAX(total_amount_paise) FROM invoices WHERE order_id IN "
          "(SELECT id FROM orders WHERE razorpay_order_id='sub_charge_pay_h1')").first()
check(inv[0] == 1 and inv[1] == 1_200_000, "verify produced one invoice for the charged amount")
check(_pricing.compat_plan(A.get_effective_plan_for_user(h_user)) == "pro", "the owner's effective plan is now Growth")
check(_pricing.compat_plan(A.get_effective_plan_for_user(m_user)) == "pro", "so is a plain member's (they inherit the company plan)")
check(_pricing.compat_plan(A.get_effective_plan_for_user(x_user)) == "free", "a stranger's is untouched")

# The webhook for the SAME payment arrives after verify: nothing doubles.
charged = sub_event("subscription.charged", sid_h, payment={
    "id": "pay_h1", "status": "captured", "amount": 1_200_000, "order_id": "order_rp_h1"},
    current_start=epoch(0), current_end=epoch(30), paid_count=1)
check(webhook(charged).status_code == 200, "the webhook is accepted")
check(webhook(charged).status_code == 200, "and a redelivery too")
check(orders_for("pay_h1") == 1, "still exactly one order for that payment")
check(sql("SELECT COUNT(*) FROM invoices WHERE user_id=:u AND total_amount_paise=1200000", u=ad_user).scalar()
      + sql("SELECT COUNT(*) FROM invoices WHERE user_id=:u AND total_amount_paise=1200000", u=h_user).scalar() == 1,
      "still exactly one invoice")

# Webhook auth and shape.
check(webhook(charged, sig="00").status_code == 400, "a wrong signature is refused")
check(webhook(charged, secret="other").status_code == 400, "a signature from another secret is refused")
r = webhook(sub_event("subscription.charged", "sub_not_ours", payment={
    "id": "pay_zz", "status": "captured", "amount": 5}))
check(r.status_code == 200 and r.get_json()["status"] == "unknown_subscription", "an unknown subscription is acked, not retried")
check(sql("SELECT COUNT(*) FROM orders WHERE razorpay_order_id='sub_charge_pay_zz'").scalar() == 0,
      "…and creates nothing")
r = webhook({"event": "subscription.charged", "payload": {}})
check(r.status_code == 200 and r.get_json()["status"] == "ignored", "a malformed event is acked and ignored")
r = webhook(sub_event("subscription.charged", sid_h, payment={"id": "pay_h9", "status": "failed", "amount": 1}))
check(r.status_code == 200 and orders_for("pay_h9") == 0, "a non-captured payment records nothing")

# Renewal via webhook only (no browser): new payment -> new order, extended period.
ren = sub_event("subscription.charged", sid_h, payment={
    "id": "pay_h2", "status": "captured", "amount": 1_200_000, "order_id": "order_rp_h2"},
    current_start=epoch(30), current_end=epoch(60), paid_count=2, created_at=epoch(30))
check(webhook(ren).status_code == 200 and orders_for("pay_h2") == 1, "a renewal charge arrives by webhook alone")
check(sub_row(sid_h)["paid_count"] == 2, "paid_count follows Razorpay's")

# Failed renewal: halted, plan kept; an old halted redelivery can't undo a later recovery.
check(webhook(sub_event("subscription.halted", sid_h, status="halted", created_at=epoch(31))).status_code == 200,
      "halted webhook accepted")
check(sub_row(sid_h)["status"] == "halted" and plan_of(h_org) == "v2_growth", "halted recorded, plan kept")
check(S.public_view(sub_row(sid_h))["past_due"] is True, "the Billing view flags it past due")
rec = sub_event("subscription.charged", sid_h, payment={
    "id": "pay_h3", "status": "captured", "amount": 1_200_000}, current_start=epoch(33),
    current_end=epoch(63), paid_count=3, created_at=epoch(33))
webhook(rec)
check(sub_row(sid_h)["status"] == "active", "a successful retry reactivates it")
webhook(sub_event("subscription.halted", sid_h, status="halted", created_at=epoch(31)))
check(sub_row(sid_h)["status"] == "active", "a stale halted redelivery can't undo the recovery")

# Read view: owner/admin only, and never leaks Razorpay ids.
r = client_as(h_user).get(f"/api/billing/subscriptions?org_id={h_org}")
j = r.get_json()
check(r.status_code == 200 and j["subscription"]["tier"] == "growth" and j["subscription"]["status"] == "active",
      "the owner reads the subscription")
check("razorpay" not in json.dumps(j["subscription"]), "the view never exposes Razorpay ids")
check({o["tier"] for o in j["options"]} == {"starter", "growth"}, "options list only self-serve tiers")
check(client_as(m_user).get(f"/api/billing/subscriptions?org_id={h_org}").status_code == 403, "a member can't read it")
check(client_as(x_user).get(f"/api/billing/subscriptions?org_id={h_org}").status_code == 403, "a stranger can't read it")

# Already subscribed / managed directly.
check(post(h_user, "/subscriptions", {"tier": "starter"}).get_json()["error"] == "already_subscribed", "one live subscription")
sql("UPDATE organizations SET plan='v2_scale' WHERE id=:i", i=x_org)
check(post(x_user, "/subscriptions", {"tier": "growth"}).get_json()["error"] == "plan_managed_directly",
      "a company already on a higher hand-assigned plan is told to talk to us")

# Cancel: a stranger can't; the owner cancels at cycle end and keeps access until then.
check(post(x_user, "/subscriptions/cancel", {"org_id": h_org}).status_code == 403, "a stranger can't cancel")
check(post(m_user, "/subscriptions/cancel", {"org_id": h_org}).status_code == 403, "a member can't cancel")
check(FakeRP.cancels == [], "refused cancels never reached Razorpay")
r = post(h_user, "/subscriptions/cancel", {})
check(r.status_code == 200 and FakeRP.cancels[-1] == (sid_h, {"cancel_at_cycle_end": 1}),
      "an active subscription cancels at cycle end")
check(r.get_json()["subscription"]["cancel_at_period_end"] is True and plan_of(h_org) == "v2_growth",
      "access continues until the cycle ends")
webhook(sub_event("subscription.cancelled", sid_h, status="cancelled", current_end=epoch(63), created_at=epoch(40)))
check(sub_row(sid_h)["status"] == "cancelled" and plan_of(h_org) == "v2_growth",
      "cancelled with days left still keeps the plan")
check(S.sweep_ended(now=datetime.now(UTC) + timedelta(days=70)) >= 1 and plan_of(h_org) == "free",
      "the plan goes when the paid period does")
check(_pricing.compat_plan(A.get_effective_plan_for_user(m_user)) == "free", "and so does the member's inherited access")
check(post(h_user, "/subscriptions/cancel", {}).status_code == 404, "nothing left to cancel")

# Immediate cancel for a subscription that never got a paid cycle.
i_user, i_org = person("ira")
r = post(i_user, "/subscriptions", {"tier": "starter"})
sid_i = r.get_json()["razorpay_subscription_id"]
S.record_state(sid_i, status="authenticated", event_ts=S.ts(epoch(0)))
r = post(i_user, "/subscriptions/cancel", {})
check(FakeRP.cancels[-1] == (sid_i, {"cancel_at_cycle_end": 0}), "an unpaid subscription cancels immediately")
check(sub_row(sid_i)["status"] == "cancelled" and plan_of(i_org) == "free", "and ends with nothing granted or left over")

# Legacy /verify and payment.captured are untouched by any of this.
r = webhook({"event": "payment.captured", "payload": {"payment": {"entity": {"id": "pay_l", "order_id": "order_none"}}}})
check(r.status_code == 200 and r.get_json()["status"] == "unknown_order", "payment.captured still behaves as before")
r = webhook({"event": "refund.processed", "payload": {}})
check(r.status_code == 200 and r.get_json()["status"] == "ignored", "other events are still ignored")

print(f"OK — {passed} checks passed")

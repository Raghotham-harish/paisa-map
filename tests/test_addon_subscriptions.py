"""
test_addon_subscriptions.py — P5 round 3: self-serve extra seats / companies.

Each purchase is its own small monthly Razorpay subscription (owner decision
17 Oct 2026 — Razorpay can't change the amount of a UPI/eMandate subscription).

Two layers, one throwaway sqlite, no network:
  1. _addon_subs.py: extras count only after a real charge, one order per
     payment however it arrives, stale/ended events can't rewrite state, halted
     or expired extras stop counting, and they add to the seat/company limits.
  2. blueprints/billing.py: gates and who may buy, checkout amount, verify,
     webhook, cancel, and add-ons cancelled when the plan won't renew (but not
     while a failed renewal is being replaced).

    python3 tests/test_addon_subscriptions.py

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

os.environ["DATABASE_URL"] = os.environ.get("DATABASE_URL") or \
    "sqlite:///" + os.path.join(tempfile.mkdtemp(prefix="pm_addons_"), "addons.sqlite")
if "sqlite" not in os.environ["DATABASE_URL"]:
    raise SystemExit("Refusing to run: DATABASE_URL doesn't look like a throwaway sqlite file.")
if not os.environ.get("CUSTOMER_DATA_KEY"):
    from cryptography.fernet import Fernet
    os.environ["CUSTOMER_DATA_KEY"] = Fernet.generate_key().decode()
os.environ.setdefault("INVOICES_DIR", tempfile.mkdtemp(prefix="pm_invoices_"))
for k in ("BILLING_SUBSCRIPTIONS", "BILLING_LIVE_PURCHASES", "BILLING_SCOPE", "SEAT_LIMITS", "PAISAMAP_GSTIN"):
    os.environ.pop(k, None)
os.environ["RAZORPAY_KEY_ID"] = "rzp_test_addons"
os.environ["RAZORPAY_KEY_SECRET"] = "addons_secret"
os.environ["RAZORPAY_WEBHOOK_SECRET"] = "whsec_addons"

import _db
_db.enabled = lambda: False
import _auth_db as A
import _pricing
import _subscriptions as S
import _addon_subs as X
import _seats
from sqlalchemy import text
import razorpay

A.init_schema()
A.migrate_schema()
A._dispatch_link_notices = lambda n: None
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
    u = A.upsert_user(f"add-{tag}", f"{tag}@example.com", tag, None)
    org = A.create_default_organization_for_user(u["id"], f"{tag} Co")
    return u["id"], org["id"]


def epoch(days=0):
    return int((datetime.now(UTC) + timedelta(days=days)).timestamp())


def extras(org):
    with engine.connect() as conn:
        return X.entitled_extras(conn, org)


_n = [0]


def paid_plan(org, user, tier="growth"):
    """A plan subscription with one successful charge, as checkout + verify leave it."""
    _n[0] += 1
    sid = f"sub_plan_{_n[0]}"
    S.create_local(org, user, tier, "monthly", sid, f"plan_p{_n[0]}",
                   _pricing.subscription_amount_paise(tier, charge_gst=False))
    S.apply_charge(sid, f"pay_plan_{_n[0]}", _pricing.subscription_amount_paise(tier, charge_gst=False),
                   current_end=datetime.now(UTC) + timedelta(days=30))
    return sid


def local_addon(org, user, kind="seat", qty=2, tier="growth"):
    _n[0] += 1
    sid = f"sub_addon_{_n[0]}"
    unit = X.unit_paise(tier, kind)
    X.create_local(org, user, kind, qty, tier, unit, unit * qty, sid, f"plan_a{_n[0]}")
    return sid


# ═════════════════════════════════════════════════════════════════════════════
# 1. _addon_subs.py
# ═════════════════════════════════════════════════════════════════════════════
check(X.tables_ready(), "addon_subscriptions table is created by init_schema")
check(X.unit_paise("starter", "seat") == 90_000 and X.unit_paise("growth", "seat") == 90_000,
      "extra seat is Rs 900 on Starter and Growth (pricing doc §5)")
check(X.unit_paise("growth", "company") == 250_000, "extra company is Rs 2,500 on Growth")
check(X.unit_paise("scale", "seat") is None and X.unit_paise("pro", "company") is None,
      "Scale and Pro extras are assisted, not self-serve")
check(X.unit_paise("free", "seat") is None and X.unit_paise("nope", "seat") is None, "free / unknown: none")
for t in _pricing.TIERS:
    if _pricing.is_signal_tier(t):
        check(X.unit_paise(t, "seat") is None, f"signal-only plan {t}: no extras")

ua, oa = person("asha")
sid = local_addon(oa, ua, "seat", 2)
check(extras(oa) == (0, 0), "a created (unpaid) add-on gives nothing")
X.record_state(sid, status="authenticated", event_ts=datetime.now(UTC))
check(extras(oa) == (0, 0), "authenticated (mandate only, no charge) gives nothing")
X.record_state(sid, status="active")
check(extras(oa) == (0, 0), "a bare 'active' status with no charge gives nothing")
check(X.public_view(X.get_by_razorpay_id(sid))["counting"] is False, "…and the Billing page doesn't say it counts")
r = X.apply_charge(sid, "pay_x1", 180_000, current_end=datetime.now(UTC) + timedelta(days=30))
check(r["newly_paid"] and r["order"]["kind"] == "addon" and r["order"]["status"] == "paid",
      "first charge -> a paid 'addon' order")
check(extras(oa) == (2, 0), "after the charge: +2 seats")
r2 = X.apply_charge(sid, "pay_x1", 180_000)
check(not r2["newly_paid"] and r2["order"]["id"] == r["order"]["id"], "same payment again -> same order, nothing new")
check(sql("SELECT COUNT(*) FROM orders WHERE razorpay_order_id='sub_charge_pay_x1'").scalar() == 1,
      "exactly one order for one payment")
check(X.get_by_razorpay_id(sid)["paid_count"] == 1, "paid_count not bumped by a redelivery")
X.apply_charge(sid, "pay_x2", 180_000)
check(X.get_by_razorpay_id(sid)["paid_count"] == 2, "a second month's charge -> paid_count 2")
check(r["order"]["org_id"] == oa and r["order"]["billing_org_id"] == oa, "order billed to the paying company")

csid = local_addon(oa, ua, "company", 1)
X.apply_charge(csid, "pay_c1", 250_000)
check(extras(oa) == (2, 1), "seat and company add-ons add up separately")

# Stale and ended events.
t0 = datetime.now(UTC)
X.record_state(sid, status="pending", event_ts=t0)
check(extras(oa) == (2, 1), "pending (Razorpay still retrying) keeps the extras")
X.record_state(sid, status="halted", event_ts=t0 + timedelta(seconds=10))
check(extras(oa) == (0, 1), "halted (Razorpay gave up) -> the extras stop counting")
res = X.record_state(sid, status="active", event_ts=t0 + timedelta(seconds=5))
check(res.get("stale") and X.get_by_razorpay_id(sid)["status"] == "halted", "an older event can't rewrite newer state")
X.apply_charge(sid, "pay_x3", 180_000, event_ts=t0 + timedelta(seconds=20))
check(extras(oa) == (2, 1), "a charge after halted brings them back")
X.record_state(sid, status="cancelled", event_ts=t0 + timedelta(seconds=30),
               current_end=datetime.now(UTC) + timedelta(days=10))
check(extras(oa) == (2, 1), "cancelled: keeps counting to the end of the paid month")
res = X.record_state(sid, status="active", event_ts=t0 + timedelta(seconds=40))
check(res.get("ignored_status") == "active" and X.get_by_razorpay_id(sid)["status"] == "cancelled",
      "an ended add-on is never resurrected")
sql("UPDATE addon_subscriptions SET ends_at=:e WHERE razorpay_subscription_id=:s",
    e=datetime.now(UTC) - timedelta(minutes=1), s=sid)
check(extras(oa) == (0, 1), "after the paid month: no longer counted")
check(len([a for a in X.list_for(oa) if a["kind"] == "seat"]) == 0, "an expired add-on drops off the list")
check(X.record_state("sub_nope", status="active") == {"error": "unknown_addon"}, "unknown add-on")
check(X.apply_charge("sub_nope", "p", 1) == {"error": "unknown_addon"}, "charge for an unknown add-on")
check(X.line_item({"kind": "seat", "quantity": 2}) == "Extra seats × 2 — monthly", "invoice line, plural")
check(X.line_item({"kind": "company", "quantity": 1}) == "Extra company × 1 — monthly", "invoice line, singular")

# Limits: tier + hand-set wallet_addons + paid add-ons.
os.environ["SEAT_LIMITS"] = "1"
ub, ob = person("bina")
sql("UPDATE organizations SET plan='v2_growth' WHERE id=:o", o=ob)
check(_seats.view_for(ob)["seats"] == 6, "Growth: 6 seats before any extras")
bsid = local_addon(ob, ub, "seat", 3)
X.apply_charge(bsid, "pay_b1", 270_000)
sql("INSERT INTO wallet_addons (wallet_org_id, extra_seats, extra_companies, updated_at) "
    "VALUES (:o, 1, 2, CURRENT_TIMESTAMP)", o=ob)
v = _seats.view_for(ob)
check(v["seats"] == 10 and v["extra_seats"] == 4, "6 + 1 hand-set + 3 bought = 10 seats")
check(v["companies"] == 3 and v["extra_companies"] == 2, "1 + 2 hand-set companies")
# A full account can add someone once the add-on is paid.
uc, oc = person("chitra")
sql("UPDATE organizations SET plan='v2_starter' WHERE id=:o", o=oc)
for i in range(2):
    A.create_or_resend_org_invite(oc, uc, f"c{i}@example.com", "member")
with engine.connect() as conn:
    err = _seats.check_new_seat(conn, oc, email="new@example.com")
check(err and err["error"] == "seat_limit" and err["limit"] == 3, "Starter full at 3 (owner + 2 invites)")
csid2 = local_addon(oc, uc, "seat", 1, tier="starter")
with engine.connect() as conn:
    check(_seats.check_new_seat(conn, oc, email="new@example.com") is not None, "unpaid add-on doesn't open a seat")
X.apply_charge(csid2, "pay_cs1", 90_000)
with engine.connect() as conn:
    check(_seats.check_new_seat(conn, oc, email="new@example.com") is None, "paid add-on opens one seat")
os.environ.pop("SEAT_LIMITS")

# ═════════════════════════════════════════════════════════════════════════════
# 2. HTTP
# ═════════════════════════════════════════════════════════════════════════════


class _Plan:
    def create(self, data):
        FakeRP.plan_creates.append(data)
        return {"id": f"plan_rp_{len(FakeRP.plan_creates)}"}


class _Subscription:
    def create(self, data):
        FakeRP.sub_creates.append(data)
        return {"id": f"sub_rp_{len(FakeRP.sub_creates)}"}

    def fetch(self, sid):
        return FakeRP.sub_entities[sid]

    def cancel(self, sid, data):
        if FakeRP.fail_cancel:
            raise RuntimeError("razorpay down")
        FakeRP.cancels.append((sid, data))
        return {"id": sid}


class _Payment:
    def fetch(self, pid):
        return FakeRP.payments[pid]


class FakeRP:
    plan_creates = []
    sub_creates = []
    cancels = []
    fail_cancel = False
    sub_entities = {}
    payments = {}
    plan = _Plan()
    subscription = _Subscription()
    payment = _Payment()
    utility = razorpay.Client(auth=("rzp_test_addons", "addons_secret")).utility


B._client = lambda: FakeRP()


def client_as(uid):
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = uid
    return c


def post(uid, path, body):
    return client_as(uid).post(f"/api/billing{path}", json=body)


def get_subs(uid, org):
    return client_as(uid).get(f"/api/billing/subscriptions?org_id={org}").get_json()


def sign(sid, pid):
    return hmac.new(b"addons_secret", f"{pid}|{sid}".encode(), hashlib.sha256).hexdigest()


def webhook(payload):
    raw = json.dumps(payload)
    sig = hmac.new(b"whsec_addons", raw.encode(), hashlib.sha256).hexdigest()
    return app.test_client().post("/api/billing/webhook", data=raw,
                                  headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"})


def sub_event(event, sid, *, status="active", payment=None, created_at=None, **entity):
    ent = {"id": sid, "status": status, **entity}
    body = {"event": event, "created_at": created_at or epoch(0), "payload": {"subscription": {"entity": ent}}}
    if payment:
        body["payload"]["payment"] = {"entity": payment}
    return body


def buy(uid, org, kind="seat", qty=2, **extra):
    return post(uid, "/addons", {"kind": kind, "quantity": qty, "org_id": org, **extra})


hu, ho = person("hari")                    # owner
mu, _ = person("meena")
A.add_org_member(ho, hu, "meena@example.com", "member")
du, _ = person("deepa")
A.add_org_member(ho, hu, "deepa@example.com", "admin")
xu, xo = person("xavi")                    # stranger

# Gates, in order.
check(get_subs(hu, ho)["addon_offer"]["reason"] == "not_enabled", "dark by default")
check(buy(hu, ho).status_code == 403 and not FakeRP.sub_creates, "refused while dark, Razorpay untouched")
os.environ["BILLING_SUBSCRIPTIONS"] = "1"
os.environ["BILLING_SCOPE"] = "wallet"
check(get_subs(hu, ho)["addon_offer"]["reason"] == "limits_off", "extras mean nothing while SEAT_LIMITS is off")
check(buy(hu, ho).status_code == 403, "refused with limits off")
os.environ["SEAT_LIMITS"] = "1"
_real_ready = X.tables_ready
X.tables_ready = lambda conn=None: False
check(get_subs(hu, ho)["addon_offer"]["reason"] == "schema_not_applied", "no table -> can't buy")
check(get_subs(hu, ho)["addons"] == [], "no table -> empty list, not a 500")
check(buy(hu, ho).status_code == 403 and not FakeRP.sub_creates, "no table -> refused before Razorpay")
check(post(hu, "/addons/1/cancel", {}).status_code == 404, "no table -> cancel finds nothing")
X.tables_ready = _real_ready
r = buy(hu, ho)
check(r.status_code == 409 and r.get_json()["reason"] == "no_paid_plan", "no plan subscription -> can't buy extras")
S.create_local(ho, hu, "growth", "monthly", "sub_unpaid_plan", "plan_u", 1_200_000)
S.record_state("sub_unpaid_plan", status="authenticated")
check(buy(hu, ho).get_json()["reason"] == "no_paid_plan", "plan authorised but never charged -> still no extras")
S.record_state("sub_unpaid_plan", status="cancelled")

plan_sid = paid_plan(ho, hu, "growth")
offer = get_subs(hu, ho)["addon_offer"]
check(offer["available"] and offer["prices"] == {"seat": 90_000, "company": 250_000}, "Growth offer: Rs 900 / Rs 2,500")

# Input validation and who may buy.
for bad, why in ((0, "zero"), (51, "over the max"), (True, "a bool"), ("2", "a string"), (None, "missing")):
    check(buy(hu, ho, qty=bad).status_code == 400, f"quantity {why} rejected")
check(buy(hu, ho, kind="viewer").status_code == 400, "unknown kind rejected")
check(buy(mu, ho).status_code == 403, "a member can't buy for the company")
check(buy(xu, ho).status_code == 403, "a stranger can't buy for it")
check(not FakeRP.sub_creates, "no refused request reached Razorpay")

# Checkout.
r = buy(du, ho, "seat", 2)
body = r.get_json()
check(r.status_code == 201 and body["amount_paise"] == 180_000, "admin buys 2 seats: Rs 1,800 (no GSTIN -> no GST)")
check(FakeRP.plan_creates[-1]["item"]["amount"] == 180_000 and FakeRP.plan_creates[-1]["period"] == "monthly",
      "a monthly Razorpay plan for exactly that amount")
check(FakeRP.sub_creates[-1]["notes"]["addon"] == "seat" and FakeRP.sub_creates[-1]["notes"]["org_id"] == str(ho),
      "subscription notes say what it is and for whom")
check("razorpay_subscription_id" not in json.dumps(body["addon"]), "public view hides Razorpay ids")
a_sid = body["razorpay_subscription_id"]
check(extras(ho) == (0, 0), "nothing counted at checkout")
n_plans = len(FakeRP.plan_creates)
buy(hu, ho, "seat", 2)
check(len(FakeRP.plan_creates) == n_plans, "same kind/tier/amount reuses the cached Razorpay plan")
check(sql("SELECT status FROM addon_subscriptions WHERE razorpay_subscription_id=:s", s=a_sid).scalar() == "abandoned",
      "an unpaid earlier seat checkout is marked abandoned")
a_sid = FakeRP.sub_creates[-1] and f"sub_rp_{len(FakeRP.sub_creates)}"
r = buy(hu, ho, "company", 1)
check(X.get_by_razorpay_id(a_sid)["status"] == "created",
      "starting a company checkout doesn't abandon the open seat checkout")

# Verify.
FakeRP.payments["pay_v1"] = {"id": "pay_v1", "amount": 180_000, "status": "captured"}
FakeRP.sub_entities[a_sid] = {"id": a_sid, "paid_count": 0, "current_end": epoch(30)}
r = post(hu, "/subscriptions/verify", {"razorpay_subscription_id": a_sid, "razorpay_payment_id": "pay_v1",
                                       "razorpay_signature": "bad"})
check(r.status_code == 400, "bad signature rejected")
r = post(hu, "/subscriptions/verify", {"razorpay_subscription_id": a_sid, "razorpay_payment_id": "pay_v1",
                                       "razorpay_signature": sign(a_sid, "pay_v1")})
check(r.get_json()["status"] == "pending" and extras(ho) == (0, 0),
      "paid_count 0 (mandate authentication only) -> pending, nothing counted")
FakeRP.sub_entities[a_sid]["paid_count"] = 1
r = post(mu, "/subscriptions/verify", {"razorpay_subscription_id": a_sid, "razorpay_payment_id": "pay_v1",
                                       "razorpay_signature": sign(a_sid, "pay_v1")})
check(r.status_code == 404, "a member can't verify the company's add-on")
FakeRP.payments["pay_v1"]["status"] = "authorized"
r = post(hu, "/subscriptions/verify", {"razorpay_subscription_id": a_sid, "razorpay_payment_id": "pay_v1",
                                       "razorpay_signature": sign(a_sid, "pay_v1")})
check(r.get_json()["status"] == "pending" and extras(ho) == (0, 0), "not captured yet -> pending")
FakeRP.payments["pay_v1"]["status"] = "captured"
r = post(hu, "/subscriptions/verify", {"razorpay_subscription_id": a_sid, "razorpay_payment_id": "pay_v1",
                                       "razorpay_signature": sign(a_sid, "pay_v1")})
check(r.status_code == 200 and r.get_json()["status"] == "active" and extras(ho) == (2, 0), "verified -> +2 seats")
order_id = sql("SELECT id FROM orders WHERE razorpay_order_id='sub_charge_pay_v1'").scalar()
inv = sql("SELECT line_item_label, total_amount_paise FROM invoices WHERE order_id=:o", o=order_id).first()
check(inv is not None and inv[0] == "Extra seats × 2 — monthly" and inv[1] == 180_000, "an invoice for the charge")
check(_seats.view_for(ho)["seats"] == 8, "Growth 6 + 2 bought = 8 seats")
check(get_subs(hu, ho)["addons"][0]["counting"] is True, "listed on the Billing payload as counting")

# The webhook for the same payment changes nothing; a renewal adds a month.
_real_inv = B._create_invoice_for_order
inv_calls = []
B._create_invoice_for_order = lambda o, li: (inv_calls.append(o["id"]), _real_inv(o, li))
webhook(sub_event("subscription.charged", a_sid, paid_count=1, current_end=epoch(30),
                  payment={"id": "pay_v1", "amount": 180_000, "status": "captured"}))
check(inv_calls == [], "a redelivered charge never even tries to invoice again (Postgres would burn an invoice number)")
B._create_invoice_for_order = _real_inv
check(sql("SELECT COUNT(*) FROM orders WHERE razorpay_order_id='sub_charge_pay_v1'").scalar() == 1,
      "webhook after verify -> still one order")
check(sql("SELECT COUNT(*) FROM invoices WHERE order_id=:o", o=order_id).scalar() == 1, "…and one invoice")
webhook(sub_event("subscription.charged", a_sid, paid_count=2, current_end=epoch(60), created_at=epoch(0) + 5,
                  payment={"id": "pay_v2", "amount": 180_000, "status": "captured"}))
check(X.get_by_razorpay_id(a_sid)["paid_count"] == 2, "renewal via webhook recorded")
webhook(sub_event("subscription.charged", a_sid, paid_count=3,
                  payment={"id": "pay_v3", "amount": 180_000, "status": "failed"}))
check(sql("SELECT COUNT(*) FROM orders WHERE razorpay_order_id='sub_charge_pay_v3'").scalar() == 0,
      "an uncaptured payment in a webhook records nothing")
webhook(sub_event("subscription.halted", a_sid, status="halted", created_at=epoch(0) + 10))
check(extras(ho) == (0, 0), "halted via webhook -> extras stop counting")
webhook(sub_event("subscription.charged", a_sid, paid_count=3, created_at=epoch(0) + 20,
                  payment={"id": "pay_v4", "amount": 180_000, "status": "captured"}))
check(extras(ho) == (2, 0), "a later successful charge restores them")
check(webhook(sub_event("subscription.activated", "sub_unknown")).get_json()["status"] == "unknown_subscription",
      "an unknown subscription is acked, not an error")

# Cancel one add-on.
aid = X.get_by_razorpay_id(a_sid)["id"]
check(post(mu, f"/addons/{aid}/cancel", {}).status_code == 404, "a member can't cancel")
check(post(xu, f"/addons/{aid}/cancel", {}).status_code == 404, "a stranger can't (and can't tell it exists)")
r = post(hu, f"/addons/{aid}/cancel", {})
check(r.status_code == 200 and FakeRP.cancels[-1] == (a_sid, {"cancel_at_cycle_end": 1}),
      "a paid-up add-on is cancelled at the end of its month")
check(extras(ho) == (2, 0) and r.get_json()["addon"]["cancel_at_period_end"], "…and keeps counting until then")
check(post(hu, f"/addons/{aid}/cancel", {}).status_code == 409, "cancelling twice -> 409")
check(post(hu, "/addons/999999/cancel", {}).status_code == 404, "unknown id -> 404")

# Plan states that block a new purchase.
sql("UPDATE subscriptions SET cancel_at_period_end=1 WHERE razorpay_subscription_id=:s", s=plan_sid)
check(buy(hu, ho).get_json()["reason"] == "plan_cancelling", "plan set to cancel -> no new extras")
sql("UPDATE subscriptions SET cancel_at_period_end=0, past_due_since=:t WHERE razorpay_subscription_id=:s",
    t=datetime.now(UTC), s=plan_sid)
check(buy(hu, ho).get_json()["reason"] == "unpaid_balance", "failed renewal unpaid -> no new extras")
sql("UPDATE subscriptions SET past_due_since=NULL WHERE razorpay_subscription_id=:s", s=plan_sid)
su, so = person("sanjay")
ssid = f"sub_scale_{so}"
S.create_local(so, su, "scale", "monthly", ssid, "plan_scale", 3_000_000)
S.apply_charge(ssid, f"pay_scale_{so}", 3_000_000)
check(buy(su, so).get_json()["reason"] == "assisted_tier", "Scale: extras are arranged with PaisaMap")

# Cancelling the plan stops the add-ons renewing.
r = buy(hu, ho, "company", 1)
c_sid = r.get_json()["razorpay_subscription_id"]
check(r.get_json()["amount_paise"] == 250_000, "one extra company: Rs 2,500")
X.apply_charge(c_sid, "pay_co1", 250_000, current_end=datetime.now(UTC) + timedelta(days=30))
FakeRP.cancels.clear()
r = post(hu, "/subscriptions/cancel", {"org_id": ho})
check(r.status_code == 200, "plan cancel works")
check((c_sid, {"cancel_at_cycle_end": 1}) in FakeRP.cancels, "…and the company add-on stops renewing too")
check(X.get_by_razorpay_id(c_sid)["cancel_at_period_end"], "recorded as cancelling")
check(extras(ho) == (2, 1), "paid-up extras still count to the end of their month")

# Orphans: plan ended at Razorpay (no cancel through us) -> add-ons cancelled.
ou, oo = person("om")
o_plan = paid_plan(oo, ou)
o_add = local_addon(oo, ou, "seat", 1)
X.apply_charge(o_add, "pay_o1", 90_000)
X.record_state(o_add, status="active")
FakeRP.cancels.clear()
webhook(sub_event("subscription.cancelled", o_plan, status="cancelled"))
check((o_add, {"cancel_at_cycle_end": 1}) in FakeRP.cancels, "plan cancelled at Razorpay -> add-on cancelled")
check(X.orphan_wallets() == [] or oo not in X.orphan_wallets(), "no orphan left after the sweep")
# Plan set to cancel at period end outside our cancel route -> its add-ons stop renewing.
qu, qo = person("qadir")
q_plan = paid_plan(qo, qu)
q_add = local_addon(qo, qu, "seat", 1)
X.apply_charge(q_add, "pay_q1", 90_000)
check(qo not in X.orphan_wallets(), "plan renewing -> add-on is not an orphan")
S.record_state(q_plan, cancel_at_period_end=True)
check(qo in X.orphan_wallets(), "plan won't renew -> add-on is an orphan")
X.cancel_orphans(FakeRP())
check(X.get_by_razorpay_id(q_add)["cancel_at_period_end"], "…and is set to stop at the end of its month")
# A Razorpay failure is survivable and retried.
pu, po = person("priya")
p_plan = paid_plan(po, pu)
p_add = local_addon(po, pu, "seat", 1)
X.apply_charge(p_add, "pay_p1", 90_000)
FakeRP.fail_cancel = True
r = webhook(sub_event("subscription.cancelled", p_plan, status="cancelled"))
check(r.status_code == 200, "a failed Razorpay cancel never fails the webhook")
check(po in X.orphan_wallets(), "…the add-on is still an orphan, for the next run")
FakeRP.fail_cancel = False
check(X.cancel_orphans(FakeRP()) >= 1 and po not in X.orphan_wallets(), "the next sweep cancels it")

# Fixing a failed renewal must NOT cancel the add-ons.
ru, ro = person("ravi")
r_plan = paid_plan(ro, ru)
r_add = local_addon(ro, ru, "seat", 1)
X.apply_charge(r_add, "pay_r1", 90_000)
X.record_state(r_add, status="active")
sql("UPDATE subscriptions SET status='halted', past_due_since=:t WHERE razorpay_subscription_id=:s",
    t=datetime.now(UTC), s=r_plan)
FakeRP.cancels.clear()
r = post(ru, "/subscriptions/recover", {"org_id": ro})
check(r.status_code == 201, "recover starts a replacement")
webhook(sub_event("subscription.cancelled", r_plan, status="cancelled", created_at=epoch(0) + 30))
check(not any(c[0] == r_add for c in FakeRP.cancels), "replacement pending -> add-on kept")
sql("UPDATE subscriptions SET created_at=:t WHERE org_id=:o AND status='created'",
    t=datetime.now(UTC) - timedelta(days=3), o=ro)
check(ro in X.orphan_wallets(), "replacement abandoned for > 2 days -> now an orphan")

# Schema script exists and is idempotent on sqlite (table already there).
import subprocess
out = subprocess.run([sys.executable, os.path.join(REPO, "paisamap-etl", "db", "apply_addon_subscriptions.py")],
                     capture_output=True, text=True, env=os.environ)
check(out.returncode == 0 and "already there: addon_subscriptions" in out.stdout, "apply script: idempotent")
check("sqlite" not in out.stdout and "postgres" not in out.stdout.lower().replace("not postgres", ""),
      "apply script prints no connection string")

print(f"OK — {passed} checks passed")

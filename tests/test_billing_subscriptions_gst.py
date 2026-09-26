"""
test_billing_subscriptions_gst.py — subscriptions go through the GST invoice
engine (P12) exactly like one-off orders.

  price       -> list price ex-GST; charged = list + 18% only when a GSTIN is
                 set (Growth: Rs 12,000 -> Rs 14,160); the Razorpay plan is
                 created for exactly that amount
  start       -> optional billing details, validated like checkout; the split
                 and the buyer are frozen on subscriptions.meta
  each charge -> its order copies the frozen split + buyer, so the invoice
                 shows CGST+SGST (Karnataka / nothing given) or IGST (other
                 states), the buyer's GSTIN and name; a charge of a different
                 amount falls back to the carve-out instead of wrong heads
  recover     -> the replacement subscription keeps the buyer details

    python3 tests/test_billing_subscriptions_gst.py

Plain script, throwaway sqlite, same conventions as the other suites.
"""

import hashlib
import hmac
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "paisamap-etl", "etl"))

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='pm_subgst_')}/subgst.sqlite")
if "sqlite" not in os.environ["DATABASE_URL"]:
    raise SystemExit("Refusing to run: DATABASE_URL doesn't look like a throwaway sqlite file.")
if not os.environ.get("CUSTOMER_DATA_KEY"):
    from cryptography.fernet import Fernet
    os.environ["CUSTOMER_DATA_KEY"] = Fernet.generate_key().decode()
os.environ.setdefault("INVOICES_DIR", tempfile.mkdtemp(prefix="pm_invoices_"))
os.environ.pop("BILLING_LIVE_PURCHASES", None)
os.environ["BILLING_SUBSCRIPTIONS"] = "1"
os.environ["BILLING_SCOPE"] = "wallet"
os.environ["RAZORPAY_KEY_ID"] = "rzp_test_subgst"
os.environ["RAZORPAY_KEY_SECRET"] = "subgst_secret"
os.environ["RAZORPAY_WEBHOOK_SECRET"] = "whsec_subgst"
os.environ.pop("PAISAMAP_GSTIN", None)

import reportlab.rl_config
reportlab.rl_config.pageCompression = 0   # so the PDF text can be read back

import _db
_db.enabled = lambda: False
import _auth_db as A
import _pricing
import _subscriptions as S

A.init_schema()
A.migrate_schema()

import server
from blueprints import billing as B

app = server.app
app.testing = True
UTC = timezone.utc
SELLER_GSTIN = "29BJUPR8491Q1ZI"
KA_GSTIN = "29AAGCB7383J1Z4"
MH_GSTIN = "27AAPFU0939F1ZV"
passed = 0


def check(cond, msg):
    global passed
    assert cond, f"FAIL: {msg}"
    passed += 1


def epoch(days=0):
    return int((datetime.now(UTC) + timedelta(days=days)).timestamp())


class _Plan:
    def create(self, data):
        FakeRP.plans.append(data)
        return {"id": f"plan_rp_{len(FakeRP.plans)}"}


class _Subscription:
    def create(self, data):
        FakeRP.subs.append(data)
        return {"id": f"sub_rp_{len(FakeRP.subs)}"}

    def cancel(self, sid, data):
        return {"id": sid}


class _Order:
    def create(self, data):
        FakeRP.orders.append(data)
        return {"id": f"order_rp_{len(FakeRP.orders)}"}


class FakeRP:
    plans, subs, orders = [], [], []
    plan = _Plan()
    subscription = _Subscription()
    order = _Order()


B._client = lambda: FakeRP()

_people = [0]


def person():
    _people[0] += 1
    t = f"p{_people[0]}"
    u = A.upsert_user(f"subgst-{t}", f"{t}@example.com", f"Person {t}", None)["id"]
    org = A.create_default_organization_for_user(u, f"Company {t}")["id"]
    return u, org


def start(uid, body):
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = uid
    return c.post("/api/billing/subscriptions", json={"tier": "growth", **body})


def charge(sid, pid, amount):
    raw = json.dumps({"event": "subscription.charged", "created_at": epoch(0), "payload": {
        "subscription": {"entity": {"id": sid, "status": "active", "current_start": epoch(0),
                                    "current_end": epoch(30), "paid_count": 1}},
        "payment": {"entity": {"id": pid, "status": "captured", "amount": amount}}}})
    sig = hmac.new(b"whsec_subgst", raw.encode(), hashlib.sha256).hexdigest()
    r = app.test_client().post("/api/billing/webhook", data=raw,
                               headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"})
    assert r.status_code == 200, r.get_data(as_text=True)
    order = A.get_order_by_razorpay_id(f"sub_charge_{pid}")
    inv = [i for i in A.list_invoices(order["user_id"]) if i["order_id"] == order["id"]]
    assert len(inv) == 1, inv
    return order, inv[0]


def pdf_text(inv):
    raw = Path(inv["file_path"]).read_bytes().decode("latin-1").replace(") Tj (", "")
    return raw.replace("\\(", "(").replace("\\)", ")")


# ── pricing ──────────────────────────────────────────────────────────────────
check(_pricing.subscription_amount_paise("growth", charge_gst=True) == 1_416_000, "Growth + GST = Rs 14,160")
check(_pricing.subscription_amount_paise("starter", charge_gst=True) == 590_000, "Starter + GST = Rs 5,900")
check(_pricing.subscription_amount_paise("growth", charge_gst=False) == 1_200_000, "unregistered: Rs 12,000")

# ── registered: Maharashtra business -> IGST ─────────────────────────────────
os.environ["PAISAMAP_GSTIN"] = SELLER_GSTIN
u1, o1 = person()
r = start(u1, {"billing": {"name": "Mumbai Foods LLP", "gstin": MH_GSTIN, "address": "Andheri, Mumbai"}})
check(r.status_code == 201, f"subscription started: {r.status_code} {r.get_data(as_text=True)[:120]}")
check(r.get_json()["amount_paise"] == 1_416_000, "Checkout is told Rs 14,160")
check(FakeRP.plans[-1]["item"]["amount"] == 1_416_000, "the Razorpay plan charges Rs 14,160")
sid1 = r.get_json()["razorpay_subscription_id"]
sub1 = S.get_by_razorpay_id(sid1)
check(sub1["amount_paise"] == 1_416_000, "local subscription records the charged amount")
tax = sub1["meta"]["tax"]
check((tax["taxable_paise"], tax["gst_paise"], tax["igst_paise"], tax["cgst_paise"], tax["place_of_supply"])
      == (1_200_000, 216_000, 216_000, 0, "27"), f"frozen: IGST for Maharashtra, {tax}")
check(sub1["meta"]["buyer"]["gstin"] == MH_GSTIN, "frozen: buyer GSTIN")

order, inv = charge(sid1, "pay_s1a", 1_416_000)
check(order["meta"]["tax"]["igst_paise"] == 216_000 and order["meta"]["buyer"]["gstin"] == MH_GSTIN,
      "the charge's order carries the split + buyer")
check(order["meta"]["subscription_id"] == sid1, "and still says which subscription it came from")
check((inv["taxable_amount_paise"], inv["gst_amount_paise"], inv["total_amount_paise"]) == (1_200_000, 216_000, 1_416_000),
      "invoice: 12,000 + 2,160 = 14,160")
check(inv["buyer_gstin"] == MH_GSTIN and inv["buyer_name"] == "Mumbai Foods LLP", "invoice issued to the business")
t = pdf_text(inv)
check("IGST @ 18%" in t and "CGST" not in t and "Place of supply: Maharashtra (27)" in t, "PDF: IGST, Maharashtra")
check("Growth plan" in t and "998439" in t and "Andheri, Mumbai" in t, "PDF: line, SAC, buyer address")

# the next month's renewal gets the same treatment
_, inv2 = charge(sid1, "pay_s1b", 1_416_000)
check("IGST @ 18%" in pdf_text(inv2) and inv2["buyer_gstin"] == MH_GSTIN, "a renewal invoice keeps the same heads + buyer")

# a charge for an amount it wasn't frozen for: no heads that don't add up
odd_order, inv3 = charge(sid1, "pay_s1odd", 1_200_000)
check("tax" not in odd_order["meta"], "odd amount: the order doesn't record a split it never charged")
check(odd_order["meta"]["buyer"]["gstin"] == MH_GSTIN, "odd amount: but it still knows the buyer")
check(inv3["taxable_amount_paise"] + inv3["gst_amount_paise"] == 1_200_000, "odd amount: parts still add up to what was paid")
check(inv3["taxable_amount_paise"] == round(1_200_000 / 1.18), "odd amount: GST carved out, like a legacy order")

# ── registered: consumer, no details -> CGST + SGST ─────────────────────────
u2, o2 = person()
r = start(u2, {})
sid2 = r.get_json()["razorpay_subscription_id"]
check(S.get_by_razorpay_id(sid2)["meta"]["tax"]["cgst_paise"] == 108_000, "no details: Karnataka, CGST 1,080")
_, inv = charge(sid2, "pay_s2", 1_416_000)
t = pdf_text(inv)
check("CGST @ 9%" in t and "SGST @ 9%" in t and "IGST" not in t, "PDF: CGST + SGST")
check(inv["buyer_gstin"] is None and inv["buyer_name"] == "Person p2", "consumer invoice to the account name")

# ── omitted billing reuses the company's last details; explicit bad ones are refused ──
u3, o3 = person()
with app.test_request_context():       # an earlier credit purchase that gave details
    _, err = B._create_razorpay_order_and_local_row(
        u3, "credit_pack", 79_900, buyer={"name": "Acme Retail", "gstin": KA_GSTIN, "state_code": "29", "address": None},
        credit_pack_id="pack_500", org_id=o3, billing_org_id=o3)
check(err is None, f"setup: earlier order created ({err})")
r = start(u3, {})
check(r.status_code == 201, "started with details omitted")
check(S.get_by_razorpay_id(r.get_json()["razorpay_subscription_id"])["meta"]["buyer"]["gstin"] == KA_GSTIN,
      "omitted -> the company's last-given details are reused")

u4, o4 = person()
before = len(FakeRP.subs)
for billing, code in (({"name": "A", "gstin": "27AAPFU0939F1ZW"}, "invalid_gstin"),
                      ({"gstin": MH_GSTIN}, "gstin_needs_name"),
                      ({"state_code": "99"}, "invalid_state")):
    r = start(u4, {"billing": billing})
    check(r.status_code == 400 and r.get_json()["error"] == code, f"{billing} -> 400 {code}")
check(len(FakeRP.subs) == before and S.get_latest(o4) is None, "refused: no Razorpay call, no local subscription")

# ── recover keeps the buyer ─────────────────────────────────────────────────
from sqlalchemy import text
with A._require_engine().begin() as conn:
    conn.execute(text("UPDATE subscriptions SET status='pending', past_due_since=:t WHERE razorpay_subscription_id=:s"),
                 {"t": datetime.now(UTC) - timedelta(days=1), "s": sid1})
c = app.test_client()
with c.session_transaction() as sess:
    sess["user_id"] = u1
r = c.post("/api/billing/subscriptions/recover", json={})
check(r.status_code == 201, f"recover: {r.status_code} {r.get_data(as_text=True)[:120]}")
new = S.get_by_razorpay_id(r.get_json()["razorpay_subscription_id"])
check(new["meta"]["buyer"]["gstin"] == MH_GSTIN and new["meta"]["tax"]["igst_paise"] == 216_000,
      "the replacement keeps the buyer and its IGST split")
check(r.get_json()["amount_paise"] == 1_416_000 == new["amount_paise"], "and charges the same Rs 14,160")

# ── unregistered: list price, plain invoice, its own Razorpay plan ───────────
os.environ.pop("PAISAMAP_GSTIN")
u5, o5 = person()
plans_before = len(FakeRP.plans)
r = start(u5, {"billing": {"name": "Mumbai Foods LLP", "gstin": MH_GSTIN}})
check(r.get_json()["amount_paise"] == 1_200_000, "unregistered: Checkout is told Rs 12,000")
check(len(FakeRP.plans) == plans_before + 1 and FakeRP.plans[-1]["item"]["amount"] == 1_200_000,
      "a separate Razorpay plan for the ex-GST amount (plans are cached by amount)")
_, inv = charge(r.get_json()["razorpay_subscription_id"], "pay_s5", 1_200_000)
t = pdf_text(inv)
check(inv["gst_amount_paise"] == 0 and "Tax Invoice" not in t and "IGST" not in t, "unregistered: no GST on the invoice")

# the registered plan is reused, not recreated, for the next registered start
os.environ["PAISAMAP_GSTIN"] = SELLER_GSTIN
u6, _ = person()
plans_before = len(FakeRP.plans)
start(u6, {})
check(len(FakeRP.plans) == plans_before, "the cached Rs 14,160 plan is reused")

# the options list shows both numbers
c = app.test_client()
with c.session_transaction() as sess:
    sess["user_id"] = u6
opts = {o["tier"]: o for o in c.get("/api/billing/subscriptions").get_json()["options"]}
check(opts["growth"]["list_paise"] == 1_200_000 and opts["growth"]["amount_paise"] == 1_416_000,
      "options: list price and what is charged")

os.environ.pop("PAISAMAP_GSTIN")
print(f"OK — {passed} checks passed")

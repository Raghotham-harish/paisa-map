"""
test_billing_live_guard.py — live Razorpay keys must never take money at the
placeholder prices by accident.

The prices in _pricing.py's legacy block (Pro Rs 999, Team Rs 2,999, the credit
packs, the Rs 149 report) are placeholders. Razorpay's mode is nothing but the
`rzp_test_` / `rzp_live_` prefix of RAZORPAY_KEY_ID, so the moment someone puts
real keys in db.env those placeholders would start charging real money. Rules:

  test keys  -> everything purchasable, exactly as before
  live keys  -> legacy plan checkout is never purchasable; credit packs and the
                one-off report only when BILLING_LIVE_PURCHASES is exactly "1"

    DATABASE_URL="sqlite:////tmp/billing_live_guard.sqlite" \
      python3 tests/test_billing_live_guard.py

Plain script, throwaway sqlite, same conventions as the other suites.
"""

import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "paisamap-etl", "etl"))

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/billing_live_guard_default.sqlite")
if "sqlite" not in os.environ["DATABASE_URL"]:
    raise SystemExit("Refusing to run: DATABASE_URL doesn't look like a throwaway sqlite file.")
if not os.environ.get("CUSTOMER_DATA_KEY"):
    from cryptography.fernet import Fernet
    os.environ["CUSTOMER_DATA_KEY"] = Fernet.generate_key().decode()
os.environ.setdefault("INVOICES_DIR", tempfile.mkdtemp(prefix="pm_invoices_"))
os.environ.pop("BILLING_LIVE_PURCHASES", None)

import _auth_db
_auth_db.init_schema()
_auth_db.migrate_schema()

import server
from blueprints import billing as B

app = server.app
app.testing = True

passed = 0


def check(cond, msg):
    global passed
    assert cond, f"FAIL: {msg}"
    passed += 1


class _FakeOrders:
    calls = 0

    def create(self, data):
        _FakeOrders.calls += 1
        return {"id": f"order_guard_{_FakeOrders.calls}"}


class _FakeClient:
    order = _FakeOrders()


B._client = lambda: _FakeClient()

u = _auth_db.upsert_user("sub-guard", "guard@example.com", "Guard", None)["id"]
_auth_db.create_default_organization_for_user(u, "Guard Co")
proj = _auth_db.create_project(u, "Guard project")
project_id = proj["id"] if isinstance(proj, dict) else proj


def client():
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = u
    return c


def buy(kind):
    c = client()
    if kind == "credits":
        return c.post("/api/billing/orders/credits", json={"pack_id": "pack_500"})
    if kind == "plan":
        return c.post("/api/billing/orders/plan", json={"plan": "pro"})
    return c.post("/api/billing/orders/report", json={"project_id": project_id})


def state(key_id, flag=None):
    if key_id is None:
        os.environ.pop("RAZORPAY_KEY_ID", None)
    else:
        os.environ["RAZORPAY_KEY_ID"] = key_id
    if flag is None:
        os.environ.pop("BILLING_LIVE_PURCHASES", None)
    else:
        os.environ["BILLING_LIVE_PURCHASES"] = flag


def orders_count():
    return len(_auth_db.list_orders(u, limit=500))


# ── test keys: exactly as before ─────────────────────────────────────────────
state("rzp_test_abc")
check(B.checkout_state() == {"live": False, "plans": True, "credits": True, "reports": True}, "test keys: everything open")
for kind in ("credits", "plan", "report"):
    r = buy(kind)
    check(r.status_code == 201, f"test keys: {kind} order is created, got {r.status_code} {r.get_data(as_text=True)[:80]}")
check(orders_count() == 3, "test keys: three orders recorded")
pricing = client().get("/api/billing/pricing").get_json()
check(pricing["checkout"] == {"live": False, "plans": True, "credits": True, "reports": True}, "pricing payload says all open")
check({"credit_packs", "plan_prices", "report_purchase_price_paise", "price_book_version"} <= set(pricing),
      "pricing payload keeps every existing key")

# ── live keys, flag unset: nothing is purchasable, no order, no gateway call ──
state("rzp_live_abc")
before_orders, before_calls = orders_count(), _FakeOrders.calls
check(B.checkout_state() == {"live": True, "plans": False, "credits": False, "reports": False}, "live keys, no flag: all closed")
r = buy("plan")
check(r.status_code == 403 and r.get_json()["error"] == "plan_checkout_retired", "live: legacy plan checkout is retired")
for kind in ("credits", "report"):
    r = buy(kind)
    check(r.status_code == 403 and r.get_json()["error"] == "purchases_not_open", f"live, no flag: {kind} refused with purchases_not_open")
check(orders_count() == before_orders, "a refused purchase leaves no order row behind")
check(_FakeOrders.calls == before_calls, "and Razorpay is never contacted")
pricing = client().get("/api/billing/pricing").get_json()
check(pricing["checkout"] == {"live": True, "plans": False, "credits": False, "reports": False}, "pricing payload tells the UI it is closed")

# ── live keys + explicit flag: credits and reports open, the legacy plan never ─
state("rzp_live_abc", "1")
check(B.checkout_state() == {"live": True, "plans": False, "credits": True, "reports": True}, "live keys + flag: credits/reports open")
r = buy("credits")
check(r.status_code == 201, f"live + flag: credit pack order is created, got {r.status_code}")
r = buy("report")
check(r.status_code == 201, f"live + flag: report order is created, got {r.status_code}")
r = buy("plan")
check(r.status_code == 403 and r.get_json()["error"] == "plan_checkout_retired", "live + flag: legacy plan STILL refused")

# ── an unknown kind of order fails closed, in both modes ─────────────────────
for key in ("rzp_test_abc", "rzp_live_abc"):
    state(key, "1")
    with app.test_request_context():
        check(B._purchase_gate("some_future_kind") is not None, f"unknown order kind is refused ({key[:8]})")
state("rzp_live_abc")

# ── the flag is strict: only the exact string "1" opens it ───────────────────
for bad in ("true", "yes", "0", "", "01", " 1"):
    state("rzp_live_abc", bad)
    check(B.checkout_state()["credits"] is False, f"flag {bad!r} does not open live purchases")

# ── what counts as a live key ────────────────────────────────────────────────
state("RZP_LIVE_abc")
check(B.checkout_state()["live"] is False, "prefix match is exact (upper-case is not a live key id)")
state("xrzp_live_abc")
check(B.checkout_state()["live"] is False, "prefix, not substring")
state(None)
check(B.checkout_state()["live"] is False, "no key configured is not 'live'")
r = buy("credits")
check(r.status_code in (201, 503), "no key configured: behaviour is whatever it was (order or 503), never the live gate")
check(not (r.get_json() or {}).get("error") in ("purchases_not_open", "plan_checkout_retired"), "no key: live gate not triggered")

# ── a payment already in flight still completes when the guard closes ────────
# Verify/webhook are untouched: an order made under test keys is still applied.
state("rzp_test_abc")
r = buy("credits")
order = r.get_json()["order"]
state("rzp_live_abc")
res = B._apply_paid_order(_auth_db.get_order(order["id"], u), "pay_inflight", None)
check(res is not None, "closing the gate does not break applying an already-created order")

print(f"OK — {passed} checks passed")

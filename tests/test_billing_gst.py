"""
test_billing_gst.py — list prices are ex-GST; GST goes on top only when a
valid PAISAMAP_GSTIN is configured, and the invoice always matches what was
actually charged.

  no GSTIN       -> charged = list price, invoice shows no GST ("Invoice")
  valid GSTIN    -> charged = list + 18% (half-up to the paisa), invoice
                    taxable = list, GST line, seller GSTIN ("Tax Invoice")
  malformed      -> treated as no GSTIN (never collect GST on a bad value)
  frozen split   -> the order records its tax split at checkout; flipping
                    PAISAMAP_GSTIN before payment doesn't change the invoice
  legacy orders  -> (no split recorded) GST-inclusive amount; GST carved out
                    only if a GSTIN is configured

    DATABASE_URL="sqlite:////tmp/billing_gst.sqlite" python3 tests/test_billing_gst.py

Plain script, throwaway sqlite, same conventions as the other suites.
"""

import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "paisamap-etl", "etl"))

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='pm_gst_')}/gst.sqlite")
if "sqlite" not in os.environ["DATABASE_URL"]:
    raise SystemExit("Refusing to run: DATABASE_URL doesn't look like a throwaway sqlite file.")
if not os.environ.get("CUSTOMER_DATA_KEY"):
    from cryptography.fernet import Fernet
    os.environ["CUSTOMER_DATA_KEY"] = Fernet.generate_key().decode()
os.environ.setdefault("INVOICES_DIR", tempfile.mkdtemp(prefix="pm_invoices_"))
os.environ["RAZORPAY_KEY_ID"] = "rzp_test_gst"
os.environ.pop("BILLING_LIVE_PURCHASES", None)
os.environ.pop("PAISAMAP_GSTIN", None)

import _auth_db
_auth_db.init_schema()
_auth_db.migrate_schema()

import _pricing as P
import server
from blueprints import billing as B

app = server.app
app.testing = True

GSTIN = "29BJUPR8491Q1ZI"
passed = 0


def check(cond, msg):
    global passed
    assert cond, f"FAIL: {msg}"
    passed += 1


class _FakeOrders:
    calls = 0
    last = None

    def create(self, data):
        _FakeOrders.calls += 1
        _FakeOrders.last = data
        return {"id": f"order_gst_{_FakeOrders.calls}"}


class _FakeClient:
    order = _FakeOrders()


B._client = lambda: _FakeClient()

u = _auth_db.upsert_user("sub-gst", "gst@example.com", "Gst Buyer", None)["id"]
_auth_db.create_default_organization_for_user(u, "Gst Co")
proj = _auth_db.create_project(u, "Gst project")
project_id = proj["id"] if isinstance(proj, dict) else proj


def client():
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = u
    return c


def gstin(value):
    if value is None:
        os.environ.pop("PAISAMAP_GSTIN", None)
    else:
        os.environ["PAISAMAP_GSTIN"] = value


def buy(kind):
    c = client()
    if kind == "credits":
        return c.post("/api/billing/orders/credits", json={"pack_id": "pack_500"})
    if kind == "plan":
        return c.post("/api/billing/orders/plan", json={"plan": "pro"})
    return c.post("/api/billing/orders/report", json={"project_id": project_id})


LIST = {"credits": P.CREDIT_PACKS["pack_500"]["price_paise"],
        "plan": P.PLAN_PRICES["pro"]["price_paise"],
        "report": P.REPORT_PURCHASE_PRICE_PAISE}


def pay(order_id):
    """Apply payment and return the invoice row."""
    B._apply_paid_order(_auth_db.get_order(order_id, u), f"pay_{order_id}", None)
    inv = [i for i in _auth_db.list_invoices(u) if i["order_id"] == order_id]
    assert len(inv) == 1, inv
    return inv[0]


# ── gst_breakdown: the arithmetic ─────────────────────────────────────────────
check(P.gst_breakdown(19900, False) == {"taxable_paise": 19900, "gst_rate": 0.0, "gst_paise": 0, "total_paise": 19900},
      "no GST: charged exactly the list price")
b = P.gst_breakdown(19900, True)
check(b == {"taxable_paise": 19900, "gst_rate": 0.18, "gst_paise": 3582, "total_paise": 23482}, f"Rs 199 + GST = Rs 234.82, got {b}")
g = P.gst_breakdown(P.TIERS["growth"]["price_paise"], True)
check(g["total_paise"] == 1_416_000, f"Growth Rs 12,000 + GST = Rs 14,160, got {g['total_paise']}")
check(g["total_paise"] <= P.UPI_AUTOPAY_MAX_PAISE, "Growth + GST stays under the Rs 15,000 UPI Autopay cap")
check(P.gst_breakdown(P.TIERS["starter"]["price_paise"], True)["total_paise"] == 590_000, "Starter Rs 5,000 + GST = Rs 5,900")
# half-up rounding at the paisa: 25 * 0.18 = 4.5 -> 5 ; 3 * 0.18 = 0.54 -> 1 ; 2 * 0.18 = 0.36 -> 0
check(P.gst_breakdown(25, True)["gst_paise"] == 5, "4.5 paise rounds up to 5")
check(P.gst_breakdown(3, True)["gst_paise"] == 1, "0.54 paise rounds to 1")
check(P.gst_breakdown(2, True)["gst_paise"] == 0, "0.36 paise rounds to 0")
check(P.gst_breakdown(0, True)["total_paise"] == 0, "zero stays zero")
for n in (1, 7, 99, 101, 14900, 79900, 249900, 99900, 1_234_567):
    x = P.gst_breakdown(n, True)
    exact = n * 18 / 100
    check(x["taxable_paise"] + x["gst_paise"] == x["total_paise"], f"{n}: parts add up to the total")
    check(abs(x["gst_paise"] - exact) <= 0.5, f"{n}: GST within half a paisa of 18%")
for bad in (-1, 1.5, "100", None, True):
    try:
        P.gst_breakdown(bad, True)
        check(False, f"gst_breakdown({bad!r}) should raise")
    except ValueError:
        check(True, f"gst_breakdown rejects {bad!r}")

# ── GSTIN shape ───────────────────────────────────────────────────────────────
check(P.valid_gstin(GSTIN), "the operator's own GSTIN is valid")
for bad in ("", "29BJUPR8491Q1Z", "29BJUPR8491Q1ZIX", "29bjupr8491q1zi", "XXBJUPR8491Q1ZI",
            "29BJUPR8491Q0ZI", "29BJUPR8491Q1YI", None, 29):
    check(not P.valid_gstin(bad), f"invalid GSTIN rejected: {bad!r}")

# ── no GSTIN: price charged = list, invoice has no GST ────────────────────────
gstin(None)
pricing = client().get("/api/billing/pricing").get_json()
check(pricing["gst"] == {"charged": False, "rate": 0.18}, f"pricing says GST not charged, got {pricing.get('gst')}")
for kind in ("credits", "plan", "report"):
    r = buy(kind)
    check(r.status_code == 201, f"no GSTIN: {kind} order created")
    body = r.get_json()
    check(body["amount_paise"] == LIST[kind], f"no GSTIN: {kind} charged the list price")
    check(_FakeOrders.last["amount"] == LIST[kind], f"no GSTIN: Razorpay asked for the list price ({kind})")
    check(body["gst_paise"] == 0 and body["taxable_paise"] == LIST[kind], f"no GSTIN: {kind} response split")
    inv = pay(body["order"]["id"])
    check(inv["gst_amount_paise"] == 0 and inv["gst_rate"] == 0, f"no GSTIN: {kind} invoice carries no GST")
    check(inv["taxable_amount_paise"] == inv["total_amount_paise"] == LIST[kind], f"no GSTIN: {kind} invoice total = list")
    check(inv["seller_gstin"] is None, f"no GSTIN: {kind} invoice has no seller GSTIN")

# ── valid GSTIN: GST on top ───────────────────────────────────────────────────
gstin(GSTIN)
pricing = client().get("/api/billing/pricing").get_json()
check(pricing["gst"] == {"charged": True, "rate": 0.18}, "pricing says GST is charged on top")
for kind in ("credits", "plan", "report"):
    want = P.gst_breakdown(LIST[kind], True)
    r = buy(kind)
    body = r.get_json()
    check(body["amount_paise"] == want["total_paise"], f"GSTIN: {kind} charged list + GST")
    check(_FakeOrders.last["amount"] == want["total_paise"], f"GSTIN: Razorpay asked for list + GST ({kind})")
    order = _auth_db.get_order(body["order"]["id"], u)
    check(order["amount_paise"] == want["total_paise"], f"GSTIN: {kind} order row stores the charged total")
    check(order["meta"]["tax"]["seller_gstin"] == GSTIN, f"GSTIN: {kind} order froze the GSTIN")
    inv = pay(order["id"])
    check(inv["taxable_amount_paise"] == LIST[kind], f"GSTIN: {kind} invoice taxable = list price")
    check(inv["gst_amount_paise"] == want["gst_paise"] and inv["gst_rate"] == 0.18, f"GSTIN: {kind} invoice GST line")
    check(inv["total_amount_paise"] == want["total_paise"], f"GSTIN: {kind} invoice total = charged")
    check(inv["seller_gstin"] == GSTIN, f"GSTIN: {kind} invoice shows seller GSTIN")

# lower-case / padded value is normalised, not rejected
gstin("  " + GSTIN.lower() + " ")
check(B._seller_gstin() == GSTIN, "GSTIN is trimmed and upper-cased")

# ── malformed GSTIN: fail closed, no GST ──────────────────────────────────────
for bad in ("yes", "29BJUPR8491Q1Z", "PENDING"):
    gstin(bad)
    check(B._seller_gstin() is None, f"malformed GSTIN {bad!r} -> no GST")
    r = buy("credits")
    check(r.get_json()["amount_paise"] == LIST["credits"], f"malformed GSTIN {bad!r}: charged list price")

# ── the split is frozen at checkout ───────────────────────────────────────────
gstin(GSTIN)
o1 = buy("credits").get_json()["order"]["id"]
gstin(None)
inv = pay(o1)
check(inv["gst_amount_paise"] == P.gst_breakdown(LIST["credits"], True)["gst_paise"] and inv["seller_gstin"] == GSTIN,
      "GSTIN removed after checkout: invoice still shows the GST the buyer paid")
o2 = buy("credits").get_json()["order"]["id"]
gstin(GSTIN)
inv = pay(o2)
check(inv["gst_amount_paise"] == 0 and inv["total_amount_paise"] == LIST["credits"] and inv["seller_gstin"] is None,
      "GSTIN added after checkout: invoice doesn't invent GST the buyer never paid")

# a tampered/inconsistent split is not trusted
o3 = _auth_db.create_order(u, "credit_pack", "order_gst_tampered", 10000, credit_pack_id="pack_100",
                           meta={"tax": {"taxable_paise": 9000, "gst_paise": 500, "gst_rate": 0.18, "seller_gstin": GSTIN}})
gstin(None)
inv = pay(o3["id"])
check(inv["total_amount_paise"] == 10000 and inv["gst_amount_paise"] == 0,
      "split that doesn't add up to the charged amount is ignored")

# ── legacy orders (no split recorded, GST-inclusive amount) ───────────────────
o4 = _auth_db.create_order(u, "credit_pack", "order_gst_legacy_a", 11800, credit_pack_id="pack_100")
gstin(GSTIN)
inv = pay(o4["id"])
check((inv["taxable_amount_paise"], inv["gst_amount_paise"]) == (10000, 1800), "legacy + GSTIN: 18% carved out of the inclusive amount")
o5 = _auth_db.create_order(u, "credit_pack", "order_gst_legacy_b", 11800, credit_pack_id="pack_100")
gstin(None)
inv = pay(o5["id"])
check((inv["taxable_amount_paise"], inv["gst_amount_paise"], inv["gst_rate"]) == (11800, 0, 0),
      "legacy without GSTIN: no GST carved out")
for total in (1, 99, 19900, 79900, 249900, 14900):
    o = _auth_db.create_order(u, "credit_pack", f"order_gst_legacy_{total}", total, credit_pack_id="pack_100")
    gstin(GSTIN)
    inv = pay(o["id"])
    check(inv["taxable_amount_paise"] + inv["gst_amount_paise"] == total, f"legacy {total}: parts add up")
    check(inv["taxable_amount_paise"] == round(total / 1.18), f"legacy {total}: same carve-out as before")

# ── the report page's "Buy this report" hint knows whether GST applies ────────
_auth_db.create_saved_location(u, project_id, "560001", name="MG Road")
_real_balance = _auth_db.get_credit_balance
_auth_db.get_credit_balance = lambda *a, **k: 0   # new accounts start with free credits
for value, want in ((None, False), (GSTIN, True)):
    gstin(value)
    r = client().post("/api/reports", json={"project_id": project_id})
    if r.status_code == 402:
        check(r.get_json().get("gst_charged") is want, f"402 body gst_charged={want}")
    else:
        check(False, f"expected 402 insufficient_credits, got {r.status_code} {r.get_data(as_text=True)[:120]}")
_auth_db.get_credit_balance = _real_balance

# ── invoice PDF title follows registration ────────────────────────────────────
import _invoice_pdf
src = open(_invoice_pdf.__file__).read()
check('"Tax Invoice" if invoice.get("seller_gstin") else "Invoice"' in src, "PDF title: Tax Invoice only when registered")

gstin(None)
print(f"OK — {passed} checks passed")

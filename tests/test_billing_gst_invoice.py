"""
test_billing_gst_invoice.py — P12, the GST invoice engine.

  SAC 998439 on every invoice; 18% GST
  Karnataka buyer (or no state given)  -> CGST 9% + SGST 9%
  any other state                      -> IGST 18%
  buyer GSTIN  -> checksum-checked, fixes the state, needs a legal name
  billing omitted -> the last details given for that company are reused
  invoice number  -> PM-<FY>-<seq>, <= 16 chars, financial year in IST
  rendered PDF    -> seller block, buyer block, place of supply, heads, words

    DATABASE_URL="sqlite:////tmp/billing_gst_invoice.sqlite" python3 tests/test_billing_gst_invoice.py

Plain script, throwaway sqlite, same conventions as the other suites.
"""

import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "paisamap-etl", "etl"))

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='pm_gstinv_')}/gstinv.sqlite")
if "sqlite" not in os.environ["DATABASE_URL"]:
    raise SystemExit("Refusing to run: DATABASE_URL doesn't look like a throwaway sqlite file.")
if not os.environ.get("CUSTOMER_DATA_KEY"):
    from cryptography.fernet import Fernet
    os.environ["CUSTOMER_DATA_KEY"] = Fernet.generate_key().decode()
os.environ.setdefault("INVOICES_DIR", tempfile.mkdtemp(prefix="pm_invoices_"))
os.environ["RAZORPAY_KEY_ID"] = "rzp_test_gstinv"
os.environ.pop("BILLING_LIVE_PURCHASES", None)
os.environ.pop("PAISAMAP_GSTIN", None)

import reportlab.rl_config
reportlab.rl_config.pageCompression = 0   # so the PDF text can be read back

import _auth_db
_auth_db.init_schema()
_auth_db.migrate_schema()

import _gst as G
import server
from blueprints import billing as B

app = server.app
app.testing = True

SELLER_GSTIN = "29BJUPR8491Q1ZI"
KA_GSTIN = "29AAGCB7383J1Z4"      # a real-shaped Karnataka GSTIN (valid checksum)
MH_GSTIN = "27AAPFU0939F1ZV"      # Maharashtra
passed = 0


def check(cond, msg):
    global passed
    assert cond, f"FAIL: {msg}"
    passed += 1


# ── _gst: GSTIN checksum ──────────────────────────────────────────────────────
for good in (SELLER_GSTIN, KA_GSTIN, MH_GSTIN):
    check(G.gstin_checksum_ok(good), f"{good} passes the checksum")
for bad in ("29BJUPR8491Q1ZJ", "29BJUPR8491Q1Z", "29bjupr8491q1zi", "", None, 29, "29BJUPR8491Q1ZI "):
    check(not G.gstin_checksum_ok(bad), f"{bad!r} fails")
# every single-character typo in the first 14 positions is caught
typos = 0
for i in range(14):
    for ch in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if ch == SELLER_GSTIN[i]:
            continue
        t = SELLER_GSTIN[:i] + ch + SELLER_GSTIN[i + 1:]
        if G.gstin_checksum_ok(t):
            typos += 1
check(typos == 0, f"no single-character typo slips through, {typos} did")

# ── _gst: buyer details ───────────────────────────────────────────────────────
check(G.normalize_buyer(None) == (None, None), "nothing given -> no buyer")
check(G.normalize_buyer({}) == (None, None), "empty -> no buyer")
check(G.normalize_buyer({"name": "  ", "address": "", "gstin": None}) == (None, None), "blanks -> no buyer")
check(G.normalize_buyer("x") == (None, "invalid_billing"), "non-object refused")
b, e = G.normalize_buyer({"name": "  Acme   Retail  Pvt Ltd ", "gstin": KA_GSTIN.lower()})
check(e is None and b == {"name": "Acme Retail Pvt Ltd", "gstin": KA_GSTIN, "state_code": "29", "address": None},
      f"GSTIN upper-cased, state from GSTIN, whitespace collapsed: {b} {e}")
check(G.normalize_buyer({"name": "A", "gstin": MH_GSTIN, "state_code": "27"})[1] is None, "matching state ok")
check(G.normalize_buyer({"name": "A", "gstin": MH_GSTIN, "state_code": "29"}) == (None, "gstin_state_mismatch"),
      "state contradicting the GSTIN refused")
check(G.normalize_buyer({"gstin": MH_GSTIN}) == (None, "gstin_needs_name"), "GSTIN needs a legal name")
check(G.normalize_buyer({"name": "A", "gstin": "27AAPFU0939F1ZW"}) == (None, "invalid_gstin"), "bad checksum refused")
check(G.normalize_buyer({"name": "A", "gstin": "NOTAGSTIN"}) == (None, "invalid_gstin"), "junk GSTIN refused")


def with_check_digit(first14):
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    total = sum((lambda p: p // 36 + p % 36)(chars.index(c) * (2 if i % 2 else 1)) for i, c in enumerate(first14))
    return first14 + chars[(36 - total % 36) % 36]


for code in ("28", "25", "99", "00"):   # retired / merged / not a state
    ghost = with_check_digit(code + MH_GSTIN[2:14])
    check(G.gstin_checksum_ok(ghost), f"setup: {ghost} has a valid check digit")
    check(G.normalize_buyer({"name": "A", "gstin": ghost}) == (None, "invalid_gstin"),
          f"checksum-valid GSTIN in non-state {code} refused")
check(G.normalize_buyer({"name": "A", "gstin": 123}) == (None, "invalid_gstin"), "non-string GSTIN refused")
check(G.normalize_buyer({"state_code": "28"}) == (None, "invalid_state"), "retired state code 28 refused")
check(G.normalize_buyer({"state_code": "7"}) == (None, "invalid_state"), "one-digit state refused")
check(G.normalize_buyer({"state_code": "007"}) == (None, "invalid_state"), "three-char state refused")
check(G.normalize_buyer({"state_code": "07"}) == ({"name": None, "gstin": None, "state_code": "07", "address": None}, None),
      "consumer with only a state")
check(G.normalize_buyer({"name": "x" * 201}) == (None, "invalid_billing_name"), "over-long name refused")
check(G.normalize_buyer({"address": "x" * 501}) == (None, "invalid_billing_address"), "over-long address refused")
check(G.normalize_buyer({"name": 5}) == (None, "invalid_billing_name"), "non-string name refused")

# ── _gst: place of supply and the split ───────────────────────────────────────
check(G.place_of_supply(None) == "29", "no details -> supplier's state")
check(G.place_of_supply({"state_code": None}) == "29", "no state -> supplier's state")
check(G.place_of_supply({"state_code": "27"}) == "27", "buyer's state wins")
check(G.split_gst(216000, "29") == {"supply": "intra", "cgst_paise": 108000, "sgst_paise": 108000, "igst_paise": 0},
      "Growth in Karnataka: 1,080 + 1,080")
check(G.split_gst(216000, "27") == {"supply": "inter", "cgst_paise": 0, "sgst_paise": 0, "igst_paise": 216000},
      "Growth in Maharashtra: IGST 2,160")
check(G.split_gst(0, "29")["supply"] == "none" and G.split_gst(0, "27")["igst_paise"] == 0, "no GST -> no heads")
for gst in range(0, 400):
    for pos in ("29", "07"):
        h = G.split_gst(gst, pos)
        check(h["cgst_paise"] + h["sgst_paise"] + h["igst_paise"] == gst, f"{gst}@{pos}: heads add up")
        check(abs(h["cgst_paise"] - h["sgst_paise"]) <= 1, f"{gst}@{pos}: halves within a paisa")

# ── _gst: numbering ───────────────────────────────────────────────────────────
utc = timezone.utc
check(G.financial_year(datetime(2026, 3, 31, 18, 29, tzinfo=utc)) == "25-26", "23:59 IST 31 Mar -> FY 25-26")
check(G.financial_year(datetime(2026, 3, 31, 18, 30, tzinfo=utc)) == "26-27", "00:00 IST 1 Apr -> FY 26-27")
check(G.financial_year(datetime(2026, 9, 26)) == "26-27", "naive datetimes are UTC")
check(G.financial_year(datetime(2099, 6, 1, tzinfo=utc)) == "99-00", "century wrap")
n = G.invoice_number(999999, datetime(2026, 9, 26, tzinfo=utc))
check(n == "PM-26-27-999999" and len(n) <= 16, f"format + GST length cap: {n}")
check(re.fullmatch(r"[A-Za-z0-9/-]{1,16}", n) is not None, "GST-allowed characters only")

# ── _gst: amount in words ─────────────────────────────────────────────────────
for paise, words in (
    (0, "Rupees Zero Only"),
    (1, "Rupees Zero and One Paise Only"),
    (100, "Rupees One Only"),
    (1_416_000, "Rupees Fourteen Thousand One Hundred Sixty Only"),
    (590_000, "Rupees Five Thousand Nine Hundred Only"),
    (10_000_000, "Rupees One Lakh Only"),
    (1_000_000_000, "Rupees One Crore Only"),
    (12_345_678_905, "Rupees Twelve Crore Thirty Four Lakh Fifty Six Thousand Seven Hundred Eighty Nine and Five Paise Only"),
    (11_000, "Rupees One Hundred Ten Only"),
    (1_999_999_999_900, "Rupees One Thousand Nine Hundred Ninety Nine Crore Ninety Nine Lakh Ninety Nine Thousand Nine Hundred Ninety Nine Only"),
):
    got = G.amount_in_words(paise)
    check(got == words, f"{paise}: {got!r}")


# ── HTTP: checkout -> invoice ─────────────────────────────────────────────────
class _FakeOrders:
    calls = 0

    def create(self, data):
        _FakeOrders.calls += 1
        return {"id": f"order_gstinv_{_FakeOrders.calls}"}


class _FakeClient:
    order = _FakeOrders()


B._client = lambda: _FakeClient()

u = _auth_db.upsert_user("sub-gstinv", "buyer@example.com", "Priya Buyer", None)["id"]
org_a = _auth_db.create_default_organization_for_user(u, "Acme")["id"]
org_b = _auth_db.create_organization(u, "Beta")["id"]
proj = _auth_db.create_project(u, "Store 40", org_id=org_a)
project_id = proj["id"] if isinstance(proj, dict) else proj
other = _auth_db.upsert_user("sub-gstinv-2", "other@example.com", "Other", None)["id"]
_auth_db.create_default_organization_for_user(other, "Other Co")


def client(user=u):
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = user
    return c


def buy(billing="__omit__", org=None, user=u):
    body = {"pack_id": "pack_500"}
    if billing != "__omit__":
        body["billing"] = billing
    if org is not None:
        body["org_id"] = org
    return client(user).post("/api/billing/orders/credits", json=body)


def pay(order_id, user=u):
    B._apply_paid_order(_auth_db.get_order(order_id, user), f"pay_{order_id}", None)
    inv = [i for i in _auth_db.list_invoices(user) if i["order_id"] == order_id]
    assert len(inv) == 1, inv
    return inv[0]


def pdf_text(inv):
    raw = Path(inv["file_path"]).read_bytes().decode("latin-1")
    # reportlab splits a line into several runs where markup-like characters sit
    raw = raw.replace(") Tj (", "")
    return raw.replace("\\(", "(").replace("\\)", ")")


os.environ["PAISAMAP_GSTIN"] = SELLER_GSTIN
pricing = client().get("/api/billing/pricing").get_json()["gst"]
check(pricing["seller_state"] == "29" and pricing["states"]["27"] == "Maharashtra" and "28" not in pricing["states"],
      "pricing exposes the state list for the checkout form")

# 1. Karnataka business: CGST + SGST
r = buy({"name": "Acme Retail Pvt Ltd", "gstin": KA_GSTIN, "address": "12 MG Road, Bengaluru 560001"})
check(r.status_code == 201, f"KA order created: {r.status_code} {r.get_data(as_text=True)[:120]}")
body = r.get_json()
check(body["place_of_supply"] == "29", "response says place of supply")
o = _auth_db.get_order(body["order"]["id"], u)
tax = o["meta"]["tax"]
check(tax["sac"] == "998439" and tax["supply"] == "intra", f"frozen: SAC + intra, {tax}")
check(tax["cgst_paise"] + tax["sgst_paise"] == tax["gst_paise"] and tax["igst_paise"] == 0, "frozen heads add up")
inv = pay(o["id"])
check(re.fullmatch(r"PM-\d\d-\d\d-\d{6}", inv["invoice_number"]) is not None, f"FY number: {inv['invoice_number']}")
check(inv["buyer_gstin"] == KA_GSTIN and inv["buyer_name"] == "Acme Retail Pvt Ltd", "invoice issued to the business")
t = pdf_text(inv)
for s in ("Tax Invoice", "Cooter Studio", "Raghotham Harisha", SELLER_GSTIN, "560057",
          "Acme Retail Pvt Ltd", KA_GSTIN, "12 MG Road", "Place of supply: Karnataka (29)",
          "reverse charge: No", "998439", "CGST @ 9%", "SGST @ 9%", "Amount in words", inv["invoice_number"]):
    check(s in t, f"KA PDF shows {s!r}")
check("IGST" not in t, "KA PDF has no IGST")

# 2. Maharashtra business: IGST
r = buy({"name": "Mumbai Foods LLP", "gstin": MH_GSTIN})
o = _auth_db.get_order(r.get_json()["order"]["id"], u)
check(o["meta"]["tax"]["supply"] == "inter" and o["meta"]["tax"]["igst_paise"] == o["meta"]["tax"]["gst_paise"],
      "MH frozen as IGST")
t = pdf_text(pay(o["id"]))
check("IGST @ 18%" in t and "CGST" not in t and "SGST" not in t, "MH PDF: IGST only")
check("Place of supply: Maharashtra (27)" in t and "State: Maharashtra (27)" in t, "MH place of supply + buyer state")

# 3. omitted billing -> last details for this company (Maharashtra) are reused
r = buy()
o = _auth_db.get_order(r.get_json()["order"]["id"], u)
check(o["meta"]["buyer"]["gstin"] == MH_GSTIN and o["meta"]["tax"]["place_of_supply"] == "27", "last details reused")
check(client().get(f"/api/billing/buyer?org_id={org_a}").get_json()["buyer"]["gstin"] == MH_GSTIN,
      "/buyer prefills the last details")
# ...but not across companies, nor across people
# Beta was created by Acme's owner, so Acme pays for it: the invoice goes to the payer, same details.
check(_auth_db.get_payer_org_id(org_b) == org_a, "setup: Beta is paid for by Acme")
check(client().get(f"/api/billing/buyer?org_id={org_b}").get_json()["buyer"]["gstin"] == MH_GSTIN,
      "a company paid for by Acme gets Acme's details")
# Once Beta pays for itself it is a different buyer and starts blank.
check(_auth_db.set_org_payer(org_b, u, None) == {"status": "ok"}, "setup: Beta now pays for itself")
check(client().get(f"/api/billing/buyer?org_id={org_b}").get_json()["buyer"] is None, "self-paying company starts blank")
r = buy(org=org_b)
o = _auth_db.get_order(r.get_json()["order"]["id"], u)
check(o["meta"]["buyer"] is None and o["meta"]["tax"]["place_of_supply"] == "29", "other company: no reuse, KA default")
check(client(other).get("/api/billing/buyer").get_json()["buyer"] is None, "another person never sees them")
check(client().get("/api/billing/buyer?org_id=abc").status_code == 400, "bad org_id refused")
check(client(other).get(f"/api/billing/buyer?org_id={org_a}").status_code == 403, "not your company -> 403")

# 4. explicit empty billing clears it: consumer, no state -> CGST + SGST, no GSTIN line
r = buy({})
o = _auth_db.get_order(r.get_json()["order"]["id"], u)
check(o["meta"]["buyer"] is None and o["meta"]["tax"]["supply"] == "intra", "empty billing = consumer in KA")
inv = pay(o["id"])
t = pdf_text(inv)
check(inv["buyer_name"] == "Priya Buyer" and inv["buyer_gstin"] is None, "consumer invoice uses the account name")
check("Not yet registered" not in t and t.count("State: Karnataka") == 1 and t.count("GSTIN:") == 1,
      "consumer block claims nothing it wasn't told (the one state/GSTIN line is the seller's)")
check("CGST @ 9%" in t, "consumer in KA pays CGST + SGST")

# 5. consumer in Delhi -> IGST
r = buy({"state_code": "07"})
t = pdf_text(pay(r.get_json()["order"]["id"]))
check("IGST @ 18%" in t and "Place of supply: Delhi (07)" in t, "Delhi consumer: IGST")

# 6. the report purchase reuses the project company's details
buy({"name": "Acme Retail Pvt Ltd", "gstin": KA_GSTIN})
r = client().post("/api/billing/orders/report", json={"project_id": project_id})
o = _auth_db.get_order(r.get_json()["order"]["id"], u)
check(o["meta"]["buyer"]["gstin"] == KA_GSTIN, "report order reuses the company's details")

# 7. refusals leave nothing behind and never contact Razorpay
before_orders, before_calls = len(_auth_db.list_orders(u, limit=500)), _FakeOrders.calls
for billing, code in (({"name": "A", "gstin": "27AAPFU0939F1ZW"}, "invalid_gstin"),
                      ({"name": "A", "gstin": MH_GSTIN, "state_code": "29"}, "gstin_state_mismatch"),
                      ({"gstin": MH_GSTIN}, "gstin_needs_name"),
                      ({"state_code": "99"}, "invalid_state"),
                      ("x", "invalid_billing")):
    r = buy(billing)
    check(r.status_code == 400 and r.get_json()["error"] == code, f"{billing!r} -> 400 {code}, got {r.get_json()}")
check(len(_auth_db.list_orders(u, limit=500)) == before_orders and _FakeOrders.calls == before_calls,
      "no order row, no Razorpay call")

# 8. markup in buyer text is printed, not interpreted
r = buy({"name": "<b>Evil</b> & Sons <font size=90>", "address": "a < b > c & d"})
t = pdf_text(pay(r.get_json()["order"]["id"]))
check("<b>Evil</b> & Sons" in t and "a < b > c & d" in t, "markup shown literally")

# 9. a legacy order (no split, no buyer) still gets a correct KA invoice
o = _auth_db.create_order(u, "credit_pack", "order_gstinv_legacy", 11800, credit_pack_id="pack_100")
inv = pay(o["id"])
t = pdf_text(inv)
check("CGST @ 9%" in t and "SGST @ 9%" in t and "Place of supply: Karnataka (29)" in t, "legacy -> KA split")

# 9b. a direct PDF call with no details still derives the heads from the GST amount
import _invoice_pdf
row = dict(inv, invoice_number="PM-26-27-DIRECT")
t = pdf_text({"file_path": _invoice_pdf.build_invoice_pdf(row, {"id": u}, Path(os.environ["INVOICES_DIR"]))})
check("CGST @ 9%" in t and "SGST @ 9%" in t and "Place of supply: Karnataka (29)" in t, "no details -> KA split")
check(t.count("Rs. 9.00") == 2 and "Rs. 0.00" not in t, "no details -> real amounts, Rs 118 = 100 + 9 + 9")

# 10. unregistered seller: plain Invoice, no tax heads, no place-of-supply line
os.environ.pop("PAISAMAP_GSTIN")
r = buy({"name": "Mumbai Foods LLP", "gstin": MH_GSTIN})
t = pdf_text(pay(r.get_json()["order"]["id"]))
check("Tax Invoice" not in t and "IGST" not in t and "CGST" not in t, "unregistered: no tax heads")
check("GSTIN: Not registered" in t and "Place of supply" not in t, "unregistered says so")

print(f"OK — {passed} checks passed")

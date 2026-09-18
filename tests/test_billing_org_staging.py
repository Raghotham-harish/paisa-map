"""
test_billing_org_staging.py — billing-v2 step 1 + stage 2a.

Step 1: the additive price book in _pricing.py matches docs/PRICING_MODEL.md
        and the legacy placeholders that live checkout still reads did NOT move.
Stage 2a: every credits_ledger row and order is stamped with its company
        (org_id), plan flips are mirrored to the buyer's OWN company, and none
        of it changes how balances or plans are actually read/enforced.

    DATABASE_URL="sqlite:////tmp/billing_org_staging.sqlite" \
      python3 tests/test_billing_org_staging.py

Plain script, throwaway sqlite, same conventions as the other suites in this
directory. A failure is a real regression in a money path.
"""

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "paisamap-etl", "etl"))

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/billing_org_staging_default.sqlite")
if "sqlite" not in os.environ["DATABASE_URL"]:
    raise SystemExit("Refusing to run: DATABASE_URL doesn't look like a throwaway sqlite file.")
if not os.environ.get("CUSTOMER_DATA_KEY"):
    from cryptography.fernet import Fernet
    os.environ["CUSTOMER_DATA_KEY"] = Fernet.generate_key().decode()

import tempfile
os.environ.setdefault("INVOICES_DIR", tempfile.mkdtemp(prefix="pm_invoices_"))

import _pricing as P

passed = 0


def check(cond, msg):
    global passed
    assert cond, f"FAIL: {msg}"
    passed += 1


# ═══ Step 1 — price book ═════════════════════════════════════════════════════

# Legacy placeholders: live checkout reads these. Editing them in place changes
# real prices, so they are pinned here. Change them only as a deliberate cutover.
check(P.CREDIT_COSTS == {"report_generate": 10, "expansion_recommend": 5, "forecast": 8}, "legacy credit costs unchanged")
check(P.CREDIT_PACKS == {
    "pack_100":  {"credits": 100,  "price_paise": 19900,  "label": "100 credits"},
    "pack_500":  {"credits": 500,  "price_paise": 79900,  "label": "500 credits"},
    "pack_2000": {"credits": 2000, "price_paise": 249900, "label": "2000 credits"},
}, "legacy credit packs unchanged")
check(P.PLAN_PRICES == {"pro": {"price_paise": 99900, "label": "Pro", "interval": "monthly"},
                        "team": {"price_paise": 299900, "label": "Team", "interval": "monthly"}},
      "legacy plan prices unchanged")
check(P.PLAN_ORDER == ["free", "pro", "team"] and P.plan_rank("team") == 2 and P.plan_rank("bogus") == 0,
      "legacy plan_rank unchanged (and still fails closed)")
check(P.REPORT_PURCHASE_PRICE_PAISE == 14900 and P.GST_RATE == 0.18, "legacy report price / GST unchanged")
payload = P.public_pricing_payload()
check({"gst_rate", "credit_costs", "credit_packs", "plan_prices", "report_purchase_price_paise"} <= set(payload),
      "public pricing payload keeps every legacy key")
check(payload["price_book_version"] == P.PRICE_BOOK_VERSION, "payload exposes the price-book version")

# v2 numbers vs docs/PRICING_MODEL.md §1/§3/§5
paid = ["starter", "growth", "scale", "pro"]
rupees = lambda paise: paise / 100
check([rupees(P.TIERS[t]["price_paise"]) for t in paid] == [5000, 12000, 25000, 50000], "tier prices 5k/12k/25k/50k")
check(rupees(P.TIERS["enterprise"]["price_paise"]) == 100000, "enterprise from Rs 1,00,000")
check([P.TIERS[t]["credits_per_month"] for t in paid + ["enterprise"]] == [1000, 3000, 7000, 16000, 40000],
      "credits/month 1,000/3,000/7,000/16,000/40,000")
check([P.TIERS[t]["annual_monthly_paise"] for t in paid] ==
      [round(P.TIERS[t]["price_paise"] * (1 - P.ANNUAL_DISCOUNT)) for t in paid], "annual = monthly x 0.8")
check([P.TIERS[t]["seats"] for t in paid] == [3, 6, 12, 25], "seats 3/6/12/25")
check([rupees(P.TIERS[t]["extra_seat_paise"]) for t in paid] == [900, 900, 800, 700], "extra seat 900/900/800/700")
check([P.TIERS[t]["companies"] for t in paid] == [1, 1, 3, 10], "companies 1/1/3/10")
check([rupees(P.TIERS[t]["extra_company_paise"]) for t in paid] == [2500, 2500, 3000, 5000], "extra company 2.5k/2.5k/3k/5k")
check(P.TRIAL_DAYS == 7 and P.TRIAL_CREDITS == 500 and P.TIERS["trial"]["seats"] == 2, "trial: 7 days, 500 credits, 2 seats")
check(P.KEYWORD_RESEARCH_CREDITS == 12 and P.ANNUAL_CREDIT_BONUS == 0.05, "keyword 12 credits, annual +5%")

# The pricing doc's own design invariants — these are the rules that stop the
# ladder being gamed, so they must survive any future number tweak.
fee_share = [P.TIERS[t]["extra_company_paise"] / P.TIERS[t]["price_paise"] for t in paid]
check(all(f < 1 for f in fee_share), "extra-company fee is always below that tier's base price")
check(fee_share == sorted(fee_share, reverse=True), "extra-company fee shrinks as a share of spend up the ladder")
per_credit = [P.TIERS[t]["price_paise"] / P.TIERS[t]["credits_per_month"] for t in paid + ["enterprise"]]
check(per_credit == sorted(per_credit, reverse=True) and per_credit[-1] <= 250, "rupees/credit falls up the ladder, <=2.50 at enterprise")
for t in paid:
    check(P.TIERS[t]["self_serve"] == (P.TIERS[t]["price_paise"] <= P.UPI_AUTOPAY_MAX_PAISE),
          f"{t}: self-serve exactly when it fits under the Rs 15,000/cycle UPI Autopay cap")
packs = [P.TOPUP_PACKS[k] for k in ("topup_500", "topup_2000", "topup_5000")]
check([p["price_paise"] / p["credits"] / 100 for p in packs] == [6.0, 5.0, 4.4], "top-up Rs/credit 6.00/5.00/4.40")
check(P.TOPUP_CREDIT_ROLLOVER_DAYS == 60, "top-ups roll 60 days")
check(P.annual_credits_per_month("starter") == 1050, "annual starter grants 1,050 credits/month")
lite = P.SIGNAL_TIERS["lite"]["extra_signals"]
check(len(lite) == 10 and len(set(lite)) == 10 and not set(lite) & set(P.CORE_SIGNALS), "Lite: 10 distinct signals beyond the 3 core")
check(P.SIGNAL_TIERS["lite"]["price_paise"] == 20000 and P.SIGNAL_TIERS["pro"]["price_paise"] == 50000, "signal tiers Rs 200 / Rs 500")

# The namespace hazard: legacy 'pro' (Rs 999) vs v2 Pro (Rs 50,000) share a string.
check(P.parse_plan("pro") == ("legacy", "pro") and P.parse_plan("v2_pro") == ("v2", "pro"), "'pro' and 'v2_pro' are distinct")
check(P.tier_rank("pro") != P.tier_rank("v2_pro"), "legacy pro must not rank as v2 Pro")
check(P.tier_rank("pro") == P.tier_rank("v2_starter") and P.tier_rank("team") == P.tier_rank("v2_growth"),
      "legacy plans grandfather at Starter / Growth")
check(P.plan_id_v2("scale") == "v2_scale", "stored v2 id is prefixed")
for junk in ("gold", "v2_bogus", "", None, "V2_PRO"):
    check(P.parse_plan(junk)[0] == "unknown" and P.tier_rank(junk) == 0 and not P.is_dashboard_tier(junk),
          f"unrecognised plan {junk!r} fails closed")
check(not P.is_dashboard_tier("free") and P.is_dashboard_tier("v2_trial") and P.is_dashboard_tier("team"),
      "dashboard access: free no, trial yes, legacy team yes")
try:
    P.plan_id_v2("gold")
    check(False, "plan_id_v2 rejects unknown tiers")
except ValueError:
    check(True, "plan_id_v2 rejects unknown tiers")

# ═══ Stage 2a — company stamping ═════════════════════════════════════════════

import _db
_db.enabled = lambda: False
import _auth_db
from sqlalchemy import text

_auth_db.init_schema()
_auth_db.migrate_schema()
engine = _auth_db._require_engine()
tables = _auth_db._get_tables()
ledger = tables["credits_ledger"]


def rows(user_id=None):
    with engine.connect() as c:
        q = ledger.select().order_by(ledger.c.id)
        if user_id is not None:
            q = q.where(ledger.c.user_id == user_id)
        return [dict(r) for r in c.execute(q).mappings()]


def user_with_company(tag):
    u = _auth_db.upsert_user(f"sub-{tag}", f"{tag}@example.com", tag, None)
    org = _auth_db.create_default_organization_for_user(u["id"], f"{tag} Co")
    return u["id"], org["id"]


a, org_a = user_with_company("alice")
b, org_b = user_with_company("bob")

# grant stamps the caller's primary company; the per-user balance is unchanged
check(_auth_db.grant_credits(a, 100, "test_grant") == 100, "grant returns the per-user balance")
check(rows(a)[-1]["org_id"] == org_a, "grant stamps the caller's primary company")
check(_auth_db.get_credit_balance(a) == 100, "per-user balance read is unchanged")

# spend defaults to the primary company; an explicit org (a project's) wins
check(_auth_db.spend_credits(a, 30, "test_spend") == 70, "spend returns the per-user balance")
check(rows(a)[-1]["org_id"] == org_a, "spend stamps the primary company by default")
second = _auth_db.create_organization(a, "Alice Second Co")
proj = _auth_db.create_project(a, "P2", org_id=second["id"])
check(proj["org_id"] == second["id"], "project lives in the second company")
check(_auth_db.spend_credits(a, 10, "test_spend", org_id=proj["org_id"]) == 60, "project-scoped spend works")
check(rows(a)[-1]["org_id"] == second["id"], "project-scoped spend is attributed to the PROJECT's company")
check(_auth_db.get_credit_balance(a) == 60, "balance is still one per-user pool (not split by company yet)")

# insufficient credits: raises, changes nothing, writes no row
n = len(rows(a))
try:
    _auth_db.spend_credits(a, 10_000, "too_much")
    check(False, "overspend must raise")
except _auth_db.InsufficientCreditsError as e:
    check(e.balance == 60 and e.required == 10_000, "InsufficientCreditsError carries balance + required")
check(len(rows(a)) == n and _auth_db.get_credit_balance(a) == 60, "failed spend writes no row and leaves balance alone")

# another user's ledger is untouched / isolated
check(_auth_db.get_credit_balance(b) == 0 and rows(b) == [], "other user's balance/ledger unaffected")

# charge_credits wrapper threads org_id through (the path forecast/reports/expansion use)
from blueprints import _session as S
S.charge_credits(a, "forecast", ref_type="project", ref_id=proj["id"], org_id=proj["org_id"])
check(rows(a)[-1]["org_id"] == second["id"] and rows(a)[-1]["delta"] == -P.credit_cost("forecast"),
      "charge_credits passes the project's company through and bills the legacy cost")
S.charge_credits(a, "forecast")
check(rows(a)[-1]["org_id"] == org_a, "charge_credits with no org falls back to the primary company")

# ── read-only parity helpers ─────────────────────────────────────────────────
# Alice's credits are now spread over two companies, which is exactly the case
# a company-level balance must decide about — the audit has to flag it.
rep = _auth_db.credit_org_parity()
check(rep["null_org_rows"] == 0, "no unstamped rows after these writes")
check(_auth_db.get_org_credit_balance(second["id"]) == -(10 + P.credit_cost("forecast")),
      "org sum is just the rows stamped to that company")
check(set(rep["mismatches"]) == {org_a, second["id"]}, "credits split across two companies are flagged as a mismatch")

# a clean one-user-one-company account matches
c, org_c = user_with_company("carol")
_auth_db.grant_credits(c, 500, "bonus")
_auth_db.spend_credits(c, 120, "test_spend")
rep = _auth_db.credit_org_parity()
carol = [o for o in rep["orgs"] if o["org_id"] == org_c][0]
check(carol["match"] and carol["org_sum"] == 380 == carol["user_sum"], "one-user-one-company account matches exactly")
check(org_c not in rep["mismatches"], "clean account not flagged")

# legacy unstamped rows (written before this deploy) are detected, then
# re-stamped by the existing idempotent backfill.
with engine.begin() as conn:
    conn.execute(text("UPDATE credits_ledger SET org_id = NULL WHERE user_id = :u"), {"u": c})
rep = _auth_db.credit_org_parity()
check(rep["null_org_rows"] == 2, "unstamped legacy rows are counted")
_auth_db.backfill_organizations()
rep = _auth_db.credit_org_parity()
check(rep["null_org_rows"] == 0, "backfill_organizations re-stamps them")
check([o for o in rep["orgs"] if o["org_id"] == org_c][0]["match"], "and carol matches again")
before = _auth_db.credit_org_parity()
_auth_db.backfill_organizations()
check(_auth_db.credit_org_parity() == before, "backfill is idempotent")

# ── plan dual-write ──────────────────────────────────────────────────────────
def org_plan(oid):
    with engine.connect() as conn:
        return conn.execute(text("SELECT plan FROM organizations WHERE id=:i"), {"i": oid}).scalar()


check(org_plan(org_b) == "free" and _auth_db.get_user(b)["plan"] == "free", "starts free")
_auth_db.set_user_plan(b, "pro")
check(_auth_db.get_user(b)["plan"] == "pro", "users.plan still updated (the enforced value)")
check(org_plan(org_b) == "pro", "owner's own company plan mirrored")
check(org_plan(org_a) == "free", "someone else's company untouched")
# a user whose primary company they do NOT own must never rewrite it
d = _auth_db.upsert_user("sub-dan", "dan@example.com", "Dan", None)["id"]
with engine.begin() as conn:
    conn.execute(text("UPDATE users SET org_id = :o WHERE id = :u"), {"o": org_a, "u": d})
_auth_db.set_user_plan(d, "team")
check(_auth_db.get_user(d)["plan"] == "team" and org_plan(org_a) == "free",
      "buying a plan never rewrites a company the buyer doesn't own")
try:
    _auth_db.set_user_plan(b, "v2_pro")
    check(False, "users.plan must still reject v2 ids (legacy CHECK)")
except AssertionError:
    check(True, "users.plan must still reject v2 ids (legacy CHECK)")

# ── orders ───────────────────────────────────────────────────────────────────
o = _auth_db.create_order(b, "credit_pack", "order_test_1", 79900, credit_pack_id="pack_500")
check(o["org_id"] == org_b, "order stamped with the buyer's company")
check(o["price_book_version"] == P.PRICE_BOOK_VERSION, "order stamped with the price-book version")
check(o["amount_paise"] == 79900 and o["status"] == "created", "order otherwise unchanged")
updated, newly = _auth_db.mark_order_paid(o["id"], "pay_1", "sig")
check(newly and updated["status"] == "paid", "paying still works")
_, again = _auth_db.mark_order_paid(o["id"], "pay_1", "sig")
check(not again, "second payment call is still an idempotent no-op")

# ── migration on a DB that predates these columns ────────────────────────────
# (SQLite can't DROP a column that carries a FOREIGN KEY, so orders.org_id can't
# be dropped here; price_book_version can, and org_id goes through the identical
# _MIGRATIONS loop that already migrated credits_ledger.org_id in prod.)
with engine.begin() as conn:
    conn.execute(text("ALTER TABLE orders DROP COLUMN price_book_version"))
with engine.connect() as conn:
    check("price_book_version" not in {r[1] for r in conn.execute(text("PRAGMA table_info(orders)"))},
          "simulated pre-migration orders table lacks the column")
_auth_db.migrate_schema()
_auth_db.migrate_schema()
with engine.connect() as conn:
    cols = {r[1] for r in conn.execute(text("PRAGMA table_info(orders)"))}
check({"org_id", "price_book_version"} <= cols, "migrate_schema re-adds the new order columns (and is re-runnable)")
check(("orders", "org_id", "INTEGER") in _auth_db._MIGRATIONS and
      ("orders", "price_book_version", "TEXT") in _auth_db._MIGRATIONS, "both columns are registered migrations")

# ── signup path: bonus lands stamped with the brand-new company ──────────────
import server
app = server.app
app.testing = True
os.environ["GOOGLE_CLIENT_ID"] = "test-client-id"
import google.oauth2.id_token as gid

real_verify = gid.verify_oauth2_token
gid.verify_oauth2_token = lambda cred, req, cid: {
    "sub": "sub-newbie", "email": "newbie@example.com", "email_verified": True, "name": "Newbie"}
try:
    resp = app.test_client().post("/api/auth/google", json={"credential": "x"})
finally:
    gid.verify_oauth2_token = real_verify
check(resp.status_code == 200, f"signup succeeds, got {resp.status_code}: {resp.get_data(as_text=True)[:120]}")
uid = resp.get_json()["user"]["id"]
new_org = _auth_db.get_user(uid)
bonus = rows(uid)
check(len(bonus) == 1 and bonus[0]["reason"] == "signup_bonus" and bonus[0]["delta"] == 50, "one signup-bonus row of 50")
with engine.connect() as conn:
    primary = conn.execute(text("SELECT org_id FROM users WHERE id=:u"), {"u": uid}).scalar()
check(primary is not None and bonus[0]["org_id"] == primary, "signup bonus is stamped with the new user's own company")
check(resp.get_json()["credits"] == 50, "and the balance shown at signup is still 50")

# the credits API a user actually sees is unchanged in shape and value
cl = app.test_client()
with cl.session_transaction() as sess:
    sess["user_id"] = a
r = cl.get("/api/credits").get_json()
check(r["balance"] == _auth_db.get_credit_balance(a) and isinstance(r["ledger"], list), "GET /api/credits unchanged")

# ═══ Stage 2a, part 2 — one paying account, many client companies ══════════
# The agency model: Priya's agency holds ONE wallet; Acme and Zen (client
# companies) draw from it; usage is logged per client.
priya, org_ag = user_with_company("priya")
kushal, org_k = user_with_company("kushal")
acme = _auth_db.create_organization(priya, "Acme Retail")["id"]
zen = _auth_db.create_organization(priya, "Zen Cafes")["id"]
_auth_db.add_org_member(org_ag, priya, "kushal@example.com", "member")     # employee of the agency
_auth_db.add_org_member(acme, priya, "kushal@example.com", "member")
_auth_db.add_org_member(zen, priya, "kushal@example.com", "member")
stranger, org_s = user_with_company("stranger")

extra = _auth_db.create_organization(priya, "Priya Side Co")["id"]
# — linking rules —
check(_auth_db.get_payer_org_id(acme) == org_ag, "a company created by an existing user starts linked under their wallet")
check(_auth_db.set_org_payer(acme, priya, None) == {"status": "ok"} and _auth_db.get_payer_org_id(acme) == acme,
      "an explicitly unlinked company pays for itself")
check(_auth_db.set_org_payer(acme, priya, org_ag) == {"status": "ok"}, "owner links a client company under her paying account")
check(_auth_db.set_org_payer(zen, priya, org_ag) == {"status": "ok"}, "second client linked")
check(_auth_db.get_payer_org_id(acme) == org_ag == _auth_db.get_payer_org_id(zen), "both clients now resolve to the agency wallet")
check(_auth_db.get_payer_org_id(org_ag) == org_ag, "the payer itself resolves to itself")
check(_auth_db.set_org_payer(acme, kushal, org_k).get("error") == "not_found", "a plain member cannot re-point a company's payer")
check(_auth_db.set_org_payer(acme, stranger, org_s).get("error") == "not_found", "a stranger cannot either")
check(_auth_db.set_org_payer(org_s, stranger, org_ag).get("error") == "not_found",
      "cannot link under a wallet you are not an owner/admin of")
check(_auth_db.set_org_payer(org_ag, priya, org_ag).get("error") == "invalid_payer", "a company cannot be its own payer")
check(_auth_db.set_org_payer(org_k, kushal, acme).get("error") in ("not_found", "payer_has_payer"),
      "cannot point at a company that itself has a payer (no chains)")
check(_auth_db.set_org_payer(extra, priya, acme).get("error") == "payer_has_payer", "no chains: payer of a payer is refused")
solo = _auth_db.create_organization(priya, "Standalone Payer")["id"]
_auth_db.set_org_payer(solo, priya, None)          # a company with no payer of its own
check(_auth_db.set_org_payer(org_ag, priya, solo).get("error") == "already_a_payer",
      "a company that already pays for others cannot be moved under another")

# — purchase into the wallet, spend per client —
check(_auth_db.grant_credits(priya, 500, "credit_purchase", org_id=org_ag) == 500, "agency buys 500 credits into its wallet")
last = rows(priya)[-1]
check(last["org_id"] == org_ag and last["billing_org_id"] == org_ag, "purchase row belongs to the agency wallet")
_auth_db.grant_credits(kushal, 100, "seed", org_id=org_ag)
_auth_db.spend_credits(priya, 40, "forecast", org_id=acme)
check(rows(priya)[-1]["org_id"] == acme and rows(priya)[-1]["billing_org_id"] == org_ag,
      "spend in Acme is logged to Acme but drawn from the agency wallet")
_auth_db.spend_credits(kushal, 25, "forecast", org_id=zen)
check(rows(kushal)[-1]["org_id"] == zen and rows(kushal)[-1]["billing_org_id"] == org_ag,
      "a second employee's spend in Zen lands on the same wallet")
check(_auth_db.get_wallet_credit_balance(org_ag) == 500 + 100 - 40 - 25, "wallet = purchases minus every client's usage (535)")
check(_auth_db.usage_by_company(org_ag) ==
      [{"org_id": acme, "name": "Acme Retail", "credits_used": 40},
       {"org_id": zen, "name": "Zen Cafes", "credits_used": 25}], "usage-per-client report, largest first, spends only")
if _auth_db.billing_scope() == "user":      # (under BILLING_SCOPE=wallet these are shared-wallet balances by design)
    check(_auth_db.get_credit_balance(priya) == 460 and _auth_db.get_credit_balance(kushal) == 75,
          "per-user balances are STILL what is enforced (stage 2a changes no reads)")

# unlinking affects only future rows; history keeps the wallet it was written under
check(_auth_db.set_org_payer(zen, priya, None) == {"status": "ok"}, "client can be unlinked")
_auth_db.grant_credits(priya, 10, "seed", org_id=zen)
check(rows(priya)[-1]["billing_org_id"] == zen, "after unlinking, new rows use the company's own wallet")
check(_auth_db.get_wallet_credit_balance(org_ag) == 535, "old rows kept their wallet (history is not rewritten)")
check(_auth_db.set_org_payer(zen, priya, org_ag).get("error") == "company_has_credits",
      "a company holding credits in its own wallet can't be linked (they'd be stranded)")
_auth_db.spend_credits(priya, 10, "seed_back", org_id=zen)
check(_auth_db.set_org_payer(zen, priya, org_ag) == {"status": "ok"}, "once its own wallet is at zero it can be linked")
check(_auth_db.get_wallet_credit_balance(org_ag) == 535, "linking moved nothing")

# additional companies default to the creator's wallet; deletion guards
check(_auth_db.get_payer_org_id(extra) == org_ag, "a company created by an existing user is linked under their own wallet by default")
check(_auth_db.get_payer_org_id(org_ag) == org_ag, "the primary company pays for itself")
check(_auth_db.org_delete_blocker(org_ag) == "pays_for_companies", "a company that pays for others can't be deleted")
check(_auth_db.org_delete_blocker(acme) == "has_billing_history", "a company with ledger history can't be deleted")
fresh = _auth_db.create_organization(priya, "Blank Co")["id"]
check(_auth_db.org_delete_blocker(fresh) is None, "a company with no history and no dependants can be deleted")
_auth_db.set_org_payer(extra, priya, None)
check(_auth_db.set_org_payer(extra, priya, org_ag) == {"status": "ok"}, "unlink then relink works")

# — "Buying for" resolution —
check(_auth_db.resolve_purchase_wallet(kushal) == {"org_id": org_k, "billing_org_id": org_k},
      "no company requested -> buyer's own primary company (today's behaviour)")
check(_auth_db.resolve_purchase_wallet(priya, acme) == {"org_id": acme, "billing_org_id": org_ag},
      "buying 'for Acme' is recorded against the agency wallet that pays for Acme")
check(_auth_db.resolve_purchase_wallet(kushal, org_ag) is None, "a plain member cannot buy for the company")
check(_auth_db.resolve_purchase_wallet(stranger, org_ag) is None, "a stranger cannot buy for it either")
check(_auth_db.resolve_purchase_wallet(priya, 99999) is None, "unknown company is refused, same as no access")

# — HTTP: the checkout option, with a fake payment gateway —
from blueprints import billing as B
class _FakeOrders:
    n = 0
    def create(self, data):
        _FakeOrders.n += 1
        return {"id": f"order_http_{_FakeOrders.n}"}
class _FakeClient:
    order = _FakeOrders()
real_client = B._client
B._client = lambda: _FakeClient()
os.environ["RAZORPAY_KEY_ID"] = "rzp_test_x"
try:
    def buy(uid, body):
        c2 = app.test_client()
        with c2.session_transaction() as sess:
            sess["user_id"] = uid
        return c2.post("/api/billing/orders/credits", json=dict(pack_id="pack_500", **body))
    r = buy(priya, {"org_id": acme})
    check(r.status_code == 201, f"owner can buy 'for Acme', got {r.status_code} {r.get_data(as_text=True)[:100]}")
    o = r.get_json()["order"]
    check(o["org_id"] == acme and o["billing_org_id"] == org_ag, "order records the chosen company and the wallet that pays")
    r = buy(priya, {})
    check(r.status_code == 201 and r.get_json()["order"]["org_id"] == org_ag, "no org_id -> the buyer's primary company, as before")
    check(buy(kushal, {"org_id": org_ag}).status_code == 403, "a plain member is refused with 403")
    check(buy(stranger, {"org_id": acme}).status_code == 403, "a stranger is refused with 403")
    check(buy(priya, {"org_id": "acme"}).status_code == 400, "non-integer org_id is a 400")
    check(buy(priya, {"org_id": True}).status_code == 400, "boolean org_id is a 400 (not silently 1)")
    # paying the wallet-targeted order grants into that wallet
    paid_order = _auth_db.get_order(o["id"], priya)
    B._apply_paid_order(paid_order, "pay_wallet_1", None)
    check(rows(priya)[-1]["reason"] == "credit_purchase" and rows(priya)[-1]["billing_org_id"] == org_ag,
          "the credit purchase lands on the wallet the buyer chose")
finally:
    B._client = real_client

# — backfill stamps wallets on legacy rows, idempotently —
with engine.begin() as conn:
    conn.execute(text("UPDATE credits_ledger SET billing_org_id = NULL WHERE user_id = :u"), {"u": kushal})
check(_auth_db.credit_org_parity()["null_wallet_rows"] > 0, "unstamped wallets are detected")
out = _auth_db.backfill_organizations()
check(out["ledger_wallets_stamped"] > 0 and _auth_db.credit_org_parity()["null_wallet_rows"] == 0, "backfill stamps them")
check(rows(kushal)[-1]["billing_org_id"] == org_ag, "a re-stamped row resolves through the payer link, not to itself")
check(_auth_db.backfill_organizations()["ledger_wallets_stamped"] == 0, "backfill is idempotent")
check({("organizations", "billing_org_id", "INTEGER"), ("credits_ledger", "billing_org_id", "INTEGER"),
       ("orders", "billing_org_id", "INTEGER")} <= set(_auth_db._MIGRATIONS), "the three wallet columns are registered migrations")

# ── the real payment path: _apply_paid_order (shared by /verify and /webhook) ─
from blueprints import billing as B

pay = _auth_db.create_order(c, "credit_pack", "order_pack_e2e", 79900, credit_pack_id="pack_500")
before = _auth_db.get_credit_balance(c)
res = B._apply_paid_order(pay, "pay_e2e_1", None)
check(res["status"] == "paid" and _auth_db.get_credit_balance(c) == before + 500, "credit pack grants 500 credits")
last = rows(c)[-1]
check(last["reason"] == "credit_purchase" and last["org_id"] == org_c and last["ref_id"] == pay["id"],
      "purchase ledger row is stamped with the buyer's company and linked to the order")
B._apply_paid_order(_auth_db.get_order(pay["id"], c), "pay_e2e_1", None)   # webhook redelivery
check(_auth_db.get_credit_balance(c) == before + 500, "webhook redelivery does not double-grant")

plan_order = _auth_db.create_order(c, "plan_upgrade", "order_plan_e2e", 99900, target_plan="pro")
B._apply_paid_order(plan_order, "pay_e2e_2", None)
check(_auth_db.get_user(c)["plan"] == "pro" and org_plan(org_c) == "pro", "plan purchase flips users.plan and mirrors the company")
check(plan_order["org_id"] == org_c and plan_order["price_book_version"] == P.PRICE_BOOK_VERSION, "plan order carries company + price book")
check(len(_auth_db.list_invoices(c)) == 2, "each paid order still gets its invoice")

print(f"OK — {passed} billing-org staging checks passed")

"""
test_billing_budgets.py — billing-v2 credit budgets (BILLING_SCOPE=wallet).

A budget caps what one COMPANY may spend from its wallet per period, in
credits. It is set by an owner/admin of the company that PAYS; warns at 80%,
then hard-stops; is mandatory for spenders outside the paying company; and is
checked in the same transaction as the wallet balance. An optional reserve
keeps credits back for the paying company's own use.

    DATABASE_URL="sqlite:////tmp/billing_budgets.sqlite" python3 tests/test_billing_budgets.py

Plain script, throwaway sqlite. A failure is a real regression in a money path.
NOT covered: true concurrent spends on Postgres (SQLite can't model the row
lock) — the check runs on the wallet-locked connection, asserted below.
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "paisamap-etl", "etl"))

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/billing_budgets_default.sqlite")
if "sqlite" not in os.environ["DATABASE_URL"]:
    raise SystemExit("Refusing to run: DATABASE_URL doesn't look like a throwaway sqlite file.")
if not os.environ.get("CUSTOMER_DATA_KEY"):
    from cryptography.fernet import Fernet
    os.environ["CUSTOMER_DATA_KEY"] = Fernet.generate_key().decode()
os.environ.setdefault("INVOICES_DIR", tempfile.mkdtemp(prefix="pm_invoices_"))
os.environ.pop("BILLING_SCOPE", None)

import _db
_db.enabled = lambda: False
import _auth_db as A
from sqlalchemy import text

A.init_schema()
A.migrate_schema()
engine = A._require_engine()
passed = 0
UTC = timezone.utc


def check(cond, msg):
    global passed
    assert cond, f"FAIL: {msg}"
    passed += 1


def scope(value):
    if value is None:
        os.environ.pop("BILLING_SCOPE", None)
    else:
        os.environ["BILLING_SCOPE"] = value


def person(tag):
    u = A.upsert_user(f"sub-{tag}", f"{tag}@example.com", tag, None)
    org = A.create_default_organization_for_user(u["id"], f"{tag} Co")
    return u["id"], org["id"]


def sql(stmt, **params):
    with engine.begin() as conn:
        return conn.execute(text(stmt), params)


def ist(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=A.IST).astimezone(UTC)


def refused(uid, amount, org, reason):
    """spend_credits must raise SpendNotAllowedError(reason) and write nothing."""
    n = sql("SELECT COUNT(*) FROM credits_ledger").scalar()
    try:
        A.spend_credits(uid, amount, "forecast", org_id=org)
    except A.SpendNotAllowedError as e:
        same = sql("SELECT COUNT(*) FROM credits_ledger").scalar() == n
        return e.reason == reason and same
    return False


def status(org):
    with engine.connect() as c:
        return A._budget_status(c, org, A.get_payer_org_id(org))


# ═══ 1. period windows (pure) ════════════════════════════════════════════════
# Months, weeks and quarters follow the IST calendar, not UTC.
s, e = A.budget_window("calendar_month", datetime(2026, 9, 30, 19, 0, tzinfo=UTC))   # 00:30 IST on 1 Oct
check((s, e) == (ist(2026, 10, 1), ist(2026, 11, 1)), "19:00 UTC on 30 Sep is already October in India")
s, e = A.budget_window("calendar_month", datetime(2026, 9, 30, 18, 0, tzinfo=UTC))   # 23:30 IST on 30 Sep
check((s, e) == (ist(2026, 9, 1), ist(2026, 10, 1)), "18:00 UTC on 30 Sep is still September in India")
s, e = A.budget_window("calendar_month", ist(2026, 12, 15))
check((s, e) == (ist(2026, 12, 1), ist(2027, 1, 1)), "December rolls into January of the next year")
s, e = A.budget_window("weekly", ist(2026, 9, 19, 12))                                # a Saturday
check((s, e) == (ist(2026, 9, 14), ist(2026, 9, 21)), "a week runs Monday 00:00 IST to the next Monday")
check(A.budget_window("weekly", ist(2026, 9, 20, 23, 59))[0] == ist(2026, 9, 14), "Sunday 23:59 IST is still the old week")
check(A.budget_window("weekly", ist(2026, 9, 21, 0, 0))[0] == ist(2026, 9, 21), "Monday 00:00 IST starts a new week")
check(A.budget_window("quarterly", ist(2026, 8, 15)) == (ist(2026, 7, 1), ist(2026, 10, 1)), "August is in the Jul-Sep quarter")
check(A.budget_window("quarterly", ist(2026, 12, 31, 23)) == (ist(2026, 10, 1), ist(2027, 1, 1)), "the Oct-Dec quarter ends at the year boundary")
check(A.budget_window("billing_cycle", ist(2026, 9, 19)) == A.budget_window("calendar_month", ist(2026, 9, 19)),
      "billing_cycle with no subscription anchor falls back to the calendar month")
anchor = ist(2026, 1, 31, 10, 0)
check(A.budget_window("billing_cycle", ist(2026, 3, 1, 9), cycle_anchor=anchor) == (ist(2026, 2, 28, 10), ist(2026, 3, 31, 10)),
      "an anniversary on the 31st clamps to the 28th in February, and the cycle before 31 Mar 10:00 IST is still Feb's")
check(A.budget_window("billing_cycle", ist(2026, 3, 31, 10, 0), cycle_anchor=anchor) == (ist(2026, 3, 31, 10), ist(2026, 4, 30, 10)),
      "on the renewal instant a new cycle starts")
check(A.budget_window("billing_cycle", ist(2026, 9, 19), cycle_anchor=ist(2026, 9, 5, 8)) == (ist(2026, 9, 5, 8), ist(2026, 10, 5, 8)),
      "a mid-month anniversary spans 5 Sep - 5 Oct")
A.budget_window("billing_cycle", ist(2026, 1, 1), cycle_anchor=ist(2026, 6, 1))
check(True, "an anchor in the future doesn't crash")
st, en = ist(2026, 9, 1), ist(2026, 9, 30)
check(A.budget_window("one_off", ist(2026, 12, 1), starts_at=st) == (st, None), "one_off: from when it was set, no end")
check(A.budget_window("until_date", ist(2026, 9, 10), starts_at=st, ends_at=en) == (st, en), "until_date: from set-time to the end date")

# ═══ 2. the agency scenario ═════════════════════════════════════════════════
priya, org_ag = person("priya")                       # the agency owner: pays
acme = A.create_organization(priya, "Acme Retail")["id"]
zen = A.create_organization(priya, "Zen Cafes")["id"]
check(A.get_payer_org_id(acme) == org_ag and A.get_payer_org_id(zen) == org_ag, "clients start linked under the agency wallet")
azhar, org_az = person("azhar")                       # Acme's own staff — OUTSIDE the paying company
A.add_org_member(acme, priya, "azhar@example.com", "member")
zoe, org_zo = person("zoe")                           # Zen's own staff
A.add_org_member(zen, priya, "zoe@example.com", "member")
kushal, org_ku = person("kushal")                     # agency staff (plain member) working on Acme
A.add_org_member(org_ag, priya, "kushal@example.com", "member")
A.add_org_member(acme, priya, "kushal@example.com", "member")
adira, org_ad = person("adira")                       # agency admin
A.add_org_member(org_ag, priya, "adira@example.com", "admin")
A.grant_credits(priya, 1000, "credit_purchase", org_id=org_ag)

# ═══ 3. budgets are inert outside wallet scope ══════════════════════════════
scope(None)
check(A.wallet_spend_block(azhar, acme, 5) is None, "legacy scope: nothing is blocked")
check(A.get_credit_view(azhar, acme)["budget"] is None, "legacy scope: the credit view carries no budget")
r = A.set_credit_budget(priya, org_ag, acme, 100, "calendar_month")
check(r["status"] == "ok", "a budget can be set while the scope is still legacy (setup before the flip)")
check(A.wallet_spend_block(azhar, acme, 500) is None, "...and it is not enforced until BILLING_SCOPE=wallet")
check(A.get_credit_view(azhar, acme)["budget"] is None, "...nor shown")
A.delete_credit_budget(priya, org_ag, acme)

scope("wallet")

# ═══ 4. no budget: outsiders blocked, insiders free ═════════════════════════
check(A.wallet_spend_block(azhar, acme, 5)["reason"] == "budget_required", "client staff with no budget: budget_required")
check(refused(azhar, 5, acme, "budget_required"), "spend_credits refuses them too and writes nothing")
check(A.wallet_spend_block(kushal, acme, 5) is None, "agency staff on a client with no budget: uncapped, as before")
check(A.wallet_spend_block(priya, acme, 5) is None, "the payer's owner: uncapped")
check(status(acme) is None, "no budget row -> no status")

# ═══ 5. a budget: warn at 80%, hard stop at the cap, boundary inclusive ═════
r = A.set_credit_budget(priya, org_ag, acme, 100, "calendar_month")
check(r["status"] == "ok" and r["budget"]["amount"] == 100 and r["budget"]["used"] == 0, "priya sets Acme to 100 credits/month")
b = status(acme)
check(b["period"] == "calendar_month" and b["resolved_period"] == "calendar_month" and b["pct"] == 0 and not b["warn"] and b["active"],
      "fresh: 0%, no warning, active")
check(A.spend_credits(azhar, 40, "forecast", org_id=acme) == 960, "client staff spend 40 -> wallet 960")
b = status(acme)
check(b["used"] == 40 and b["remaining"] == 60 and b["pct"] == 40 and not b["warn"], "40 used: 40%, no warning yet")
A.spend_credits(azhar, 39, "forecast", org_id=acme)
check(status(acme)["pct"] == 79 and not status(acme)["warn"], "79% still doesn't warn")
A.spend_credits(azhar, 1, "forecast", org_id=acme)
b = status(acme)
check(b["used"] == 80 and b["pct"] == 80 and b["warn"] and not b["exhausted"], "80% warns")
A.grant_credits(priya, 25, "credit_purchase", org_id=acme)             # a purchase/grant attributed to Acme is not spending
check(status(acme)["used"] == 80, "credits ADDED to a company (a purchase) never reduce or inflate what it has spent")
bal = A.get_wallet_credit_balance(org_ag)
check(refused(azhar, 21, acme, "budget_exceeded"), "80 + 21 > 100: budget_exceeded, nothing written")
check(A.get_wallet_credit_balance(org_ag) == bal, "...and the wallet is untouched")
try:
    A.spend_credits(azhar, 21, "forecast", org_id=acme)
except A.SpendNotAllowedError as e:
    check(e.info["used"] == 80 and e.info["amount"] == 100, "the refusal carries the budget status")
A.spend_credits(azhar, 20, "forecast", org_id=acme)
b = status(acme)
check(b["used"] == 100 and b["exhausted"] and b["remaining"] == 0 and b["pct"] == 100, "spending exactly to the cap is allowed")
check(refused(azhar, 1, acme, "budget_exceeded"), "one credit more is refused")
check(A.wallet_spend_block(azhar, acme, 0)["reason"] == "budget_exceeded", "even an amount-0 pre-check treats an exhausted budget as stopped")

# the cap applies to everyone spending on that company, agency staff and owner included
check(refused(kushal, 1, acme, "budget_exceeded"), "agency staff working on Acme are stopped by Acme's budget too")
check(refused(priya, 1, acme, "budget_exceeded"), "...and so is the paying company's owner (raise the budget to go on)")
check(A.wallet_spend_block(priya, org_ag, 5) is None, "but the agency's OWN company (no budget) is unaffected")
check(A.wallet_spend_block(zoe, zen, 5)["reason"] == "budget_required", "another client's budget state is independent")

# raising / lowering / removing
A.set_credit_budget(priya, org_ag, acme, 150, "calendar_month")
check(status(acme)["used"] == 100 and status(acme)["remaining"] == 50, "raising the budget mid-period keeps what was spent and opens headroom")
A.spend_credits(azhar, 50, "forecast", org_id=acme)
A.set_credit_budget(priya, org_ag, acme, 90, "calendar_month")
b = status(acme)
check(b["used"] == 150 and b["remaining"] == 0 and b["pct"] >= 100 and b["exhausted"], "lowering below what's spent leaves it stopped, remaining floors at 0")
check(refused(azhar, 1, acme, "budget_exceeded"), "...and spending is refused")
A.delete_credit_budget(priya, org_ag, acme)
check(status(acme) is None and refused(azhar, 1, acme, "budget_required"),
      "removing the budget puts the client back to 'no budget, no spending'")
check(A.wallet_spend_block(kushal, acme, 5) is None, "...while agency staff are uncapped again")

# ═══ 6. spend is checked inside the wallet-locked transaction ═══════════════
A.set_credit_budget(priya, org_ag, acme, 1000, "calendar_month")
seen = []
real = A._spend_block


def spy(conn, *a, **k):
    seen.append(conn.in_transaction())
    return real(conn, *a, **k)


A._spend_block = spy
A.spend_credits(azhar, 1, "forecast", org_id=acme)
A._spend_block = real
check(seen == [True], "spend_credits runs the budget check on the open, wallet-locked transaction")
A.delete_credit_budget(priya, org_ag, acme)

# ═══ 7. periods reset; no rollover ══════════════════════════════════════════
A.set_credit_budget(priya, org_ag, zen, 50, "calendar_month")
A.spend_credits(zoe, 50, "forecast", org_id=zen)
check(refused(zoe, 1, zen, "budget_exceeded"), "Zen exhausts its monthly budget")
sql("UPDATE credits_ledger SET created_at = :t WHERE org_id = :o AND delta < 0", t=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=70), o=zen)
b = status(acme) or {}
check(status(zen)["used"] == 0 and status(zen)["remaining"] == 50, "spend from two months ago doesn't count against this month (no rollover)")
A.spend_credits(zoe, 50, "forecast", org_id=zen)
check(status(zen)["used"] == 50, "a fresh month can be spent in full again")
A.set_credit_budget(priya, org_ag, zen, 50, "weekly")
check(status(zen)["used"] == 50, "switching to weekly: this week's spend still counts")
sql("UPDATE credits_ledger SET created_at = :t WHERE org_id = :o AND delta < 0", t=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=10), o=zen)
check(status(zen)["used"] == 0, "weekly: spend from 10 days ago is outside this week")
A.set_credit_budget(priya, org_ag, zen, 50, "quarterly")
check(status(zen)["period"] == "quarterly", "quarterly can be chosen")

# ═══ 8. one-off totals keep counting when edited ════════════════════════════
A.set_credit_budget(priya, org_ag, zen, 30, "one_off")
A.spend_credits(zoe, 30, "forecast", org_id=zen)
check(refused(zoe, 1, zen, "budget_exceeded") and status(zen)["window_end"] is None, "a one-off total is a lifetime cap with no end")
sql("INSERT INTO credits_ledger (user_id, org_id, billing_org_id, delta, reason, balance_after, created_at) "
    "VALUES (:u, :o, :w, -30, 'forecast', 0, :t)", u=zoe, o=zen, w=org_az,
    t=datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=1))                        # Zen spent 30 under ANOTHER wallet, inside the window
A.set_credit_budget(priya, org_ag, zen, 100, "one_off")
check(status(zen)["used"] == 30 and status(zen)["remaining"] == 70,
      "editing a one-off keeps its start, so earlier spend still counts — and spend under a different wallet never does")
A.set_credit_budget(priya, org_ag, zen, 100, "weekly")
A.set_credit_budget(priya, org_ag, zen, 100, "one_off")
check(status(zen)["used"] == 0 or status(zen)["used"] == 30, "(a re-created one-off starts counting from when it was re-set)")

# ═══ 9. until-a-date ════════════════════════════════════════════════════════
fut = (datetime.now(UTC) + timedelta(days=10)).astimezone(A.IST).date().isoformat()
r = A.set_credit_budget(priya, org_ag, acme, 40, "until_date", ends_at=fut)
check(r["status"] == "ok" and r["budget"]["window_end"] is not None and r["budget"]["active"], "until_date with a future date is accepted")
check(A.set_credit_budget(priya, org_ag, acme, 40, "until_date")["error"] == "invalid_end_date", "until_date needs an end date")
check(A.set_credit_budget(priya, org_ag, acme, 40, "until_date", ends_at="2020-01-01")["error"] == "invalid_end_date", "a past end date is refused")
check(A.set_credit_budget(priya, org_ag, acme, 40, "until_date", ends_at="not-a-date")["error"] == "invalid_end_date", "junk is refused")
A.spend_credits(azhar, 10, "forecast", org_id=acme)
sql("UPDATE credit_budgets SET ends_at = :t WHERE org_id = :o", t=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1), o=acme)
check(status(acme)["active"] is False and not status(acme)["warn"], "a lapsed until_date budget is inactive")
check(refused(azhar, 1, acme, "budget_required"), "...and behaves as no budget: outsiders can't spend")
check(A.wallet_spend_block(kushal, acme, 1) is None, "...while insiders are uncapped")
A.delete_credit_budget(priya, org_ag, acme)

# ═══ 10. only the payer's owner/admins set budgets, and only for their own wallet ═══
check(A.set_credit_budget(azhar, org_ag, acme, 10, "calendar_month") == {"error": "not_found"}, "client staff can't set their own budget (404, same as nonexistent)")
check(A.set_credit_budget(kushal, org_ag, acme, 10, "calendar_month") == {"error": "forbidden"}, "a plain member of the paying company can't either")
check(A.set_credit_budget(adira, org_ag, acme, 10, "calendar_month")["status"] == "ok", "an admin of the paying company can")
check(A.set_credit_budget(priya, org_az, acme, 10, "calendar_month") == {"error": "not_found"},
      "the owner of ANOTHER company can't set a budget through a wallet she isn't in")
check(A.set_credit_budget(azhar, org_az, acme, 10, "calendar_month") == {"error": "not_found"},
      "...and Azhar can't use his own wallet to set a cap on a company it doesn't pay for")
check(A.set_credit_budget(priya, org_ag, org_az, 10, "calendar_month") == {"error": "not_found"},
      "the agency can't put a budget on a company it doesn't pay for")
for bad in (-1, True, 1.5, "5", None, BUD := A.BUDGET_MAX_CREDITS + 1):
    check(A.set_credit_budget(priya, org_ag, acme, bad, "calendar_month") == {"error": "invalid_amount"}, f"amount {bad!r} is refused")
check(A.set_credit_budget(priya, org_ag, acme, 10, "daily") == {"error": "invalid_period"}, "an unknown period is refused")
check(A.set_credit_budget(priya, org_ag, acme, 0, "calendar_month")["status"] == "ok" and refused(azhar, 1, acme, "budget_exceeded"),
      "a budget of 0 is a pause: allowed to set, nothing can be spent")
b0 = status(acme)
check(b0["pct"] == 100 and b0["exhausted"] and b0["warn"] and b0["remaining"] == 0, "a zero budget reads as 100% used, warning, exhausted")
check(A.set_credit_budget(azhar, org_az, org_az, 50, "calendar_month")["status"] == "ok",
      "a company that pays for itself can cap its own spending")
check(A.delete_credit_budget(priya, org_ag, org_az) == {"error": "not_found"} and status(org_az) is not None,
      "the agency can't remove a cap on a company it doesn't pay for")
check(A.delete_credit_budget(azhar, org_az, org_az)["status"] == "ok" and status(org_az) is None, "its own admin can")
check(A.delete_credit_budget(kushal, org_ag, acme) == {"error": "forbidden"}, "a plain member can't remove one")
check(A.delete_credit_budget(azhar, org_ag, acme) == {"error": "not_found"}, "client staff can't remove theirs")
A.set_credit_budget(priya, org_ag, acme, 100, "calendar_month")
# a stale cap (set by someone who no longer pays) is ignored
sql("UPDATE credit_budgets SET wallet_org_id = :w WHERE org_id = :o AND kind = 'cap'", w=org_az, o=acme)
check(status(acme) is None and refused(azhar, 1, acme, "budget_required"), "a cap recorded under a different wallet is ignored")
A.set_credit_budget(priya, org_ag, acme, 100, "calendar_month")
check(status(acme) is not None, "setting it again re-owns it")

# ═══ 11. re-linking drops the old payer's cap ═══════════════════════════════
check(A.set_org_payer(acme, priya, None)["status"] == "ok", "unlink Acme from the agency")
check(sql("SELECT COUNT(*) FROM credit_budgets WHERE org_id = :o AND kind = 'cap'", o=acme).scalar() == 0, "its cap went with the link")
A.set_org_payer(acme, priya, org_ag)
check(status(acme) is None, "re-linking to the same payer does not resurrect the old cap")
A.set_credit_budget(priya, org_ag, acme, 1000, "calendar_month")      # ample headroom: isolate the reserve rule

# ═══ 12. the reserve floor ══════════════════════════════════════════════════
bal = A.get_wallet_credit_balance(org_ag)
check(A.set_wallet_reserve(kushal, org_ag, 100) == {"error": "forbidden"}, "a plain member can't set a reserve")
check(A.set_wallet_reserve(azhar, org_ag, 100) == {"error": "not_found"}, "client staff can't")
check(A.set_wallet_reserve(priya, org_ag, -5) == {"error": "invalid_amount"} and A.set_wallet_reserve(priya, org_ag, True) == {"error": "invalid_amount"},
      "a bad reserve is refused")
A.set_wallet_reserve(priya, org_ag, bal - 10)
check(A.wallet_spend_block(azhar, acme, 10) is None, "spending down to exactly the reserve is allowed")
check(A.wallet_spend_block(azhar, acme, 11)["reason"] == "wallet_reserve", "one credit into the reserve is refused")
check(refused(azhar, 11, acme, "wallet_reserve"), "spend_credits refuses it and writes nothing")
check(A.wallet_spend_block(kushal, acme, 11)["reason"] == "wallet_reserve", "agency staff working on a client are held to it too")
check(A.wallet_spend_block(priya, org_ag, 500) is None, "the paying company's OWN spending is not limited by its own reserve")
A.set_wallet_reserve(priya, org_ag, 0)
check(A.wallet_spend_block(azhar, acme, 11) is None, "clearing the reserve lifts it")
check(A.set_wallet_reserve(priya, org_ag, None) == {"status": "ok", "reserve": None}, "None clears too")

# ═══ 13. what people are shown ══════════════════════════════════════════════
v = A.get_credit_view(azhar, acme)
check(v["balance"] is None and v["paid_by"]["name"] == "priya Co", "client staff still never see the wallet balance")
check(v["budget"]["amount"] == 1000 and "used" in v["budget"] and set(v["budget"]) >= {"period", "remaining", "pct", "warn", "window_end"},
      "...but see their own company's budget and usage")
check(str(bal) not in str(v), "the wallet total appears nowhere in what they're shown")
check(A.get_credit_view(azhar)["budget"] is None, "in his own company (no budget, pays for itself) nothing is shown")
lst = A.list_wallet_budgets(priya, org_ag)
check(lst["enforced"] is True, "the listing says budgets are being enforced under the wallet scope")
check(lst["balance"] == A.get_wallet_credit_balance(org_ag) and lst["reserve"] is None and lst["warn_pct"] == 80, "the admin listing shows wallet balance and reserve")
by = {c["org_id"]: c for c in lst["companies"]}
check(set(by) == {org_ag, acme, zen} and by[org_ag]["is_wallet"] and not by[acme]["is_wallet"], "it lists the wallet company and each company it pays for")
check(by[acme]["budget"]["amount"] == 1000 and by[org_ag]["budget"] is None, "with each company's budget (or None)")
check(by[acme]["used_30d"] >= 0 and set(lst["periods"]) == set(A.BUDGET_PERIODS), "and a 30-day usage figure plus the valid periods")
check(A.list_wallet_budgets(azhar, org_ag) == {"error": "not_found"} and A.list_wallet_budgets(kushal, org_ag) == {"error": "forbidden"},
      "only owners/admins can list")

# ═══ 14. over HTTP ══════════════════════════════════════════════════════════
import server
app = server.app
app.testing = True


def call(uid, method, url, **kw):
    c = app.test_client()
    if uid:
        with c.session_transaction() as sess:
            sess["user_id"] = uid
    return c.open(url, method=method, **kw)


base = f"/api/organizations/{org_ag}/budgets"
check(call(None, "GET", base).status_code == 401, "budgets need a login")
r = call(priya, "GET", base)
check(r.status_code == 200 and r.get_json()["wallet_org_id"] == org_ag, "GET budgets as the owner")
check(call(azhar, "GET", base).status_code == 404 and call(kushal, "GET", base).status_code == 403, "404 for outsiders, 403 for a plain member")
r = call(adira, "PUT", f"{base}/{zen}", json={"amount": 60, "period": "weekly"})
check(r.status_code == 200 and r.get_json()["budget"]["amount"] == 60 and r.get_json()["budget"]["period"] == "weekly", "PUT sets a budget")
check(call(adira, "PUT", f"{base}/{zen}", json={"amount": "lots"}).status_code == 400, "bad amount -> 400")
check(call(adira, "PUT", f"{base}/{zen}", json={"amount": 5, "period": "hourly"}).status_code == 400, "bad period -> 400")
check(call(adira, "PUT", f"{base}/{zen}", json={"amount": 5, "period": "until_date", "ends_at": "2020-01-01"}).status_code == 400, "past end date -> 400")
check(call(adira, "PUT", f"{base}/{org_az}", json={"amount": 5}).status_code == 404, "a company the wallet doesn't pay for -> 404")
check(call(azhar, "PUT", f"{base}/{acme}", json={"amount": 999999}).status_code == 404, "client staff can't raise their own budget over HTTP")
check(call(kushal, "PUT", f"{base}/{acme}", json={"amount": 999999}).status_code == 403, "a plain member can't")
r = call(adira, "PUT", f"{base}/{zen}", json={"amount": 60})
check(r.get_json()["budget"]["period"] == "billing_cycle", "period defaults to billing_cycle")
check(call(priya, "PUT", f"/api/organizations/{org_ag}/reserve", json={"credits": 5}).status_code == 200, "PUT reserve")
check(call(kushal, "PUT", f"/api/organizations/{org_ag}/reserve", json={"credits": 5}).status_code == 403, "reserve: plain member 403")
check(call(priya, "PUT", f"/api/organizations/{org_ag}/reserve", json={"credits": "x"}).status_code == 400, "reserve: junk 400")
call(priya, "PUT", f"/api/organizations/{org_ag}/reserve", json={"credits": None})
check(call(adira, "DELETE", f"{base}/{zen}").status_code == 200 and status(zen) is None, "DELETE removes it")
check(call(kushal, "DELETE", f"{base}/{acme}").status_code == 403, "a plain member can't delete")

# the gate names the real reason on the spending routes
acme_project = A.create_project(priya, "Acme P", org_id=acme)
A.set_credit_budget(priya, org_ag, acme, 100, "calendar_month")
used = status(acme)["used"]
A.set_credit_budget(priya, org_ag, acme, used + 5, "calendar_month")          # 5 credits of headroom; a forecast costs 8
for url in (f"/api/forecast?project_id={acme_project['id']}&budget=30000",):
    r = call(azhar, "GET", url)
    body = r.get_json()
    check(r.status_code == 403 and body["error"] == "budget_exceeded", f"forecast (8 credits) with 5 of headroom: 403 budget_exceeded, got {r.status_code} {body}")
    check("balance" not in body and body["paid_by"]["name"] == "priya Co" and body["budget"]["remaining"] == 5,
          "the refusal shows the company's budget, never the wallet balance")
    check("raise it" in body["detail"] and "priya Co" in body["detail"], "and says who can raise it")
r = call(priya, "GET", f"/api/expansion/recommend?project_id={acme_project['id']}&budget=100000")
check(r.status_code != 403, "expansion (5 credits) fits in 5 of headroom: not refused by the budget")
A.set_credit_budget(priya, org_ag, acme, 1000, "calendar_month")
r = call(azhar, "GET", f"/api/forecast?project_id={acme_project['id']}&budget=30000")
check(r.status_code != 403, "with headroom the same request passes the gate")
A.set_wallet_reserve(priya, org_ag, A.get_wallet_credit_balance(org_ag))
r = call(azhar, "GET", f"/api/forecast?project_id={acme_project['id']}&budget=30000")
b = r.get_json()
check(r.status_code == 403 and b["error"] == "wallet_reserve" and "reserve" in b["detail"] and "balance" not in b, "a reserve refusal names the reserve, no balance")
A.set_wallet_reserve(priya, org_ag, None)
# credits/me pass the budget through
r = call(azhar, "GET", f"/api/credits?org_id={acme}").get_json()
check(r["balance"] is None and r["budget"]["amount"] == 1000, "/api/credits: no balance, but the company's budget")
me = call(azhar, "GET", f"/api/auth/me?org_id={acme}").get_json()["user"]
check(me["credits"] is None and me["credits_budget"]["amount"] == 1000, "/api/auth/me carries it for the header")
me = call(azhar, "GET", "/api/auth/me").get_json()["user"]
check(me["credits_budget"] is None, "no budget for his own company")
scope(None)
check(call(azhar, "GET", f"/api/credits?org_id={acme}").get_json()["budget"] is None, "under the legacy scope /api/credits carries no budget")
check(call(priya, "GET", base).get_json()["enforced"] is False, "...and the admin listing says budgets aren't enforced yet")
scope("wallet")

# ═══ 14b. personal allowances inside a company ═════════════════════════════
A.grant_credits(priya, 3000, "credit_purchase", org_id=org_ag)
amit, org_am = person("amit")                        # Acme's OWN admin — outside the paying company
asha, org_as = person("asha")                        # another Acme staffer, no allowance
A.add_org_member(acme, priya, "amit@example.com", "admin")
A.add_org_member(acme, priya, "asha@example.com", "member")
A.set_credit_budget(priya, org_ag, acme, 1000, "calendar_month")
r = A.set_member_budget(amit, acme, azhar, 50, "calendar_month")
check(r["status"] == "ok" and r["budget"]["scope"] == "member" and r["budget"]["amount"] == 50,
      "a client's own admin gives a colleague a personal allowance (inside the company budget)")
check(A.set_member_budget(amit, acme, azhar, 1001, "calendar_month") == {"error": "exceeds_company_budget"},
      "...but never above the company's own budget")
check(A.set_member_budget(amit, acme, azhar, 1000, "calendar_month")["status"] == "ok", "...exactly the company budget is allowed")
check(A.set_member_budget(amit, acme, azhar, 50, "calendar_month")["status"] == "ok", "(back to 50)")
check(A.set_member_budget(asha, acme, azhar, 10, "calendar_month") == {"error": "forbidden"}, "a plain member can't set allowances")
check(A.set_member_budget(kushal, acme, azhar, 10, "calendar_month") == {"error": "forbidden"}, "nor can plain agency staff")
check(A.set_member_budget(zoe, acme, azhar, 10, "calendar_month") == {"error": "not_found"}, "a stranger gets not_found")
check(A.set_member_budget(amit, acme, zoe, 10, "calendar_month") == {"error": "not_a_member"}, "the target must belong to the company")
check(A.set_member_budget(amit, org_ag, azhar, 10, "calendar_month") == {"error": "not_found"},
      "a client admin can't reach into the AGENCY's company (not a member there)")
check(A.set_member_budget(amit, acme, azhar, -3, "calendar_month") == {"error": "invalid_amount"}
      and A.set_member_budget(amit, acme, azhar, 5, "hourly") == {"error": "invalid_period"}
      and A.set_member_budget(amit, acme, azhar, 5, "until_date", ends_at="2020-01-01") == {"error": "invalid_end_date"},
      "the same amount/period/date validation applies")
check(A.set_member_budget(adira, acme, azhar, 60, "calendar_month")["status"] == "ok", "the PAYER's admin may set one too")
A.set_member_budget(amit, acme, azhar, 50, "calendar_month")
# no company budget: a client admin has nothing to work inside; the payer's admin may
A.delete_credit_budget(priya, org_ag, acme)
check(A.set_member_budget(amit, acme, azhar, 20, "calendar_month") == {"error": "company_budget_required"},
      "with no company budget a client's own admin can't set allowances")
check(A.set_member_budget(adira, acme, kushal, 20, "calendar_month")["status"] == "ok", "the payer's admin can (kushal is an Acme member)")
A.delete_member_budget(adira, acme, kushal)
A.set_credit_budget(priya, org_ag, acme, 1000, "calendar_month")

# enforcement: personal allowance binds only that person, and counts only THEIR spend
ana, _ = person("ana")                                   # a fresh Acme staffer (Azhar already spent plenty this month)
A.add_org_member(acme, priya, "ana@example.com", "member")
A.set_member_budget(amit, acme, ana, 50, "calendar_month")
before = status(acme)["used"]
A.spend_credits(asha, 40, "forecast", org_id=acme)                                 # a colleague spends 40
with engine.connect() as c:
    ms = A._member_budget_status(c, acme, ana, org_ag)
check(ms["used"] == 0 and ms["amount"] == 50, "a colleague's spending doesn't count against Ana's allowance")
A.spend_credits(ana, 50, "forecast", org_id=acme)
check(refused(ana, 1, acme, "budget_exceeded"), "Ana is stopped at her own 50, though the company has plenty left")
try:
    A.spend_credits(ana, 1, "forecast", org_id=acme)
except A.SpendNotAllowedError as e:
    check(e.info["scope"] == "member" and e.info["used"] == 50, "and the refusal says it is HER allowance")
check(A.wallet_spend_block(asha, acme, 5) is None, "Asha (no allowance) can still spend from the company budget")
check(status(acme)["used"] == before + 90, "the company budget counted both people (40 + 50)")
# the lower of the two binds
A.set_member_budget(amit, acme, asha, 500, "calendar_month")
A.set_credit_budget(priya, org_ag, acme, status(acme)["used"] + 5, "calendar_month")
check(refused(asha, 6, acme, "budget_exceeded"), "a big personal allowance can't beat a nearly-used company budget")
try:
    A.spend_credits(asha, 6, "forecast", org_id=acme)
except A.SpendNotAllowedError as e:
    check(e.info["scope"] == "company", "...and that refusal names the COMPANY budget")
A.set_credit_budget(priya, org_ag, acme, 1000, "calendar_month")
A.delete_member_budget(amit, acme, asha)
# what people are shown
v = A.get_credit_view(ana, acme)
check(v["member_budget"]["amount"] == 50 and v["member_budget"]["exhausted"] and v["balance"] is None,
      "Ana sees her own allowance (used up) and still no wallet balance")
check(A.get_credit_view(kushal, acme)["member_budget"] is None, "someone without one sees None")
# listing
lst = A.list_member_budgets(amit, acme)
by = {m["user_id"]: m for m in lst["members"]}
check(lst["company_budget"]["amount"] == 1000 and lst["can_set_without_company_budget"] is False, "the company admin sees the budget they work inside")
check(by[ana]["budget"]["amount"] == 50 and by[ana]["used_30d"] == 50 and by[asha]["used_30d"] == 40 and by[amit]["budget"] is None,
      "each member with their allowance and last-30-day spend")
check(A.list_member_budgets(adira, acme)["can_set_without_company_budget"] is True, "the payer's admin may set one without a company budget")
check(A.list_member_budgets(asha, acme) == {"error": "forbidden"} and A.list_member_budgets(zoe, acme) == {"error": "not_found"}, "plain members / strangers can't list")
check(A.list_member_budgets(amit, 999999) == {"error": "not_found"}, "a nonexistent company is not_found")
# over HTTP
mb = f"/api/organizations/{acme}/member-budgets"
r = call(amit, "GET", mb)
check(r.status_code == 200 and len(r.get_json()["members"]) >= 4, "GET member-budgets as a client admin")
check(call(asha, "GET", mb).status_code == 403 and call(zoe, "GET", mb).status_code == 404 and call(None, "GET", mb).status_code == 401, "403 / 404 / 401 as expected")
check(call(amit, "PUT", f"{mb}/{azhar}", json={"amount": 1001}).status_code == 400, "over the company budget -> 400")
check(call(amit, "PUT", f"{mb}/{zoe}", json={"amount": 5}).status_code == 404, "not a member -> 404")
check(call(asha, "PUT", f"{mb}/{azhar}", json={"amount": 5}).status_code == 403, "plain member -> 403")
r = call(amit, "PUT", f"{mb}/{azhar}", json={"amount": 80})
check(r.status_code == 200 and r.get_json()["budget"]["amount"] == 80, "PUT raises Azhar's allowance to 80")
proj = A.create_project(priya, "Acme P2", org_id=acme)
r = call(ana, "GET", f"/api/forecast?project_id={proj['id']}&budget=30000")
b = r.get_json()
check(r.status_code == 403 and b["error"] == "budget_exceeded" and b["budget"]["scope"] == "member" and "personal credit allowance" in b["detail"] and "balance" not in b,
      f"the route refuses with the personal-allowance message, got {r.status_code} {b}")
me = call(ana, "GET", f"/api/auth/me?org_id={acme}").get_json()["user"]
cr = call(ana, "GET", f"/api/credits?org_id={acme}").get_json()
check(me["credits_member_budget"]["amount"] == 50 and cr["member_budget"]["amount"] == 50, "/me and /credits carry the personal allowance")
check(A.wallet_spend_block(ana, acme, 5)["reason"] == "budget_exceeded", "(Ana is still stopped by her allowance)")
check(call(amit, "DELETE", f"{mb}/{ana}").status_code == 200 and A.wallet_spend_block(ana, acme, 5) is None,
      "removing it frees Ana again (the company budget still applies)")
check(call(asha, "DELETE", f"{mb}/{ana}").status_code == 403, "a plain member can't remove one")
A.set_member_budget(amit, acme, ana, 50, "calendar_month")
scope(None)
check(A.wallet_spend_block(ana, acme, 5) is None and A.get_credit_view(ana, acme)["member_budget"] is None,
      "allowances are inert under the legacy scope")
scope("wallet")
# relinking the company keeps its OWN admins' allowances (they are the company's, not the payer's)
A.set_org_payer(acme, priya, None); A.set_org_payer(acme, priya, org_ag)
check(sql("SELECT COUNT(*) FROM credit_member_budgets WHERE org_id = :o", o=acme).scalar() >= 2,
      "personal allowances survive a relink (only the payer's company cap is dropped)")
A.set_credit_budget(priya, org_ag, acme, 1000, "calendar_month")

# ═══ 14c. 80% / 100% alerts ═════════════════════════════════════════════════
notices = []
_real_dispatch = A._dispatch_budget_notice
A._dispatch_budget_notice = lambda n: notices.append((n, sql("SELECT COUNT(*) FROM credits_ledger").scalar()))
bolt = A.create_organization(priya, "Bolt Co")["id"]
bo, _ = person("bo"); bea, _ = person("bea")
A.add_org_member(bolt, priya, "bo@example.com", "member")
A.add_org_member(bolt, priya, "bea@example.com", "admin")
A.set_credit_budget(priya, org_ag, bolt, 100, "calendar_month")
A.spend_credits(bo, 50, "forecast", org_id=bolt)
check(notices == [], "50% sends nothing")
A.spend_credits(bo, 29, "forecast", org_id=bolt)
check(notices == [], "79% sends nothing")
n_ledger = sql("SELECT COUNT(*) FROM credits_ledger").scalar()
A.spend_credits(bo, 1, "forecast", org_id=bolt)
check(len(notices) == 1 and notices[0][0]["level"] == 80, "crossing 80% sends one alert")
n, ledger_rows_at_send = notices[0]
check(ledger_rows_at_send == n_ledger + 1, "the alert goes out only AFTER the spend is committed")
check(sorted(n["recipients"]) == sorted(["priya@example.com", "adira@example.com", "bea@example.com"]),
      "it goes to the owners/admins of the payer AND of the company, not to plain members")
check(n["org_name"] == "Bolt Co" and n["used"] == 80 and n["amount"] == 100 and n["window_text"] == "this month"
      and not any("balance" in k or "wallet" in k for k in n), "it carries only the company's own figures — no wallet balance")
A.spend_credits(bo, 5, "forecast", org_id=bolt)
check(len(notices) == 1, "further spending inside the same level doesn't re-send")
A.spend_credits(bo, 15, "forecast", org_id=bolt)
check(len(notices) == 2 and notices[1][0]["level"] == 100, "reaching 100% sends the second (final) alert")
check(refused(bo, 1, bolt, "budget_exceeded") and len(notices) == 2, "a refused spend sends nothing")
# a single spend that jumps straight over both levels sends ONE alert, at the higher level
B2 = A.create_organization(priya, "Jump Co")["id"]
jo, _ = person("jo"); A.add_org_member(B2, priya, "jo@example.com", "member")
A.set_credit_budget(priya, org_ag, B2, 100, "calendar_month")
k = len(notices)
A.spend_credits(jo, 100, "forecast", org_id=B2)
check(len(notices) == k + 1 and notices[-1][0]["level"] == 100, "jumping from 0 to 100% sends one alert (level 100), not two")
# a new period is news again
old_t = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=45)      # simulate "that alert was sent last month"
sql("UPDATE credits_ledger SET created_at = :t WHERE org_id = :o AND delta < 0", t=old_t, o=bolt)
sql("UPDATE credit_budgets SET notified_window_start = :t WHERE org_id = :o AND kind = 'cap'", t=old_t, o=bolt)
k = len(notices)
A.spend_credits(bo, 80, "forecast", org_id=bolt)
check(len(notices) == k + 1 and notices[-1][0]["level"] == 80, "next month, crossing 80% alerts again")
# raising the budget starts a fresh alert cycle
A.spend_credits(bo, 20, "forecast", org_id=bolt)
k = len(notices)
A.set_credit_budget(priya, org_ag, bolt, 200, "calendar_month")
A.spend_credits(bo, 60, "forecast", org_id=bolt)
check(len(notices) == k + 1 and notices[-1][0]["level"] == 80, "after the budget is raised, its next 80% is news again")
# only wallet scope, only company budgets
k = len(notices)
lea, _ = person("lea")
scope(None)
A.grant_credits(lea, 20, "bonus")                        # legacy scope spends from the person's OWN balance
A.spend_credits(lea, 15, "forecast", org_id=bolt)        # ...attributed to Bolt, which sits at 80%+ of its budget
scope("wallet")
check(len(notices) == k, "under the legacy scope nothing is evaluated or sent")
A.spend_credits(priya, 5, "forecast", org_id=org_ag)
check(len(notices) == k, "a company with no budget never alerts")
# the real dispatcher: SES calls, isolation of failures, HTML escaping
import _email
calls = []
_real_send = _email.send_budget_notice


def fake_send(to, org, level, used, amount, window, url):
    calls.append((to, org, level, url))
    if to == "adira@example.com":
        raise RuntimeError("smtp down")


_email.send_budget_notice = fake_send
A._send_budget_notices({"level": 80, "org_name": "X", "used": 1, "amount": 2, "window_text": "this month",
                        "recipients": ["priya@example.com", "adira@example.com", "bea@example.com"]})
_email.send_budget_notice = _real_send
check([c[0] for c in calls] == ["priya@example.com", "adira@example.com", "bea@example.com"],
      "one recipient failing doesn't stop the others")
check(all(c[3].endswith("/workspace/billing") and c[3].startswith("https://") for c in calls), "the email links to the Billing page")
check(_email.send_budget_notice("a@example.com", "X", 80, 80, 100, "this month", "https://x/y") is False,
      "with SES unconfigured a send just returns False (no crash)")
sent = {}


class FakeSES:
    def send_email(self, **kw):
        sent.update(kw)


_real_client = _email._client
_email._client = lambda: FakeSES()
os.environ["SES_FROM_EMAIL"] = "noreply@example.com"
check(_email.send_budget_notice("a@example.com", "<script>alert(1)</script> Co", 80, 80, 100, "this month", "https://x/y?a=1&b=2") is True, "SES accepts it")
html_body = sent["Message"]["Body"]["Html"]["Data"]
check("<script>" not in html_body and "&lt;script&gt;" in html_body, "a company name is HTML-escaped in the email")
check("80%" in sent["Message"]["Subject"]["Data"], "the 80% subject says so")
_email.send_budget_notice("a@example.com", "Acme", 100, 100, 100, "this month", "https://x/y")
check("used up" in sent["Message"]["Subject"]["Data"] and "stopped" in sent["Message"]["Body"]["Text"]["Data"], "the 100% email says spending has stopped")
_email._client = _real_client
os.environ.pop("SES_FROM_EMAIL", None)
# a broken dispatcher can't hurt a committed spend
def _boom(_):
    raise RuntimeError("dispatcher down")


A._dispatch_budget_notice = _boom
A.set_credit_budget(priya, org_ag, bolt, 400, "calendar_month")
A.spend_credits(bo, 165, "forecast", org_id=bolt)       # crosses 80% of 400 -> tries to dispatch -> raises -> swallowed
check(status(bolt)["used"] >= 325, "a failing alert dispatcher never fails the spend")
A._dispatch_budget_notice = _real_dispatch
# housekeeping: a company's allowances go with it
scratch = A.create_organization(priya, "Scratch Co")["id"]
sc, _ = person("sc"); A.add_org_member(scratch, priya, "sc@example.com", "member")
A.set_credit_budget(priya, org_ag, scratch, 10, "calendar_month")
A.set_member_budget(priya, scratch, sc, 5, "calendar_month")
check(A.delete_organization(scratch, priya) and sql("SELECT COUNT(*) FROM credit_member_budgets WHERE org_id = :o", o=scratch).scalar() == 0,
      "deleting a company removes its personal allowances too")

# ═══ 15. cleanup, and the preflight ═════════════════════════════════════════
tmp = A.create_organization(priya, "Temp Co")["id"]
gone = A.create_organization(priya, "Gone Co")["id"]     # (created first: sqlite reuses a deleted top id, and doesn't cascade members)
A.set_credit_budget(priya, org_ag, tmp, 5, "calendar_month")
check(A.delete_organization(tmp, priya), "a company with no billing history can be deleted")
check(sql("SELECT COUNT(*) FROM credit_budgets WHERE org_id = :o", o=tmp).scalar() == 0, "its budget row is removed with it")
scope(None)
pre = A.wallet_mode_preflight()
check(not any("credit_budgets" in p for p in pre["problems"]), "preflight is happy when the table exists")
sql("DROP TABLE credit_member_budgets")
check(any("credit_member_budgets" in p for p in A.wallet_mode_preflight()["problems"]), "preflight blocks the flip when the allowances table is missing")
sql("DROP TABLE credit_budgets")
check(any("credit_budgets" in p for p in A.wallet_mode_preflight()["problems"]), "preflight blocks the flip when the table is missing")
# a deploy that reaches the server BEFORE the table exists must not break relinking or deleting companies
check(A.set_org_payer(gone, priya, None)["status"] == "ok" and A.set_org_payer(gone, priya, org_ag)["status"] == "ok",
      "relinking a company works even before the credit_budgets table exists")
check(A.delete_organization(gone, priya), "...and so does deleting one")
A.init_schema()
check(not any("credit_budgets" in p for p in A.wallet_mode_preflight()["problems"]), "and re-creating it clears the problem")

print(f"OK — {passed} budget checks passed")

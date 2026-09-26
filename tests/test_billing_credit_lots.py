"""
test_billing_credit_lots.py — P3: monthly plan credits that expire.

  grant    -> each paid subscription charge adds the tier's monthly credits to the
              paying company's wallet as a lot, exactly once per payment (redelivery,
              a race, or a crash between "paid" and "granted" never doubles/loses it)
  expiry   -> a lot expires PLAN_CREDIT_ROLLOVER_DAYS after its cycle ends; what is
              left is written off as a 'credits_expired' ledger row (never taking a
              wallet below zero); reads, spends and the hourly sweep all do it, once
  spending -> soonest-expiring lot first; credits with no lot never expire and go last
  display  -> /api/credits says how many plan credits expire next and when — only to
              members of the paying company (it's the payer's number)
  dark     -> with no credit_lots table everything behaves exactly as before

    python3 tests/test_billing_credit_lots.py

Plain script, throwaway sqlite, same conventions as the other suites.
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "paisamap-etl", "etl"))

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='pm_lots_')}/lots.sqlite")
if "sqlite" not in os.environ["DATABASE_URL"]:
    raise SystemExit("Refusing to run: DATABASE_URL doesn't look like a throwaway sqlite file.")
if not os.environ.get("CUSTOMER_DATA_KEY"):
    from cryptography.fernet import Fernet
    os.environ["CUSTOMER_DATA_KEY"] = Fernet.generate_key().decode()
os.environ.setdefault("INVOICES_DIR", tempfile.mkdtemp(prefix="pm_invoices_"))
os.environ["BILLING_SCOPE"] = "wallet"
os.environ["RAZORPAY_KEY_ID"] = "rzp_test_lots"
os.environ["RAZORPAY_KEY_SECRET"] = "lots_secret"
os.environ["RAZORPAY_WEBHOOK_SECRET"] = "whsec_lots"

import _db
_db.enabled = lambda: False
import _auth_db as A
import _pricing
import _subscriptions as S
from sqlalchemy import text

A.init_schema()
A.migrate_schema()
engine = A._require_engine()

import server
app = server.app
app.testing = True
UTC = timezone.utc
passed = 0
GROWTH = _pricing.TIERS["growth"]["credits_per_month"]
ROLL = timedelta(days=_pricing.PLAN_CREDIT_ROLLOVER_DAYS)


def check(cond, msg):
    global passed
    assert cond, f"FAIL: {msg}"
    passed += 1


def sql(stmt, **p):
    with engine.begin() as conn:
        return conn.execute(text(stmt), p)


def now():
    return datetime.now(UTC)


_n = [0]


def person():
    _n[0] += 1
    u = A.upsert_user(f"lots-{_n[0]}", f"l{_n[0]}@example.com", f"L{_n[0]}", None)["id"]
    org = A.create_default_organization_for_user(u, f"L{_n[0]} Co")["id"]
    return u, org


def paid_sub(uid, org, tier="growth", *, end=None):
    """A subscription with one captured charge. Returns (sid, charge result)."""
    _n[0] += 1
    sid = f"sub_L{_n[0]}"
    S.create_local(org, uid, tier, "monthly", sid, f"plan_L{_n[0]}",
                   _pricing.subscription_amount_paise(tier, charge_gst=False))
    end = end or (now() + timedelta(days=30))
    res = S.apply_charge(sid, f"pay_{sid}", _pricing.subscription_amount_paise(tier, charge_gst=False),
                         current_start=end - timedelta(days=30), current_end=end, paid_count=1,
                         event_ts=now())
    return sid, res


def lots(org):
    return [dict(r) for r in sql("SELECT * FROM credit_lots WHERE billing_org_id=:o ORDER BY id", o=org).mappings()]


def ledger(org, reason):
    return [dict(r) for r in sql("SELECT * FROM credits_ledger WHERE billing_org_id=:o AND reason=:r ORDER BY id",
                                 o=org, r=reason).mappings()]


def aware(dt):
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def expire_lot(lot_id, days_ago=1):
    sql("UPDATE credit_lots SET expires_at=:t WHERE id=:i", t=now() - timedelta(days=days_ago), i=lot_id)


# ── 1. a paid charge grants the month's credits, once ─────────────────────────
u, org = person()
before = A.get_credit_balance(u, org)
end = now() + timedelta(days=30)
sid, res = paid_sub(u, org, end=end)
check(res["credits_granted"] is True, "the first charge grants credits")
check(A.get_credit_balance(u, org) == before + GROWTH, f"Growth adds {GROWTH} credits")
L = lots(org)
check(len(L) == 1 and L[0]["amount"] == GROWTH and L[0]["remaining"] == GROWTH and L[0]["kind"] == "plan", "one plan lot")
check(abs((aware(L[0]["expires_at"]) - (end + ROLL)).total_seconds()) < 2,
      "it expires one rollover period after the cycle ends")
order = A.get_order_by_razorpay_id(f"sub_charge_pay_{sid}")
check(L[0]["source_ref"] == f"order:{order['id']}", "the lot is keyed on the charge's order")
g = ledger(org, "plan_credits")
check(len(g) == 1 and g[0]["delta"] == GROWTH and g[0]["ref_type"] == "order" and g[0]["ref_id"] == order["id"],
      "one ledger row, pointing at the order")

again = S.apply_charge(sid, f"pay_{sid}", 1_200_000, current_start=end - timedelta(days=30), current_end=end,
                       paid_count=1, event_ts=now())
check(again["credits_granted"] is False and len(lots(org)) == 1 and len(ledger(org, "plan_credits")) == 1,
      "a redelivered charge grants nothing more")
check(A.get_credit_balance(u, org) == before + GROWTH, "balance unchanged by the redelivery")
check(A.grant_plan_credits(u, org, GROWTH, source_ref=f"order:{order['id']}", expires_at=end)["granted"] is False,
      "granting the same source twice is refused")

# a second month is a second lot
S.apply_charge(sid, f"pay_{sid}_m2", 1_200_000, current_start=end, current_end=end + timedelta(days=30),
               paid_count=2, event_ts=now())
check(len(lots(org)) == 2 and A.get_credit_balance(u, org) == before + 2 * GROWTH, "the next month adds another lot")

# crash between "paid" and "granted": the next delivery heals it
u2, org2 = person()
_n[0] += 1
sid2 = f"sub_L{_n[0]}"
S.create_local(org2, u2, "growth", "monthly", sid2, "plan_crash", 1_200_000)
real = A.grant_plan_credits
A.grant_plan_credits = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("crash"))
try:
    S.apply_charge(sid2, "pay_crash", 1_200_000, current_end=now() + timedelta(days=30), paid_count=1, event_ts=now())
    check(False, "setup: the simulated crash should raise")
except RuntimeError:
    pass
A.grant_plan_credits = real
check(A.get_order_by_razorpay_id("sub_charge_pay_crash")["status"] == "paid" and lots(org2) == [],
      "setup: paid but not granted")
healed = S.apply_charge(sid2, "pay_crash", 1_200_000, current_end=now() + timedelta(days=30), paid_count=1, event_ts=now())
check(healed["credits_granted"] is True and len(lots(org2)) == 1, "the redelivery grants what the crash lost")

# a tier with no monthly credits grants nothing
check(S.grant_cycle_credits({"tier": "trial", "org_id": org2}, {"id": 0, "user_id": u2}) is None,
      "no credits_per_month -> no lot")

# ── 2. spending: soonest-expiring lot first, never-expiring credits last ──────
u3, org3 = person()
A.grant_credits(u3, 50, reason="signup_bonus", org_id=org3)   # no lot: never expires
bonus = A.get_credit_balance(u3, org3)
A.grant_plan_credits(u3, org3, 100, source_ref="t:late", expires_at=now() + timedelta(days=50))
A.grant_plan_credits(u3, org3, 60, source_ref="t:soon", expires_at=now() + timedelta(days=5))
A.spend_credits(u3, 40, reason="report_generate", org_id=org3)
rem = {l["source_ref"]: l["remaining"] for l in lots(org3)}
check(rem == {"t:late": 100, "t:soon": 20}, f"the soonest lot is drawn first: {rem}")
A.spend_credits(u3, 50, reason="report_generate", org_id=org3)
rem = {l["source_ref"]: l["remaining"] for l in lots(org3)}
check(rem == {"t:late": 70, "t:soon": 0}, f"then the next one: {rem}")
A.spend_credits(u3, 70 + 5, reason="report_generate", org_id=org3)
rem = {l["source_ref"]: l["remaining"] for l in lots(org3)}
check(rem == {"t:late": 0, "t:soon": 0}, "lots are used up before the never-expiring credits")
check(A.get_credit_balance(u3, org3) == bonus - 5, "and the rest comes from the never-expiring credits")

# ── 3. expiry: write off what is left, once, never below zero ────────────────
u4, org4 = person()
A.grant_credits(u4, 50, reason="signup_bonus", org_id=org4)
bonus4 = A.get_credit_balance(u4, org4)
A.grant_plan_credits(u4, org4, 300, source_ref="e:1", expires_at=now() + timedelta(days=10))
A.spend_credits(u4, 100, reason="report_generate", org_id=org4)
lot = lots(org4)[0]
expire_lot(lot["id"])
check(A.get_credit_balance(u4, org4) == bonus4, "a read writes off the 200 left in the expired lot")
x = ledger(org4, "credits_expired")
check(len(x) == 1 and x[0]["delta"] == -200 and x[0]["ref_type"] == "credit_lot" and x[0]["ref_id"] == lot["id"],
      "one 'credits_expired' row for what was left")
check(lots(org4)[0]["remaining"] == 0 and lots(org4)[0]["expired_at"] is not None, "the lot is closed")
A.get_credit_balance(u4, org4)
A.get_credit_view(u4, org4)
check(len(ledger(org4, "credits_expired")) == 1, "reading again writes nothing more")

# a fully used lot expires silently
A.grant_plan_credits(u4, org4, 30, source_ref="e:used", expires_at=now() + timedelta(days=10))
A.spend_credits(u4, 30, reason="report_generate", org_id=org4)
expire_lot(lots(org4)[-1]["id"])
A.get_credit_balance(u4, org4)
check(len(ledger(org4, "credits_expired")) == 1, "a used-up lot expires without a ledger row")

# expired credits can't pay for a spend, even before anything wrote them off
u5, org5 = person()
sql("DELETE FROM credits_ledger WHERE user_id=:u", u=u5)          # no signup credits: only the lot
A.grant_plan_credits(u5, org5, 50, source_ref="e:spend", expires_at=now() + timedelta(days=3))
expire_lot(lots(org5)[0]["id"])
try:
    A.spend_credits(u5, 10, reason="report_generate", org_id=org5)
    check(False, "an expired lot must not pay for a spend")
except A.InsufficientCreditsError:
    check(True, "an expired lot can't pay for a spend")
check(A.get_credit_balance(u5, org5) == 0 and len(ledger(org5, "credits_expired")) == 1,
      "the spend attempt wrote it off (in its own transaction) and left 0")

# the spend-order helper on its own also skips an expired lot nobody has written off yet
u5b, org5b = person()
A.grant_plan_credits(u5b, org5b, 40, source_ref="e:stale", expires_at=now() + timedelta(days=3))
A.grant_plan_credits(u5b, org5b, 40, source_ref="e:fresh", expires_at=now() + timedelta(days=9))
expire_lot([l for l in lots(org5b) if l["source_ref"] == "e:stale"][0]["id"])
with engine.begin() as conn:
    A._consume_lots(conn, org5b, 10)
rem = {l["source_ref"]: l["remaining"] for l in lots(org5b)}
check(rem == {"e:stale": 40, "e:fresh": 30}, f"an expired lot is never drawn from: {rem}")
A.get_credit_balance(u5b, org5b)       # tidy up: write it off before the sweep tests count wallets

# never below zero: something else already drew the balance down
u6, org6 = person()
sql("DELETE FROM credits_ledger WHERE user_id=:u", u=u6)
A.grant_plan_credits(u6, org6, 100, source_ref="e:clamp", expires_at=now() + timedelta(days=3))
A.grant_credits(u6, -70, reason="manual_adjustment", org_id=org6)   # an operator correction, no lot
expire_lot(lots(org6)[0]["id"])
check(A.get_credit_balance(u6, org6) == 0, "only the 30 actually there are written off")
check(ledger(org6, "credits_expired")[0]["delta"] == -30, "the write-off is clamped to the balance")

# ── 4. the hourly sweep, across wallets ───────────────────────────────────────
u7, org7 = person()
u8, org8 = person()
A.grant_plan_credits(u7, org7, 11, source_ref="s:7", expires_at=now() + timedelta(days=3))
A.grant_plan_credits(u8, org8, 22, source_ref="s:8", expires_at=now() + timedelta(days=3))
A.grant_plan_credits(u8, org8, 44, source_ref="s:8b", expires_at=now() + timedelta(days=30))
expire_lot(lots(org7)[0]["id"])
expire_lot([l for l in lots(org8) if l["source_ref"] == "s:8"][0]["id"])
check(A.expire_due_credits() == 33, "the sweep writes off 11 + 22 in two wallets")
check(A.expire_due_credits() == 0, "and a second run finds nothing")
check([l["remaining"] for l in lots(org8) if l["source_ref"] == "s:8b"] == [44], "a lot still in date is untouched")

# ── 5. what people are shown ──────────────────────────────────────────────────
u9, org9 = person()
at1 = now() + timedelta(days=4)
A.grant_plan_credits(u9, org9, 120, source_ref="v:1", expires_at=at1)
A.grant_plan_credits(u9, org9, 80, source_ref="v:2", expires_at=at1)
A.grant_plan_credits(u9, org9, 500, source_ref="v:3", expires_at=now() + timedelta(days=40))
v = A.get_credit_view(u9, org9)
check(v["expiring"]["credits"] == 200 and abs((aware(v["expiring"]["at"]) - at1).total_seconds()) < 2,
      "the view shows the credits expiring soonest (both lots due that day) and when")
c = app.test_client()
with c.session_transaction() as sess:
    sess["user_id"] = u9
body = c.get(f"/api/credits?org_id={org9}").get_json()
check(body["expiring"]["credits"] == 200 and aware(body["expiring"]["at"]).tzinfo is not None,
      "/api/credits returns it with a timezone")
check(any(r["reason"] == "plan_credits" for r in body["ledger"]), "the grant appears in the credit history")

# a client company's own staff never see the payer's numbers
agency_u, agency = u9, org9
client_owner, client = person()
check(A.set_org_payer(client, client_owner, None)["status"] == "ok", "setup: client pays for itself first")
sql("UPDATE organizations SET billing_org_id=:a WHERE id=:c", a=agency, c=client)   # agency pays for it now
cv = A.get_credit_view(client_owner, client)
check(cv["balance"] is None and cv["expiring"] is None, "client staff: no balance, no expiry numbers")

# user scope: lots play no part
os.environ["BILLING_SCOPE"] = "user"
check(A.get_credit_view(u9, org9).get("expiring") is None, "user scope: nothing about expiry")
spent_before = [l["remaining"] for l in lots(org9)]
A.spend_credits(u9, 5, reason="report_generate", org_id=org9)
check([l["remaining"] for l in lots(org9)] == spent_before, "user scope: a spend doesn't touch the lots")
A.grant_plan_credits(u9, org9, 7, source_ref="v:userscope", expires_at=now() + timedelta(days=2))
expire_lot([l for l in lots(org9) if l["source_ref"] == "v:userscope"][0]["id"])
x_before = len(ledger(org9, "credits_expired"))
A.get_credit_balance(u9, org9); A.get_credit_view(u9, org9); A.list_credit_ledger(u9, org_id=org9)
check(len(ledger(org9, "credits_expired")) == x_before, "user scope: reads never write anything off (legacy behaviour exactly)")
os.environ["BILLING_SCOPE"] = "wallet"

# ── 6. before the table exists, nothing changes ──────────────────────────────
sql("DROP TABLE credit_lots")
A._lots_ok, A._lots_recheck_at = False, 0.0
S._ready, S._not_ready_until = False, 0.0
u10, org10 = person()
A.grant_credits(u10, 10, reason="signup_bonus", org_id=org10)
b10 = A.get_credit_balance(u10, org10)
check(A.grant_plan_credits(u10, org10, 10, source_ref="d:1", expires_at=now()) is None, "no table: the grant is a no-op")
A.spend_credits(u10, 1, reason="report_generate", org_id=org10)
check(A.get_credit_balance(u10, org10) == b10 - 1, "no table: spending works exactly as before")
check(A.get_credit_view(u10, org10)["expiring"] is None and A.expire_due_credits() == 0, "no table: no expiry, no sweep")
check(S.tables_ready() is False, "subscriptions don't count as ready without credit_lots")

print(f"OK — {passed} checks passed")

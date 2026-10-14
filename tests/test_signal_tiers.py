"""
test_signal_tiers.py — billing-v2 P7: the ₹200 / ₹500 signal-only tiers.

One throwaway sqlite, no network:
  1. _pricing: where the two tiers sit on the ladder (above Free, below Trial,
     never the dashboard), what they unlock, that they're sold like any other
     self-serve monthly plan and never assigned by the legacy plan mapping.
  2. _signals_data: the per-plan signal allow-list with SIGNAL_TIERS off (the old
     free/Pro split, unchanged) and on (free = 3 core only).
  3. HTTP: /api/export, /api/signals/catalog and /api/auth/me agree for anonymous,
     free, Signals Lite, Signals Pro and dashboard callers.
  4. Billing: a signal plan is not on sale while SIGNAL_TIERS is off; moving up
     from a live signal plan cancels the old one at Razorpay (so the company never
     holds two live subscriptions — the unique index would mark the new one
     'duplicate' and grant nothing) but keeps what was already paid for.

    DATABASE_URL="sqlite:///$(mktemp -d)/t.sqlite" python3 tests/test_signal_tiers.py
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "paisamap-etl", "etl"))

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/signal_tiers.sqlite")
if "sqlite" not in os.environ["DATABASE_URL"]:
    raise SystemExit("Refusing to run: DATABASE_URL doesn't look like a throwaway sqlite file.")
if not os.environ.get("CUSTOMER_DATA_KEY"):
    from cryptography.fernet import Fernet
    os.environ["CUSTOMER_DATA_KEY"] = Fernet.generate_key().decode()
os.environ.setdefault("INVOICES_DIR", tempfile.mkdtemp(prefix="pm_invoices_"))
for k in ("BILLING_SUBSCRIPTIONS", "BILLING_LIVE_PURCHASES", "BILLING_SCOPE", "SIGNAL_TIERS",
          "DASHBOARD_PAYWALL"):
    os.environ.pop(k, None)
os.environ["RAZORPAY_KEY_ID"] = "rzp_test_sig"
os.environ["RAZORPAY_KEY_SECRET"] = "sig_secret"
os.environ["RAZORPAY_WEBHOOK_SECRET"] = "whsec_sig"

import _db
_db.enabled = lambda: False
import _auth_db as A
import _pricing as P
import _plan_mapping
import _signals_data as SD
import _subscriptions as S
import _trial
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


def flag(on):
    if on:
        os.environ["SIGNAL_TIERS"] = "1"
    else:
        os.environ.pop("SIGNAL_TIERS", None)


LITE = P.plan_id_v2("signals_lite")
SPRO = P.plan_id_v2("signals_pro")
LITE_SIGNALS = set(P.SIGNAL_TIERS["lite"]["extra_signals"])
ALL = set(SD.SIGNAL_COLUMNS)

# ═════════════════════════════════════════════════════════════════════════════
# 1. _pricing
# ═════════════════════════════════════════════════════════════════════════════
order = P.TIER_ORDER
check(order.index("free") < order.index("signals_lite") < order.index("signals_pro") < order.index("trial")
      < order.index("starter"), "ladder: free < Signals Lite < Signals Pro < trial < starter")
for t in ("signals_lite", "signals_pro"):
    check(P.is_signal_tier(t), f"{t} is a signal tier")
    check(not P.is_dashboard_tier(P.plan_id_v2(t)), f"{t} does not include the dashboard")
    check(P.compat_plan(P.plan_id_v2(t)) == "free", f"{t} reads as 'free' to the old Pro surfaces")
    check(P.TIERS[t]["credits_per_month"] == 0, f"{t} carries no credits")
    check(P.subscription_error(t) is None, f"{t} is a self-serve monthly subscription")
    check(not P.entitlements(P.plan_id_v2(t))["api_elevated"], f"{t} has no raised API limit")
    check(t not in _plan_mapping.MAPPABLE_TIERS, f"{t} is never a legacy-mapping target")
    check(P.subscription_error(t, "annual") == "cycle_not_self_serve", f"{t}: monthly only")
check(not P.is_signal_tier("growth") and not P.is_signal_tier("trial"), "dashboard tiers are not signal tiers")
check(P.subscription_amount_paise("signals_lite", charge_gst=True) == 23_600, "Signals Lite: Rs 200 + GST = Rs 236")
check(P.subscription_amount_paise("signals_pro", charge_gst=True) == 59_000, "Signals Pro: Rs 500 + GST = Rs 590")
check(P.tier_label(LITE) == "Signals Lite" and P.tier_label(SPRO) == "Signals Pro", "labels")
check(P.entitlements(LITE)["signals"] == P.SIGNAL_TIERS["lite"]["extra_signals"], "Lite: its own list")
check(P.entitlements(SPRO)["signals"] == "all" and P.entitlements(SPRO)["pro_columns"], "Signals Pro: every signal")
check(P.entitlements("free")["signals"] == () and P.entitlements("junk")["signals"] == (), "free/unknown: core only")
check(P.entitlements("pro")["signals"] == "all", "legacy Pro keeps every signal")
for t in ("trial", "starter", "growth", "scale", "pro", "enterprise"):
    check(P.entitlements(P.plan_id_v2(t))["signals"] == "all", f"dashboard tier {t}: every signal")
strength = [P.plan_strength(p) for p in ("free", LITE, SPRO, P.plan_id_v2("trial"), P.plan_id_v2("starter"))]
check(strength == sorted(strength) and len(set(strength)) == 5, "plan_strength orders free < Lite < Pro < trial < starter")
check(P.best_plan("free", LITE) == LITE and P.best_plan(LITE, P.plan_id_v2("growth")) == P.plan_id_v2("growth"),
      "best_plan: a signal plan beats free, a dashboard plan beats a signal plan")
check(_plan_mapping.propose_tier("pro") == "growth" and _plan_mapping.propose_tier("team") == "growth",
      "legacy mapping unchanged by the new tiers")

# ═════════════════════════════════════════════════════════════════════════════
# 2. _signals_data allow-list
# ═════════════════════════════════════════════════════════════════════════════
check(LITE_SIGNALS < ALL and len(LITE_SIGNALS) == 9, "Lite's 9 signals are all real, served signals")
check("rate_per_sqft" in ALL and "cars_per_1000" not in ALL, "signal columns = the export's (no duplicate alias)")
flag(False)
check(SD.allowed_signals("free") == ALL - SD.PRO_COLUMNS == {"rate_per_sqft"}, "flag off: free sees the non-Pro signal, as before")
check(SD.allowed_signals("pro") == ALL and SD.allowed_signals(P.plan_id_v2("growth")) == ALL, "flag off: paid sees all")
check(SD.allowed_signals(LITE) == {"rate_per_sqft"}, "flag off: a Lite plan is worth nothing extra (so it isn't sold)")
flag(True)
check(SD.allowed_signals("free") == frozenset() and SD.allowed_signals("junk") == frozenset(), "flag on: free = core only")
check(SD.allowed_signals(LITE) == LITE_SIGNALS, "flag on: Lite = its list")
check("rate_per_sqft" not in SD.allowed_signals(LITE), "flag on: property rate is a Signals Pro signal")
for p in (SPRO, "pro", "team", P.plan_id_v2("trial"), P.plan_id_v2("starter"), P.plan_id_v2("enterprise")):
    check(SD.allowed_signals(p) == ALL, f"flag on: {p} sees every signal")
cols = SD.EXPORT_ALL_COLUMNS
kept = SD.columns_for_plan(cols, "free")
check(set(kept) == set(SD.EXPORT_CORE_FIELDS), "flag on: free export = identity + PPI/income/spend only")
check(set(SD.columns_for_plan(cols, LITE)) == set(SD.EXPORT_CORE_FIELDS) | LITE_SIGNALS, "flag on: Lite export")
check(SD.columns_for_plan(cols, SPRO) == cols, "flag on: Signals Pro export = everything")
check(SD.columns_for_plan(["pincode", "bogus_col"], "free") == ["pincode", "bogus_col"],
      "non-signal columns are not this filter's business")
check({SD.signal_tier_of(c) for c in P.CORE_SIGNALS} == {"core"}, "tier_of: core")
check({SD.signal_tier_of(c) for c in LITE_SIGNALS} == {"lite"}, "tier_of: lite")
check(SD.signal_tier_of("rate_per_sqft") == "pro" and SD.signal_tier_of("ev_share") == "pro", "tier_of: pro")
flag(False)

# ═════════════════════════════════════════════════════════════════════════════
# 3. HTTP surfaces agree
# ═════════════════════════════════════════════════════════════════════════════
os.environ["BILLING_SCOPE"] = "wallet"


def person(tag):
    u = A.upsert_user(f"sig-{tag}", f"{tag}@example.com", tag, None)
    org = A.create_default_organization_for_user(u["id"], f"{tag} Co")
    return u["id"], org["id"]


def set_plan(org_id, plan):
    sql("UPDATE organizations SET plan=:p WHERE id=:i", p=plan, i=org_id)


def plan_of(org_id):
    return sql("SELECT plan FROM organizations WHERE id=:i", i=org_id).scalar()


def client_as(uid=None):
    c = app.test_client()
    if uid is not None:
        with c.session_transaction() as sess:
            sess["user_id"] = uid
    return c




def export_cols(uid=None):
    r = client_as(uid).get("/api/export?format=csv&dataset=ppi&scope=all")
    assert r.status_code == 200, r.status_code
    return set(r.get_data(as_text=True).splitlines()[0].split(","))


def catalog(uid=None):
    return client_as(uid).get("/api/signals/catalog").get_json()


def me(uid):
    return client_as(uid).get("/api/auth/me").get_json()


free_u, free_o = person("fran")
lite_u, lite_o = person("lina")
spro_u, spro_o = person("sipho")
dash_u, dash_o = person("dara")
set_plan(lite_o, LITE)
set_plan(spro_o, SPRO)
set_plan(dash_o, P.plan_id_v2("growth"))
core = set(SD.EXPORT_CORE_FIELDS)


def expect(uid, want, label):
    got = export_cols(uid) - core
    check(got == want, f"{label}: export signal columns {sorted(got)} == {sorted(want)}")
    cat = catalog(uid)
    unlocked = {s["key"] for s in cat["signals"] if s["key"] in ALL and not s["locked"]}
    check(unlocked == want, f"{label}: catalog unlocks exactly what the export serves")
    check(set(cat["access"]["allowed"]) == want, f"{label}: catalog access.allowed matches")
    check(not any(s["locked"] for s in cat["signals"] if s["key"] in P.CORE_SIGNALS), f"{label}: core never locked")
    if uid is not None:
        check(set(me(uid)["user"]["signals"]["allowed"]) == want, f"{label}: /me agrees")


flag(False)
expect(None, {"rate_per_sqft"}, "flag off, anonymous")
expect(free_u, {"rate_per_sqft"}, "flag off, free")
expect(dash_u, ALL, "flag off, Growth")
check(catalog(None)["access"]["enforced"] is False, "catalog says the tiers are off")
flag(True)
expect(None, set(), "flag on, anonymous")
expect(free_u, set(), "flag on, free")
expect(lite_u, LITE_SIGNALS, "flag on, Signals Lite")
expect(spro_u, ALL, "flag on, Signals Pro")
expect(dash_u, ALL, "flag on, Growth")
cat = catalog(lite_u)
check(cat["access"]["enforced"] is True and cat["access"]["plan_label"] == "Signals Lite", "catalog names the plan")
check({s["key"]: s["tier"] for s in cat["signals"]}["rate_per_sqft"] == "pro", "catalog tier for property rate")
m = me(lite_u)
check(m["plan"] == "free" and m["user"]["tier_label"] == "Signals Lite", "/me: no Pro pill, but the plan's own name")
# Explicitly asking for a column the plan lacks still gets it stripped.
r = client_as(lite_u).get("/api/export?format=csv&dataset=ppi&scope=all&columns=ev_share,msme_per_lakh")
hdr = set(r.get_data(as_text=True).splitlines()[0].split(","))
check("ev_share" not in hdr and "msme_per_lakh" in hdr, "requested-but-not-included column is stripped")
# A signal plan never opens the dashboard paywall.
os.environ["DASHBOARD_PAYWALL"] = "1"
check(A.dashboard_access(spro_u, spro_o)["allowed"] is False, "Signals Pro: no dashboard behind the paywall")
check(A.dashboard_access(dash_u, dash_o)["allowed"] is True, "Growth: dashboard")
os.environ.pop("DASHBOARD_PAYWALL")
flag(False)

# ═════════════════════════════════════════════════════════════════════════════
# 4. Billing — on sale only with the flag; moving up
# ═════════════════════════════════════════════════════════════════════════════


class _Plan:
    def create(self, data):
        FakeRP.plan_creates += 1
        return {"id": f"plan_rp_{FakeRP.plan_creates}"}


class _Subscription:
    def create(self, data):
        if FakeRP.fail_create:
            raise RuntimeError("gateway down")
        FakeRP.sub_creates += 1
        FakeRP.last_sub_payload = data
        return {"id": f"sub_rp_{FakeRP.sub_creates}"}

    def fetch(self, sid):
        return FakeRP.sub_entities[sid]

    def cancel(self, sid, data):
        if FakeRP.fail_cancel:
            raise RuntimeError("gateway down")
        FakeRP.cancels.append((sid, data))
        return {"id": sid}


class FakeRP:
    plan_creates = 0
    sub_creates = 0
    cancels = []
    fail_create = False
    fail_cancel = False
    last_sub_payload = None
    sub_entities = {}
    plan = _Plan()
    subscription = _Subscription()
    utility = razorpay.Client(auth=("rzp_test_sig", "sig_secret")).utility


B._client = lambda: FakeRP()
os.environ["BILLING_SUBSCRIPTIONS"] = "1"


def post(uid, path, body):
    return client_as(uid).post(f"/api/billing{path}", json=body)


def options(uid):
    return {o["tier"]: o for o in client_as(uid).get("/api/billing/subscriptions").get_json()["options"]}


buy_u, buy_o = person("bilal")
flag(False)
check(B.subscriptions_state()["enabled"] is True, "subscriptions open for this section")
check(not {"signals_lite", "signals_pro"} & set(options(buy_u)), "flag off: signal plans are not offered")
r = post(buy_u, "/subscriptions", {"tier": "signals_lite"})
check(r.status_code == 400 and r.get_json()["error"] == "not_offered", "flag off: buying one is refused")
check(FakeRP.sub_creates == 0, "...without contacting Razorpay")
flag(True)
opts = options(buy_u)
check(opts["signals_lite"]["kind"] == "signals" and opts["growth"]["kind"] == "dashboard", "flag on: offered, with kind")
check(opts["signals_lite"]["signal_count"] == 9 and opts["signals_pro"]["signal_count"] == len(ALL), "signal counts")
check(not any(o["upgrade"] for o in opts.values()), "no live plan: nothing is an 'upgrade'")

# Buy Signals Lite and have it charged.
r = post(buy_u, "/subscriptions", {"tier": "signals_lite"})
check(r.status_code == 201 and r.get_json()["tier"] == "signals_lite", "Signals Lite checkout created")
sid_lite = r.get_json()["razorpay_subscription_id"]
check("replaces" not in FakeRP.last_sub_payload["notes"], "a first plan replaces nothing")
period_end = datetime.now(UTC) + timedelta(days=30)
res = S.apply_charge(sid_lite, "pay_lite_1", 20_000, current_start=datetime.now(UTC), current_end=period_end,
                     paid_count=1)
check(res["newly_paid"] and plan_of(buy_o) == LITE, "charged: the company is on Signals Lite")
check(set(me(buy_u)["user"]["signals"]["allowed"]) == LITE_SIGNALS, "and sees the Lite signals")
check(_trial.unavailable(buy_o, buy_u) == "trial_used", "a company that has paid (even Rs 200) gets no trial")

# What it may move to.
opts = options(buy_u)
check({t for t, o in opts.items() if o["upgrade"]} == {"signals_pro", "starter", "growth"},
      "with live Lite: Signals Pro and the self-serve dashboard plans are upgrades")
r = post(buy_u, "/subscriptions", {"tier": "signals_lite"})
check(r.status_code == 409 and r.get_json()["error"] == "already_subscribed", "same tier again: refused")

# A gateway failure while moving up changes nothing.
FakeRP.fail_cancel = True
r = post(buy_u, "/subscriptions", {"tier": "growth"})
check(r.status_code == 502, "cancel of the old plan failed -> 502")
check(S.get_by_razorpay_id(sid_lite)["status"] == "active", "...and the old plan is untouched locally")
FakeRP.fail_cancel = False
n_cancels = len(FakeRP.cancels)
FakeRP.fail_create = True
r = post(buy_u, "/subscriptions", {"tier": "growth"})
check(r.status_code == 502 and len(FakeRP.cancels) == n_cancels,
      "new subscription couldn't be created -> the old one is never cancelled")
check(S.get_by_razorpay_id(sid_lite)["status"] == "active" and plan_of(buy_o) == LITE, "...and stays live")
FakeRP.fail_create = False

# Move up to Growth.
before = len(FakeRP.cancels)
r = post(buy_u, "/subscriptions", {"tier": "growth"})
check(r.status_code == 201 and r.get_json()["tier"] == "growth", "moving up to Growth: checkout created")
sid_growth = r.get_json()["razorpay_subscription_id"]
check(FakeRP.last_sub_payload["notes"]["replaces"] == sid_lite, "Razorpay notes record what it replaces")
check(FakeRP.cancels[before:] == [(sid_lite, {"cancel_at_cycle_end": 0})],
      "the old plan is cancelled at Razorpay right away: it never charges again")
old = S.get_by_razorpay_id(sid_lite)
check(old["status"] == "cancelled", "old plan ended locally")
check(abs((S._aware(old["ends_at"]) - period_end).total_seconds()) < 2, "...but keeps access to the end of its paid month")
check(plan_of(buy_o) == LITE, "while the new checkout is open the company still has Signals Lite")
check(S.get_live(buy_o) is None, "no live subscription in between, so nothing can clash")
# Walking away from the checkout costs nothing already paid for.
S.sweep_ended()
check(plan_of(buy_o) == LITE, "sweep before the paid month ends: Signals Lite kept")
# The new one goes live — NOT a 'duplicate' (the bug this design avoids).
st = S.record_state(sid_growth, status="authenticated")
check(not st.get("duplicate") and S.get_by_razorpay_id(sid_growth)["status"] == "authenticated",
      "the upgrade's subscription goes live cleanly")
res = S.apply_charge(sid_growth, "pay_growth_1", 1_200_000, current_end=datetime.now(UTC) + timedelta(days=30),
                     paid_count=1)
check(res["newly_paid"] and plan_of(buy_o) == P.plan_id_v2("growth"), "first charge: the company is on Growth")
check(S.get_by_razorpay_id(sid_growth)["status"] == "active", "Growth subscription active")
# The old plan's month runs out: Growth must stay.
S.sweep_ended(now=period_end + timedelta(minutes=1))
check(plan_of(buy_o) == P.plan_id_v2("growth"), "old Lite month ending never takes Growth away")
# A stray late renewal of the old one can't pull the company back down.
S.apply_charge(sid_lite, "pay_lite_late", 20_000, paid_count=2)
check(plan_of(buy_o) == P.plan_id_v2("growth"), "a late charge on the replaced plan doesn't lower the plan")
# From a dashboard plan there is no second subscription.
opts = options(buy_u)
check(not any(o["upgrade"] for o in opts.values()), "live Growth: nothing offered as an upgrade")
r = post(buy_u, "/subscriptions", {"tier": "signals_pro"})
check(r.status_code == 409, "live Growth: buying a signal plan is refused")
# Starter -> Growth is a dashboard plan change, not this move-up path: still refused.
st_u, st_o = person("stella")
r = post(st_u, "/subscriptions", {"tier": "starter"})
S.apply_charge(r.get_json()["razorpay_subscription_id"], "pay_st_1", 500_000,
               current_end=datetime.now(UTC) + timedelta(days=30), paid_count=1)
n_cancels = len(FakeRP.cancels)
r = post(st_u, "/subscriptions", {"tier": "growth"})
check(r.status_code == 409 and r.get_json()["error"] == "already_subscribed", "live Starter -> Growth: refused")
check(len(FakeRP.cancels) == n_cancels and not options(st_u)["growth"]["upgrade"],
      "...nothing cancelled, not offered as an upgrade")

# Lite -> Signals Pro, and Signals Pro -> Lite (a downgrade) is not a move-up.
up_u, up_o = person("uma")
r = post(up_u, "/subscriptions", {"tier": "signals_pro"})
sid_sp = r.get_json()["razorpay_subscription_id"]
S.apply_charge(sid_sp, "pay_sp_1", 50_000, current_end=datetime.now(UTC) + timedelta(days=30), paid_count=1)
check(plan_of(up_o) == SPRO, "on Signals Pro")
r = post(up_u, "/subscriptions", {"tier": "signals_lite"})
check(r.status_code == 409 and r.get_json()["error"] == "already_subscribed", "Signals Pro -> Lite is not a move-up")
check({t for t, o in options(up_u).items() if o["upgrade"]} == {"starter", "growth"}, "Signals Pro: only dashboard upgrades")

# No moving up while a renewal is failing: fix the payment first.
S.record_state(sid_sp, status="halted", paid_count=1)
r = post(up_u, "/subscriptions", {"tier": "growth"})
check(r.status_code == 409 and r.get_json()["error"] == "already_subscribed", "past due: no move-up")
check(not any(o["upgrade"] for o in options(up_u).values()), "past due: nothing offered as an upgrade")

# An unpaid (authenticated, never charged) signal subscription can't be "upgraded" either.
un_u, un_o = person("una")
r = post(un_u, "/subscriptions", {"tier": "signals_lite"})
S.record_state(r.get_json()["razorpay_subscription_id"], status="authenticated")
check(post(un_u, "/subscriptions", {"tier": "growth"}).status_code == 409, "not yet paid: no move-up")

# Turning the flag off with a live signal plan: the plan stays, the offer goes.
flag(False)
check("signals_pro" not in options(up_u), "flag off again: no longer offered")
check(plan_of(up_o) == SPRO, "...but a company already on it keeps its plan")
flag(True)

print(f"OK — {passed} signal-tier checks passed")

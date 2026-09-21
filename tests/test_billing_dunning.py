"""
test_billing_dunning.py — billing-v2 P13: failed renewals, dunning and the soft-lock.

A renewal that fails puts the paying company PAST DUE; DUNNING_DAYS later, if no
charge has cleared it, the company is LOCKED. The lock is a real backend refusal,
not a blur: a locked company's plan is suspended for everyone it covers (Pro
columns, API keys), its credits can't be spent, its reports can't be downloaded,
and nobody can buy more credits while a payment is unpaid. Nothing is deleted, and
one real charge restores everything.

    DATABASE_URL="sqlite:////tmp/billing_dunning.sqlite" python3 tests/test_billing_dunning.py

Plain script, throwaway sqlite, no network. A failure is a real regression in a
money path.
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

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/billing_dunning_default.sqlite")
if "sqlite" not in os.environ["DATABASE_URL"]:
    raise SystemExit("Refusing to run: DATABASE_URL doesn't look like a throwaway sqlite file.")
if not os.environ.get("CUSTOMER_DATA_KEY"):
    from cryptography.fernet import Fernet
    os.environ["CUSTOMER_DATA_KEY"] = Fernet.generate_key().decode()
os.environ.setdefault("INVOICES_DIR", tempfile.mkdtemp(prefix="pm_invoices_"))
for k in ("BILLING_LIVE_PURCHASES",):
    os.environ.pop(k, None)
os.environ.update(BILLING_SCOPE="wallet", BILLING_SUBSCRIPTIONS="1", RAZORPAY_KEY_ID="rzp_test_dun",
                  RAZORPAY_KEY_SECRET="dun_secret", RAZORPAY_WEBHOOK_SECRET="whsec_dun")

import _db
_db.enabled = lambda: False
import _auth_db as A
import _pricing
import _subscriptions as S
import _email
import _api_keys
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
    u = A.upsert_user(f"dun-{tag}", f"{tag}@example.com", tag, None)
    org = A.create_default_organization_for_user(u["id"], f"{tag} Co")
    return u["id"], org["id"]


def plan_of(org_id):
    return sql("SELECT plan FROM organizations WHERE id=:i", i=org_id).scalar()


def sub_row(sid):
    return S.get_by_razorpay_id(sid)


def epoch(days=0):
    return int((datetime.now(UTC) + timedelta(days=days)).timestamp())


def eff(uid):
    return _pricing.compat_plan(A.get_effective_plan_for_user(uid))


_n = [0]


def paying_sub(org_id, user_id, tier="growth", pay="a"):
    """A subscription that has genuinely been paid once (so a plan is granted)."""
    _n[0] += 1
    sid = f"sub_D{_n[0]}"
    S.create_local(org_id, user_id, tier, "monthly", sid, f"plan_D{_n[0]}", _pricing.subscription_amount_paise(tier))
    S.apply_charge(sid, f"pay_{sid}_{pay}", _pricing.subscription_amount_paise(tier),
                   current_start=S.ts(epoch(-30)), current_end=S.ts(epoch(0)), paid_count=1, event_ts=S.ts(epoch(-30)))
    return sid


def backdate_past_due(sid, days):
    sql("UPDATE subscriptions SET past_due_since=:t WHERE razorpay_subscription_id=:s",
        t=datetime.now(UTC) - timedelta(days=days), s=sid)


# Capture emails instead of sending them.
sent = []
_email.send_dunning_notice = lambda to, org, kind, lock_at, url: sent.append((to, kind)) or True

# ═════════════════════════════════════════════════════════════════════════════
# 1. The clock: when does "past due" start, and what stops it
# ═════════════════════════════════════════════════════════════════════════════
owner, org = person("olivia")
member, _ = person("marc")
A.add_org_member(org, owner, "marc@example.com", "member")
admin, _ = person("asha")
A.add_org_member(org, owner, "asha@example.com", "admin")
stranger, s_org = person("sam")

# A FIRST charge that fails is not "past due": nothing was ever granted.
fu, fo = person("firsty")
_n[0] += 1
first = f"sub_D{_n[0]}"
S.create_local(fo, fu, "growth", "monthly", first, "plan_first", 1_200_000)
S.record_state(first, status="pending", event_ts=S.ts(epoch(-1)))
check(sub_row(first)["past_due_since"] is None, "a failed FIRST charge does not start dunning")
check(A.wallet_dunning(fo) is None and A.account_standing(fu) is None, "…and the account is in good standing")

# A healthy paid subscription: nothing to report.
sid = paying_sub(org, owner)
check(plan_of(org) == "v2_growth" and eff(owner) == "pro" and eff(member) == "pro", "paid Growth: everyone on the company is Pro")
check(A.wallet_dunning(org) is None and A.account_standing(owner) is None, "no dunning while renewals succeed")

# A RENEWAL fails: the clock starts, once, and access is unchanged during the grace.
S.record_state(sid, status="pending", event_ts=S.ts(epoch(-0.5)))
row = sub_row(sid)
check(row["past_due_since"] is not None, "a failed renewal starts the dunning clock")
t0 = row["past_due_since"]
d = A.wallet_dunning(org)
check(d is not None and d["locked"] is False, "past due but not yet locked")
check(d["lock_at"] - S._aware(t0) == timedelta(days=_pricing.DUNNING_DAYS), "lock time is DUNNING_DAYS after the failure")
check(eff(owner) == "pro" and eff(member) == "pro", "plan is unchanged during the grace period")
check(A.wallet_spend_block(owner, org, 10) is None, "spending still works during the grace period")
st = A.account_standing(owner)
check(st["past_due"] is True and st["locked"] is False and st["lock_at"], "account_standing reports past due, not locked")

# Repeated failure events don't restart or extend the clock.
S.record_state(sid, status="halted", event_ts=S.ts(epoch(-0.2)))
S.record_state(sid, status="pending", event_ts=S.ts(epoch(-0.1)))
check(S._aware(sub_row(sid)["past_due_since"]) == S._aware(t0), "later failure events don't move the clock")
check(A.wallet_dunning(org)["locked"] is False, "halted inside the grace period is still not locked")

# A bare 'active' status event is not a payment and doesn't clear the debt.
S.record_state(sid, status="active", event_ts=S.ts(epoch(-0.05)))
check(sub_row(sid)["past_due_since"] is not None, "a bare 'active' event doesn't clear dunning")
S.record_state(sid, status="halted", event_ts=S.ts(epoch(-0.04)))

# ═════════════════════════════════════════════════════════════════════════════
# 2. The lock: a real refusal, everywhere the plan or wallet is read
# ═════════════════════════════════════════════════════════════════════════════
backdate_past_due(sid, _pricing.DUNNING_DAYS - 1)
check(A.wallet_dunning(org)["locked"] is False, "day 2 of 3: not locked yet")
backdate_past_due(sid, _pricing.DUNNING_DAYS + 1)
check(A.wallet_dunning(org)["locked"] is True, "past DUNNING_DAYS: locked")
check(eff(owner) == "free" and eff(member) == "free" and eff(admin) == "free",
      "a locked company's plan is suspended for everyone it covers")
check(plan_of(org) == "v2_growth", "…but the plan is not removed, only suspended")
blk = A.wallet_spend_block(owner, org, 10)
check(blk is not None and blk["reason"] == "account_locked", "spending is refused with reason account_locked")
check(A.wallet_spend_block(member, org, 10)["reason"] == "account_locked", "…for a plain member too")
try:
    A.spend_credits(owner, 5, reason="forecast", org_id=org)
    refused = False
except A.SpendNotAllowedError as e:
    refused = e.reason == "account_locked"
check(refused, "the atomic charge-time check refuses too (not only the pre-check)")
st = A.account_standing(owner)
check(st["locked"] is True, "account_standing reports locked")
check(A.account_standing(stranger) is None and eff(stranger) == "free", "an unrelated user is untouched")
check(A.wallet_spend_block(stranger, s_org, 10) is None, "…and can still spend their own credits")

# A linked client company is covered by the payer's lock.
cl_user, cl_org = person("clara")
A.add_org_member(org, owner, "clara@example.com", "admin")
check(A.set_org_payer(cl_org, cl_user, org).get("status") == "ok", "client linked under the paying company")
check(A.wallet_spend_block(cl_user, cl_org, 10)["reason"] == "account_locked", "a linked client's spending is locked with its payer")
check(A.account_standing(cl_user)["locked"] is True, "…and its standing says locked")

# A user's OWN legacy plan is not suspended by someone else's lock.
lg_user, lg_org = person("legacy")
sql("UPDATE users SET plan='pro' WHERE id=:i", i=lg_user)
A.add_org_member(org, owner, "legacy@example.com", "member")
check(eff(lg_user) == "pro", "a member with their own paid plan keeps it while the company is locked")

# ═════════════════════════════════════════════════════════════════════════════
# 3. Clearing it: only a real charge
# ═════════════════════════════════════════════════════════════════════════════
r = S.apply_charge(sid, "pay_recover_1", 1_200_000, current_start=S.ts(epoch(0)), current_end=S.ts(epoch(30)),
                   paid_count=2, event_ts=S.ts(epoch(0)))
check(r["newly_paid"] is True, "the retry payment is recorded")
check(sub_row(sid)["past_due_since"] is None and sub_row(sid)["lock_notified_at"] is None, "a charge clears dunning")
check(A.wallet_dunning(org) is None and eff(owner) == "pro" and eff(member) == "pro", "access is back for everyone")
check(A.wallet_spend_block(owner, org, 10) is None and A.account_standing(owner) is None, "spending works again, standing clean")

# ═════════════════════════════════════════════════════════════════════════════
# 4. Only LIVE subscriptions can lock
# ═════════════════════════════════════════════════════════════════════════════
e_user, e_org = person("ended")
es = paying_sub(e_org, e_user)
S.record_state(es, status="pending", event_ts=S.ts(epoch(-1)))
backdate_past_due(es, 10)
check(A.wallet_dunning(e_org)["locked"] is True, "a past-due live subscription is locked")
S.record_state(es, status="cancelled", event_ts=S.ts(epoch(1)))
check(A.wallet_dunning(e_org) is None, "once cancelled there is no debt to lock over")

# ═════════════════════════════════════════════════════════════════════════════
# 5. Emails: once each, to the people who can fix it
# ═════════════════════════════════════════════════════════════════════════════
n_user, n_org = person("nora")
person("nick")
A.add_org_member(n_org, n_user, "nick@example.com", "admin")
person("pat")
A.add_org_member(n_org, n_user, "pat@example.com", "member")
ns = paying_sub(n_org, n_user)
sent.clear()
check(S.dunning_sweep() == [] and sent == [], "a healthy subscription sends nothing")
S.record_state(ns, status="pending", event_ts=S.ts(epoch(-1)))
notices = S.dunning_sweep()
check([n["kind"] for n in notices] == ["past_due"], "a failed renewal produces one past-due notice")
recips = {to for to, kind in sent if kind == "past_due"}
check(recips == {"nora@example.com", "nick@example.com"}, "sent to the paying company's owner and admin, not a plain member")
check(S.dunning_sweep() == [] and len(sent) == 2, "a second sweep doesn't send it again")
backdate_past_due(ns, _pricing.DUNNING_DAYS + 1)
notices = S.dunning_sweep()
check([n["kind"] for n in notices] == ["locked"], "once locked, one locked notice")
check({to for to, kind in sent if kind == "locked"} == recips, "the locked notice goes to the same people")
check(S.dunning_sweep() == [], "and only once")
check(len(sent) == 4, "four emails in total: 2 people x (past due + locked)")

# Claiming is atomic: a stamp already taken by another worker means no second send.
n2_user, n2_org = person("nadia")
n2 = paying_sub(n2_org, n2_user)
S.record_state(n2, status="pending", event_ts=S.ts(epoch(-1)))
sql("UPDATE subscriptions SET past_due_notified_at=:t WHERE razorpay_subscription_id=:s", t=datetime.now(UTC), s=n2)
sent.clear()
check(S.dunning_sweep() == [] and sent == [], "a notice another worker already claimed is not re-sent")

# Restored: only if we'd actually warned them.
rec = S.apply_charge(ns, "pay_n_rec", 1_200_000, current_end=S.ts(epoch(30)), paid_count=2, event_ts=S.ts(epoch(0)))
check(rec["recovered"] is True, "recovering after a notice reports recovered")
plain = S.apply_charge(n2, "pay_n2_ok", 1_200_000, current_end=S.ts(epoch(30)), paid_count=2, event_ts=S.ts(epoch(0)))
check(plain["recovered"] is True, "recovering after a claimed notice also reports recovered")
q_user, q_org = person("quiet")
qs = paying_sub(q_org, q_user)
quiet = S.apply_charge(qs, "pay_q_2", 1_200_000, current_end=S.ts(epoch(30)), paid_count=2, event_ts=S.ts(epoch(0)))
check(quiet["recovered"] is False, "a normal renewal is not a 'recovery'")
sent.clear()
S.notify_restored(n_org)
check({k for _, k in sent} == {"restored"} and {t for t, _ in sent} == recips, "the restored notice goes to the payers")

# ═════════════════════════════════════════════════════════════════════════════
# 6. Cancel-then-resubscribe must not lose the plan (round-1 bug found while designing this)
# ═════════════════════════════════════════════════════════════════════════════
r_user, r_org = person("rita")
old = paying_sub(r_org, r_user)
S.record_state(old, status="cancelled", event_ts=S.ts(epoch(1)), current_end=S.ts(epoch(10)))   # ends in 10 days
_n[0] += 1
new = f"sub_D{_n[0]}"
S.create_local(r_org, r_user, "growth", "monthly", new, "plan_new", 1_200_000)
S.apply_charge(new, "pay_new_1", 1_200_000, current_end=S.ts(epoch(40)), paid_count=1, event_ts=S.ts(epoch(2)))
S.sweep_ended(now=datetime.now(UTC) + timedelta(days=20))
check(plan_of(r_org) == "v2_growth", "the OLD subscription's end doesn't revoke what the NEW one granted")
# ...but with no replacement, the end still revokes.
S.record_state(new, status="cancelled", event_ts=S.ts(epoch(3)), current_end=S.ts(epoch(40)))
S.sweep_ended(now=datetime.now(UTC) + timedelta(days=60))
check(plan_of(r_org) == "free", "with no replacement, the end revokes as before")
# An unpaid replacement (never charged) doesn't protect the plan.
u_user, u_org = person("uma")
old2 = paying_sub(u_org, u_user)
S.record_state(old2, status="cancelled", event_ts=S.ts(epoch(1)), current_end=S.ts(epoch(5)))
_n[0] += 1
unpaid = f"sub_D{_n[0]}"
S.create_local(u_org, u_user, "growth", "monthly", unpaid, "plan_unpaid", 1_200_000)
S.record_state(unpaid, status="authenticated", event_ts=S.ts(epoch(2)))
S.sweep_ended(now=datetime.now(UTC) + timedelta(days=20))
check(plan_of(u_org) == "free", "an authenticated-but-never-paid replacement doesn't keep the plan alive")

# ═════════════════════════════════════════════════════════════════════════════
# 7. HTTP: purchases, data endpoints, /me, recover, webhook
# ═════════════════════════════════════════════════════════════════════════════
class _Orders:
    calls = 0

    def create(self, data):
        _Orders.calls += 1
        return {"id": f"order_dun_{_Orders.calls}"}


class _Plan:
    def create(self, data):
        FakeRP.plan_creates += 1
        return {"id": f"plan_dun_{FakeRP.plan_creates}"}


class _Subscription:
    def create(self, data):
        if FakeRP.fail_create:
            raise RuntimeError("razorpay down")
        FakeRP.sub_creates += 1
        FakeRP.log.append("create")
        return {"id": f"sub_dun_new_{FakeRP.sub_creates}"}

    def fetch(self, sid):
        return FakeRP.sub_entities[sid]

    def cancel(self, sid, data):
        FakeRP.log.append("cancel")
        FakeRP.cancels.append((sid, data))
        return {"id": sid}


class _Payment:
    def fetch(self, pid):
        return FakeRP.payments[pid]


class FakeRP:
    plan_creates = sub_creates = 0
    fail_create = False
    log = []
    cancels = []
    sub_entities = {}
    payments = {}
    order = _Orders()
    plan = _Plan()
    subscription = _Subscription()
    payment = _Payment()
    utility = razorpay.Client(auth=("rzp_test_dun", "dun_secret")).utility


B._client = lambda: FakeRP()


def client_as(uid):
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = uid
    return c


def post(uid, path, body=None):
    return client_as(uid).post(f"/api/billing{path}", json=body or {})


def webhook(payload):
    raw = json.dumps(payload)
    sig = hmac.new(b"whsec_dun", raw.encode(), hashlib.sha256).hexdigest()
    return app.test_client().post("/api/billing/webhook", data=raw,
                                  headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"})


def sub_event(event, sid, status, created_at=None, payment=None, **entity):
    ent = {"id": sid, "status": status, **entity}
    body = {"event": event, "created_at": created_at or epoch(0), "payload": {"subscription": {"entity": ent}}}
    if payment:
        body["payload"]["payment"] = {"entity": payment}
    return body


h_user, h_org = person("hana")
hm_user, _ = person("hugo")
A.add_org_member(h_org, h_user, "hugo@example.com", "member")
hs = paying_sub(h_org, h_user)
proj = A.create_project(h_user, "Hana project", org_id=h_org)
proj_id = proj["id"] if isinstance(proj, dict) else proj
rep = A.create_report(h_user, proj_id, "Hana report", status="ready", file_path="/nonexistent/x.pdf")
rep_id = rep["id"] if isinstance(rep, dict) else rep

# Healthy: purchases open, /me clean.
r = post(h_user, "/orders/credits", {"pack_id": next(iter(_pricing.CREDIT_PACKS))})
check(r.status_code == 201, "a healthy account can buy credits")
me = client_as(h_user).get("/api/auth/me").get_json()
check(me["user"]["account"] is None, "/api/auth/me shows no account issue when healthy")

# Past due (in grace): no NEW purchases, but the dashboard still works.
webhook(sub_event("subscription.pending", hs, "pending", created_at=epoch(-0.5)))
check(sub_row(hs)["past_due_since"] is not None, "the pending webhook starts dunning")
before = _Orders.calls
r = post(h_user, "/orders/credits", {"pack_id": next(iter(_pricing.CREDIT_PACKS))})
check(r.status_code == 402 and r.get_json()["error"] == "unpaid_balance" and _Orders.calls == before,
      "past due: buying credits is refused (402) and never reaches Razorpay")
r = post(h_user, "/orders/report", {"project_id": proj_id})
check(r.status_code == 402 and r.get_json()["error"] == "unpaid_balance", "past due: buying a report is refused too")
me = client_as(h_user).get("/api/auth/me").get_json()["user"]
check(me["account"]["past_due"] is True and me["account"]["locked"] is False, "/api/auth/me flags past due, not locked")
check(me["plan"] == "pro", "the plan is still Pro during the grace period")
check(client_as(h_user).get("/api/billing/invoices").status_code == 200, "invoices stay reachable")
check(post(stranger, "/orders/credits", {"pack_id": next(iter(_pricing.CREDIT_PACKS))}).status_code == 201,
      "another company's purchases are unaffected")

# Locked: the data endpoints refuse.
backdate_past_due(hs, _pricing.DUNNING_DAYS + 1)
me = client_as(h_user).get("/api/auth/me").get_json()["user"]
check(me["account"]["locked"] is True and me["plan"] == "free", "locked: /me says so and the plan reads free")
check(client_as(hm_user).get("/api/auth/me").get_json()["user"]["plan"] == "free", "…for the company's plain members too")
r = client_as(h_user).get(f"/api/forecast?project_id={proj_id}&budget=5000000")
check(r.status_code == 402 and r.get_json()["error"] == "account_locked", "locked: forecast is refused with 402 account_locked")
check(r.get_json()["lock_at"], "…naming when the lock began")
r = client_as(h_user).get(f"/api/reports/{rep_id}/download")
check(r.status_code == 402 and r.get_json()["error"] == "account_locked", "locked: a report download is refused")
r = post(h_user, "/orders/credits", {"pack_id": next(iter(_pricing.CREDIT_PACKS))})
check(r.status_code == 402, "locked: still can't buy credits")
check(client_as(h_user).get("/api/billing/invoices").status_code == 200, "locked: billing/invoices still reachable to fix it")
check(client_as(h_user).get(f"/api/billing/subscriptions?org_id={h_org}").get_json()["standing"]["locked"] is True,
      "locked: the billing page can read its own standing")
check(client_as(h_user).get("/api/reports").status_code == 200, "locked: the list of saved reports is still visible (nothing deleted)")
check(A.get_report(rep_id, h_user) is not None and A.get_project(proj_id, h_user) is not None, "locked: saved data is intact")
# API key: Pro elevation is suspended too.
raw_key = "pmk_dunning_test_key_0123456789"
kh = hashlib.sha256(raw_key.encode()).hexdigest()
A.create_api_key(h_user, kh, raw_key[:8], label="dun", org_id=h_org)
check(_pricing.compat_plan(A.get_api_key_by_hash(kh)["plan"]) == "free", "locked: an API key no longer elevates to Pro")
# Unlocked user, healthy company: forecast is NOT refused for lock reasons.
r = client_as(stranger).get(f"/api/forecast?project_id={proj_id}&budget=5000000")
check(r.status_code == 404, "someone else's project still 404s (isolation unchanged)")

# Recovery through the webhook: a real charge clears everything and says so.
captured = []
S._deliver = lambda notices, background: captured.extend(notices)
webhook(sub_event("subscription.charged", hs, "active", created_at=epoch(0),
                  payment={"id": "pay_h_rec", "status": "captured", "amount": 1_200_000, "order_id": "order_h_rec"},
                  current_start=epoch(0), current_end=epoch(30), paid_count=2))
check(sub_row(hs)["past_due_since"] is None and A.wallet_dunning(h_org) is None, "the charged webhook clears the lock")
check(client_as(h_user).get("/api/auth/me").get_json()["user"]["plan"] == "pro", "Pro is back")
check(client_as(h_user).get(f"/api/reports/{rep_id}/download").status_code != 402, "downloads are no longer refused for lock")
check(post(h_user, "/orders/credits", {"pack_id": next(iter(_pricing.CREDIT_PACKS))}).status_code == 201, "purchases reopen")
check(any(n["kind"] == "restored" for n in captured), "the restored notice was sent")
del S._deliver          # back to the real one for the rest

# ── Recover: replace a failing subscription with a new payment method ───────
rc_user, rc_org = person("rhea")
rcs = paying_sub(rc_org, rc_user)
check(post(rc_user, "/subscriptions/recover", {}).get_json()["error"] == "not_past_due", "recover on a healthy subscription -> 409")
check(post(rc_user, "/subscriptions/recover", {}).status_code == 409, "…with status 409")
S.record_state(rcs, status="halted", event_ts=S.ts(epoch(-0.5)))
check(post(hm_user, "/subscriptions/recover", {"org_id": rc_org}).status_code == 403, "a stranger can't recover someone else's subscription")
FakeRP.log.clear()
FakeRP.fail_create = True
r = post(rc_user, "/subscriptions/recover", {})
check(r.status_code == 502 and sub_row(rcs)["status"] == "halted" and FakeRP.log == [],
      "if Razorpay refuses the replacement the old subscription is untouched")
FakeRP.fail_create = False
r = post(rc_user, "/subscriptions/recover", {})
check(r.status_code == 201 and FakeRP.log == ["create", "cancel"], "recover starts the replacement BEFORE retiring the old one")
new_sid = r.get_json()["razorpay_subscription_id"]
check(FakeRP.cancels[-1] == (rcs, {"cancel_at_cycle_end": 0}), "the old subscription is cancelled at Razorpay now")
check(sub_row(rcs)["status"] == "cancelled" and sub_row(new_sid)["status"] == "created", "old cancelled, new awaiting payment")
lock_at = S._aware(sub_row(rcs)["past_due_since"]) + timedelta(days=_pricing.DUNNING_DAYS)
check(S._aware(sub_row(rcs)["ends_at"]) == lock_at, "the old one keeps access exactly until the original lock time")
check(plan_of(rc_org) == "v2_growth" and eff(rc_user) == "pro", "inside the grace period the plan is kept while the new one is paid")
S.sweep_ended(now=lock_at + timedelta(hours=1))
check(plan_of(rc_org) == "free", "if the new payment never comes, the plan ends at the original lock time")
# The new one paid: plan comes back and stays.
S.apply_charge(new_sid, "pay_rc_new", 1_200_000, current_end=S.ts(epoch(30)), paid_count=1, event_ts=S.ts(epoch(1)))
check(plan_of(rc_org) == "v2_growth" and S.get_live(rc_org)["razorpay_subscription_id"] == new_sid, "paying the replacement re-grants the plan")

# Recover when ALREADY locked: the old one ends now (no extra grace), plan revoked until paid.
lk_user, lk_org = person("liam")
lks = paying_sub(lk_org, lk_user)
S.record_state(lks, status="halted", event_ts=S.ts(epoch(-6)))
backdate_past_due(lks, _pricing.DUNNING_DAYS + 2)
check(A.wallet_dunning(lk_org)["locked"] is True, "liam is locked")
r = post(lk_user, "/subscriptions/recover", {})
check(r.status_code == 201, "a locked account can start recovery")
check(plan_of(lk_org) == "free" and A.wallet_dunning(lk_org) is None,
      "already-locked recovery ends the old subscription now, so there is no free grace")

# The flag still gates recover.
os.environ["BILLING_SUBSCRIPTIONS"] = "0"
check(post(rc_user, "/subscriptions/recover", {}).status_code == 403, "recover is dark when subscriptions are off")
os.environ["BILLING_SUBSCRIPTIONS"] = "1"

# Legacy scope: none of this exists — even for a company that IS locked under wallet scope.
ls_user, ls_org = person("lena")
lss = paying_sub(ls_org, ls_user)
S.record_state(lss, status="halted", event_ts=S.ts(epoch(-6)))
backdate_past_due(lss, _pricing.DUNNING_DAYS + 2)
check(A.account_standing(ls_user)["locked"] is True and A.wallet_spend_block(ls_user, ls_org, 10) is not None,
      "lena's company is locked under wallet scope")
os.environ.pop("BILLING_SCOPE")
check(A.account_standing(ls_user) is None, "outside wallet scope there is no account standing")
check(A.wallet_spend_block(ls_user, ls_org, 10) is None, "outside wallet scope spending is never blocked")
os.environ["BILLING_SCOPE"] = "wallet"

print(f"OK — {passed} checks passed")

"""
test_assisted_sale.py — billing-v2: sales-assisted plans (invoice/NEFT rail).

An operator records a sale paid outside Razorpay: the company's plan for N months, its
credits, and an audit row against the EXTERNAL invoice number. Checked here: every
refusal, that a dry run changes nothing, that one invoice can only ever be recorded
once, that repeat sales stack, that access ends on time without ever revoking a plan
from under something else that still entitles the company, revoke, the customer-facing
field, and the command-line tool end to end.

    DATABASE_URL="sqlite:////tmp/assisted_sale.sqlite" python3 tests/test_assisted_sale.py

Plain script, throwaway sqlite, no network. A failure is a real regression in a money path.
"""

import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "paisamap-etl", "etl"))

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/assisted_sale_default.sqlite")
if "sqlite" not in os.environ["DATABASE_URL"]:
    raise SystemExit("Refusing to run: DATABASE_URL doesn't look like a throwaway sqlite file.")
if not os.environ.get("CUSTOMER_DATA_KEY"):
    from cryptography.fernet import Fernet
    os.environ["CUSTOMER_DATA_KEY"] = Fernet.generate_key().decode()
os.environ.setdefault("INVOICES_DIR", tempfile.mkdtemp(prefix="pm_invoices_"))
os.environ.update(BILLING_SCOPE="wallet")
DB_URL = os.environ["DATABASE_URL"]

import _db
_db.enabled = lambda: False
import _auth_db as A
import _pricing
import _subscriptions as S
import _assisted_sale as AS
from sqlalchemy import text

A.init_schema()
A.migrate_schema()
engine = A._require_engine()

import server

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
    u = A.upsert_user(f"as-{tag}", f"{tag}@example.com", tag, None)
    org = A.create_default_organization_for_user(u["id"], f"{tag} Co")
    return u["id"], org["id"]


def plan_of(org_id):
    return sql("SELECT plan FROM organizations WHERE id=:i", i=org_id).scalar()


def eff(uid):
    return _pricing.compat_plan(A.get_effective_plan_for_user(uid))


def wallet(org_id):
    return A.get_wallet_credit_balance(org_id)


def n_sales(org_id=None):
    if org_id is None:
        return sql("SELECT COUNT(*) FROM assisted_sales").scalar()
    return sql("SELECT COUNT(*) FROM assisted_sales WHERE org_id=:o", o=org_id).scalar()


_inv = [0]


def inv():
    _inv[0] += 1
    return f"PM/TEST/{_inv[0]:04d}"


def sale(org_id, tier="growth", months=1, **kw):
    """Record a real (not dry-run) paid sale with sensible defaults."""
    args = dict(months=months, invoice_number=inv(), amount_paise=1_200_000, payment_method="neft",
                payment_ref="UTR000111", operator="tester", dry_run=False)
    args.update(kw)
    return AS.grant(org_id, tier, **args)


def refused(res, code):
    return res.get("error") == code


# ═════════════════════════════════════════════════════════════════════════════
# 0. Small helpers
# ═════════════════════════════════════════════════════════════════════════════
d = datetime(2026, 1, 31, tzinfo=UTC)
check(AS.add_months(d, 1) == datetime(2026, 2, 28, tzinfo=UTC), "31 Jan + 1 month clamps to 28 Feb")
check(AS.add_months(datetime(2028, 1, 31, tzinfo=UTC), 1) == datetime(2028, 2, 29, tzinfo=UTC), "…and 29 Feb in a leap year")
check(AS.add_months(d, 12) == datetime(2027, 1, 31, tzinfo=UTC), "12 months is the same day next year")
check(AS.add_months(datetime(2026, 11, 15, tzinfo=UTC), 3) == datetime(2027, 2, 15, tzinfo=UTC), "months roll over the year")
check(AS.rupees_to_paise("12000") == 1_200_000 and AS.rupees_to_paise("12,000.50") == 1_200_050, "rupees parse to paise")
check(AS.rupees_to_paise("0") == 0, "zero is a valid amount")
check(AS.rupees_to_paise("-5") is None and AS.rupees_to_paise("abc") is None and AS.rupees_to_paise("1.234") is None,
      "negative, junk and sub-paisa amounts are refused")

# ═════════════════════════════════════════════════════════════════════════════
# 1. Refusals
# ═════════════════════════════════════════════════════════════════════════════
owner, org = person("olivia")
member, _ = person("marc")
A.add_org_member(org, owner, "marc@example.com", "member")
before = n_sales()
base = dict(invoice_number="X-1", amount_paise=100, payment_method="neft", payment_ref="U1", operator="t", dry_run=False)
check(refused(AS.grant(org, "nope", **base), "invalid_tier"), "unknown tier refused")
check(refused(AS.grant(org, "trial", **base), "invalid_tier"), "the free trial isn't sellable")
check(refused(AS.grant(org, "growth", months=0, **base), "invalid_months"), "0 months refused")
check(refused(AS.grant(org, "growth", months=37, **base), "invalid_months"), "37 months refused")
check(refused(AS.grant(org, "growth", months=True, **base), "invalid_months"), "a boolean isn't a month count")
check(refused(AS.grant(org, "growth", credits=-1, **base), "invalid_credits"), "negative credits refused")
check(refused(AS.grant(org, "growth", credits=1_000_001, **base), "invalid_credits"), "absurd credits refused")
check(refused(AS.grant(org, "growth", **dict(base, invoice_number="  ")), "invalid_invoice"), "blank invoice number refused")
check(refused(AS.grant(org, "growth", **dict(base, invoice_number="x" * 65)), "invalid_invoice"), "over-long invoice number refused")
check(refused(AS.grant(org, "growth", **dict(base, amount_paise=-1)), "invalid_amount"), "negative amount refused")
check(refused(AS.grant(org, "growth", **dict(base, amount_paise=0)), "invalid_amount"), "a paid sale of 0 refused")
check(refused(AS.grant(org, "growth", **dict(base, payment_method="bitcoin")), "invalid_method"), "unknown payment method refused")
check(refused(AS.grant(org, "growth", **dict(base, payment_ref=None)), "payment_ref_required"), "a paid sale needs a payment reference")
check(refused(AS.grant(org, "growth", **dict(base, payment_ref="  ")), "payment_ref_required"), "…a blank one doesn't count")
check(refused(AS.grant(org, "growth", **dict(base, payment_method="complimentary", amount_paise=5, payment_ref=None, notes="x")),
              "invalid_amount"), "complimentary with money is refused")
check(refused(AS.grant(org, "growth", **dict(base, payment_method="complimentary", amount_paise=0, payment_ref=None)),
              "note_required"), "complimentary needs a reason")
check(refused(AS.grant(org, "growth", **dict(base, operator="")), "operator_required"), "an operator is required")
check(refused(AS.grant(99999, "growth", **base), "org_not_found"), "unknown company refused")
check(n_sales() == before and plan_of(org) == "free" and wallet(org) == 0, "none of those refusals changed anything")

# A linked client company can't be granted directly.
cl_user, cl_org = person("clara")
A.add_org_member(org, owner, "clara@example.com", "admin")
check(A.set_org_payer(cl_org, cl_user, org).get("status") == "ok", "client linked under the paying company")
r = AS.grant(cl_org, "growth", **base)
check(refused(r, "not_a_payer") and r["payer_org_id"] == org, "a linked client is refused, naming its payer")

# ═════════════════════════════════════════════════════════════════════════════
# 2. Dry run changes nothing
# ═════════════════════════════════════════════════════════════════════════════
r = AS.grant(org, "scale", months=12, invoice_number="DRY-1", amount_paise=30_000_000, payment_method="neft",
             payment_ref="UTRDRY", operator="tester")          # dry_run defaults to True
check(r["ok"] and r["dry_run"] is True, "dry run is the default")
s = r["summary"]
check(s["credits"] == 7000 * 12 and s["plan_after"] == "v2_scale" and s["plan_before"] == "free", "dry run reports the effect")
check(s["wallet_balance_before"] == 0 and s["wallet_balance_after"] == 84000, "…including the wallet before/after")
check(n_sales() == 0 and plan_of(org) == "free" and wallet(org) == 0, "a dry run changed nothing")
check(AS.grant(org, "scale", months=12, invoice_number="DRY-1", amount_paise=30_000_000, payment_method="neft",
               payment_ref="U", operator="t")["ok"], "the same invoice is still recordable after a dry run")

# ═════════════════════════════════════════════════════════════════════════════
# 3. A real grant
# ═════════════════════════════════════════════════════════════════════════════
t0 = datetime.now(UTC)
r = sale(org, "scale", months=12, invoice_number="PM/2026-27/001", amount_paise=30_000_000, payment_ref="UTR99")
check(r["ok"] and r["dry_run"] is False, "the grant is applied")
sl = r["sale"]
check(plan_of(org) == "v2_scale", "the company is on Scale")
check(eff(owner) == "team" and eff(member) == "team", "everyone in the company gets Scale's access")
check(wallet(org) == 84000, "the wallet got 7,000 credits x 12 months")
check(sl["status"] == "active" and sl["invoice_number"] == "PM/2026-27/001" and sl["payment_ref"] == "UTR99"
      and sl["granted_by"] == "tester" and sl["plan_before"] == "free" and sl["amount_paise"] == 30_000_000,
      "the audit row records invoice, payment, operator and the plan before")
check(S._aware(sl["period_end"]) - S._aware(sl["period_start"]) > timedelta(days=364), "the period is 12 months")
row = sql("SELECT delta, reason, ref_type, ref_id, org_id FROM credits_ledger WHERE reason='assisted_sale'").first()
check(row[0] == 84000 and row[2] == "assisted_sale" and row[3] == sl["id"] and row[4] == org,
      "the ledger row points back at the sale")

# Idempotent by invoice number.
before_credits = wallet(org)
r = sale(org, "scale", months=12, invoice_number="PM/2026-27/001", amount_paise=30_000_000)
check(refused(r, "invoice_already_recorded") and r["sale"]["id"] == sl["id"], "re-running the same invoice is refused")
check(wallet(org) == before_credits and n_sales(org) == 1, "…and grants nothing twice")
other_user, other_org = person("otto")
r = AS.grant(other_org, "starter", **dict(base, invoice_number="PM/2026-27/001"))
check(refused(r, "invoice_already_recorded") and plan_of(other_org) == "free" and wallet(other_org) == 0,
      "an invoice number is unique across all companies: it can't be reused for a different one")

# Credits land in the company that PAYS, even if its owner's primary company is another one.
mo_user, mo_primary = person("mona")
extra = A.create_organization(mo_user, "Mona Second Co")
extra_id = extra["id"] if isinstance(extra, dict) else extra
check(A.set_org_payer(extra_id, mo_user, None).get("status") == "ok", "mona's second company pays for itself")
sale(extra_id, "growth", months=2)
check(wallet(extra_id) == 6000 and wallet(mo_primary) == 0,
      "credits go to the company that was sold the plan, not to its owner's primary company")

# ═════════════════════════════════════════════════════════════════════════════
# 4. Refusals that depend on the company's state
# ═════════════════════════════════════════════════════════════════════════════
check(refused(sale(org, "growth"), "higher_plan_held"), "can't sell Growth to a company already on Scale")
check(refused(sale(org, "starter"), "higher_plan_held"), "…or Starter")
check(plan_of(org) == "v2_scale" and n_sales(org) == 1, "those refusals left the company alone")

sub_user, sub_org = person("sonia")
S.create_local(sub_org, sub_user, "growth", "monthly", "sub_AS1", "plan_AS1", 1_200_000)
S.apply_charge("sub_AS1", "pay_AS1", 1_200_000, current_end=S.ts(int((t0 + timedelta(days=30)).timestamp())), paid_count=1)
check(refused(sale(sub_org, "scale"), "has_subscription"), "a company with a live paid subscription is refused (no double billing)")
unpaid_user, unpaid_org = person("ursula")
S.create_local(unpaid_org, unpaid_user, "growth", "monthly", "sub_AS2", "plan_AS2", 1_200_000)
check(sale(unpaid_org, "scale")["ok"], "a subscription that never got paid doesn't block a sale")

hand_user, hand_org = person("hank")
sql("UPDATE organizations SET plan='v2_growth' WHERE id=:i", i=hand_org)
check(sale(hand_org, "growth")["ok"], "a company on the same tier by hand can be sold that tier (period starts now)")

# Complimentary pilot.
pil_user, pil_org = person("pilot")
r = AS.grant(pil_org, "growth", months=3, invoice_number="PILOT-001", amount_paise=0, payment_method="complimentary",
             notes="3-month design-partner pilot", operator="tester", dry_run=False)
check(r["ok"] and r["sale"]["amount_paise"] == 0 and r["sale"]["notes"].startswith("3-month"), "a complimentary pilot is recorded with its reason")
check(plan_of(pil_org) == "v2_growth" and wallet(pil_org) == 3000 * 3, "…and works like any other grant")

# ═════════════════════════════════════════════════════════════════════════════
# 5. Stacking, expiry, and never revoking from under something else
# ═════════════════════════════════════════════════════════════════════════════
st_user, st_org = person("stella")
a = sale(st_org, "growth", months=1)["sale"]
b = sale(st_org, "growth", months=1)
check(S._aware(b["sale"]["period_start"]) == S._aware(a["period_end"]), "a repeat sale of the same tier starts when the current one ends")
check(b["summary"]["stacked"] is True and wallet(st_org) == 6000, "…and its credits are added straight away")
b = b["sale"]
check(AS.active_for_org(st_org)["id"] == b["id"], "the company's current sale is the one that ends last")

S.sweep_ended(now=S._aware(a["period_end"]) - timedelta(hours=1))
check(plan_of(st_org) == "v2_growth" and AS.get(a["id"])["status"] == "active", "nothing expires before its period ends")
S.sweep_ended(now=S._aware(a["period_end"]) + timedelta(hours=1))
check(AS.get(a["id"])["status"] == "expired" and AS.get(a["id"])["expired_at"] is not None, "the first period expires")
check(plan_of(st_org) == "v2_growth", "…but the plan stays: the stacked sale still entitles the company")
check(AS.get(b["id"])["status"] == "active", "the stacked sale is still active")
S.sweep_ended(now=S._aware(b["period_end"]) + timedelta(hours=1))
check(plan_of(st_org) == "free" and AS.get(b["id"])["status"] == "expired", "when the last period ends the plan goes")
check(wallet(st_org) == 6000, "credits are kept when a period ends")
check(S.sweep_ended(now=S._aware(b["period_end"]) + timedelta(days=5)) == 0, "the sweep is idempotent")
check(AS.active_for_org(st_org) is None, "no active sale afterwards")

# Upgrade mid-period: when the higher sale ends the plan drops to the lower one still running.
up_user, up_org = person("uma")
low = sale(up_org, "growth", months=6)["sale"]
high = sale(up_org, "scale", months=1)["sale"]
check(plan_of(up_org) == "v2_scale", "an upgrade takes effect at once")
S.sweep_ended(now=S._aware(high["period_end"]) + timedelta(hours=1))
check(plan_of(up_org) == "v2_growth" and AS.get(high["id"])["status"] == "expired",
      "when the upgrade ends the plan falls back to the lower sale still running, not to free")
S.sweep_ended(now=S._aware(low["period_end"]) + timedelta(hours=1))
check(plan_of(up_org) == "free", "and to free when that ends too")

# A plan someone else assigned meanwhile is never touched.
ov_user, ov_org = person("oscar")
o = sale(ov_org, "growth", months=1)["sale"]
sql("UPDATE organizations SET plan='v2_pro' WHERE id=:i", i=ov_org)
S.sweep_ended(now=S._aware(o["period_end"]) + timedelta(hours=1))
check(AS.get(o["id"])["status"] == "expired" and plan_of(ov_org) == "v2_pro", "expiry never touches a plan that is no longer this sale's")

# ═════════════════════════════════════════════════════════════════════════════
# 6. Revoke
# ═════════════════════════════════════════════════════════════════════════════
rv_user, rv_org = person("rita")
x = sale(rv_org, "scale", months=6)["sale"]
credits_before = wallet(rv_org)
check(refused(AS.revoke(x["id"], reason="", operator="t", dry_run=False), "reason_required"), "revoke needs a reason")
check(refused(AS.revoke(x["id"], reason="r", operator="", dry_run=False), "operator_required"), "revoke needs an operator")
check(refused(AS.revoke(99999, reason="r", operator="t", dry_run=False), "not_found"), "revoking an unknown sale is refused")
r = AS.revoke(x["id"], reason="refunded", operator="t")
check(r["ok"] and r["dry_run"] is True and AS.get(x["id"])["status"] == "active" and plan_of(rv_org) == "v2_scale",
      "a revoke dry run changes nothing")
r = AS.revoke(x["id"], reason="refunded, invoice cancelled", operator="tester", dry_run=False)
check(r["ok"] and AS.get(x["id"])["status"] == "revoked", "revoke ends the sale")
rs = AS.get(x["id"])
check(rs["revoke_reason"] == "refunded, invoice cancelled" and rs["revoked_by"] == "tester" and rs["revoked_at"] is not None,
      "…recording who, when and why")
check(plan_of(rv_org) == "free" and eff(rv_user) == "free", "the plan ends now")
check(wallet(rv_org) == credits_before, "credits already granted are NOT taken back")
check(refused(AS.revoke(x["id"], reason="again", operator="t", dry_run=False), "not_active"), "a sale can't be revoked twice")
check(refused(AS.revoke(a["id"], reason="x", operator="t", dry_run=False), "not_active"), "an expired sale can't be revoked")
check(refused(AS.revoke(a["id"], reason="x", operator="t", dry_run=True), "not_active"),
      "…and a dry run says so too instead of promising a revoke that won't happen")
check(refused(AS.revoke(x["id"], reason="x", operator="t", dry_run=True), "not_active"), "…as does a dry run on a revoked one")

# Revoking one of two: the company keeps what the other still gives it.
rv2_user, rv2_org = person("raj")
keep = sale(rv2_org, "growth", months=6)["sale"]
drop = sale(rv2_org, "scale", months=1)["sale"]
AS.revoke(drop["id"], reason="wrong tier", operator="t", dry_run=False)
check(plan_of(rv2_org) == "v2_growth", "revoking the higher sale falls back to the lower one still running")

# ═════════════════════════════════════════════════════════════════════════════
# 7. Interplay with self-serve subscriptions
# ═════════════════════════════════════════════════════════════════════════════
ix_user, ix_org = person("ines")
low = sale(ix_org, "starter", months=6)["sale"]
S.create_local(ix_org, ix_user, "growth", "monthly", "sub_AS3", "plan_AS3", 1_200_000)
S.apply_charge("sub_AS3", "pay_AS3", 1_200_000, current_end=S.ts(int((t0 + timedelta(days=30)).timestamp())), paid_count=1)
check(plan_of(ix_org) == "v2_growth", "a paid subscription upgrades a company on an assisted Starter")
S.record_state("sub_AS3", status="cancelled", event_ts=S.ts(int((t0 + timedelta(days=1)).timestamp())),
               current_end=S.ts(int((t0 + timedelta(days=3)).timestamp())))
S.sweep_ended(now=t0 + timedelta(days=10))
check(plan_of(ix_org) == "v2_starter", "when the subscription ends the plan falls back to the assisted sale, not to free")
# An assisted sale ending while a paid subscription owns the plan leaves the plan alone.
iy_user, iy_org = person("ivo")
old = sale(iy_org, "starter", months=1)["sale"]
S.create_local(iy_org, iy_user, "growth", "monthly", "sub_AS4", "plan_AS4", 1_200_000)
S.apply_charge("sub_AS4", "pay_AS4", 1_200_000, current_end=S.ts(int((t0 + timedelta(days=90)).timestamp())), paid_count=1)
S.sweep_ended(now=S._aware(old["period_end"]) + timedelta(hours=1))
check(AS.get(old["id"])["status"] == "expired" and plan_of(iy_org) == "v2_growth",
      "an assisted sale ending doesn't touch a plan a paid subscription now owns")

# A subscription that was still unpaid when the sale was recorded, then gets paid (same tier):
# when the assisted period ends the paid subscription must keep the plan.
iz_user, iz_org = person("izzy")
S.create_local(iz_org, iz_user, "starter", "monthly", "sub_AS5", "plan_AS5", 500_000)
S.record_state("sub_AS5", status="authenticated", event_ts=S.ts(int(t0.timestamp())))
sold = sale(iz_org, "starter", months=1)["sale"]
S.apply_charge("sub_AS5", "pay_AS5", 500_000, current_end=S.ts(int((t0 + timedelta(days=90)).timestamp())), paid_count=1)
S.sweep_ended(now=S._aware(sold["period_end"]) + timedelta(hours=1))
check(AS.get(sold["id"])["status"] == "expired" and plan_of(iz_org) == "v2_starter",
      "an assisted sale ending doesn't revoke the SAME tier from a company whose subscription is now paid")

# ═════════════════════════════════════════════════════════════════════════════
# 8. What the customer sees, and scopes
# ═════════════════════════════════════════════════════════════════════════════
cu_user, cu_org = person("cyrus")
stranger, _ = person("sam")


def client_as(uid):
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = uid
    return c


j = client_as(cu_user).get(f"/api/billing/subscriptions?org_id={cu_org}").get_json()
check(j["assisted"] is None, "no assisted plan -> nothing shown")
sale(cu_org, "scale", months=12)
j = client_as(cu_user).get(f"/api/billing/subscriptions?org_id={cu_org}").get_json()
check(j["assisted"]["tier"] == "scale" and j["assisted"]["label"] == "Scale" and j["assisted"]["period_end"],
      "the owner sees 'managed by PaisaMap until <date>'")
check("invoice" not in str(j["assisted"]).lower() and "payment" not in str(j["assisted"]).lower(),
      "…without invoice or payment details")
check(client_as(stranger).get(f"/api/billing/subscriptions?org_id={cu_org}").status_code == 403, "a stranger can't read it")

# The plan-managed-directly refusal for self-serve now applies to an assisted company.
os.environ.update(BILLING_SUBSCRIPTIONS="1", RAZORPAY_KEY_ID="rzp_test_as", RAZORPAY_KEY_SECRET="s")
r = client_as(cu_user).post("/api/billing/subscriptions", json={"tier": "growth", "org_id": cu_org})
check(r.status_code == 409 and r.get_json()["error"] == "plan_managed_directly", "an assisted company can't also start a self-serve subscription")

# Legacy (user) scope: credits go to the owner's own balance.
os.environ.pop("BILLING_SCOPE")
lu, lo = person("lena")
b0 = A.get_credit_balance(lu)
sale(lo, "growth", months=1)
check(A.get_credit_balance(lu) == b0 + 3000, "in user scope the credits land on the owner's balance")
os.environ["BILLING_SCOPE"] = "wallet"

# Tables not applied yet.
_real = S.tables_ready
S.tables_ready = lambda: False
check(refused(AS.grant(org, "growth", **base), "tables_missing"), "granting is refused until the tables are applied")
S.tables_ready = _real

# ═════════════════════════════════════════════════════════════════════════════
# 9. The command-line tool, end to end
# ═════════════════════════════════════════════════════════════════════════════
cli_user, cli_org = person("carla")
ENV = dict(os.environ, DATABASE_URL=DB_URL, SUDO_USER="tester", PYTHONDONTWRITEBYTECODE="1")
CLI = [sys.executable, "-B", os.path.join(REPO, "paisamap-etl", "db", "assisted_sale.py")]


def cli(*args, env=None):
    p = subprocess.run(CLI + list(args), capture_output=True, text=True, env=env or ENV, cwd=REPO, timeout=120)
    return p.returncode, (p.stdout + p.stderr)


grant_args = ["grant", "--owner-email", "carla@example.com", "--tier", "scale", "--months", "12",
              "--invoice", "PM/CLI/001", "--amount", "300000", "--method", "neft", "--ref", "UTRCLI1"]
code, out = cli(*grant_args)
check(code == 0 and "DRY RUN" in out and "v2_scale" in out and "84000" in out, "the CLI defaults to a dry run and shows the effect")
check(plan_of(cli_org) == "free" and n_sales(cli_org) == 0, "…and changes nothing")
code, out = cli(*grant_args, "--yes")
check(code == 0 and "APPLIED" in out and "assisted sale #" in out and "tester" in out, "--yes applies it and names the operator")
check(plan_of(cli_org) == "v2_scale" and wallet(cli_org) == 84000, "the plan and credits are really there")
check("sweep" in out.lower(), "it reminds the operator that the sweep cron ends the plan")
code, out = cli(*grant_args, "--yes")
check(code == 1 and "invoice_already_recorded" in out and wallet(cli_org) == 84000, "re-running the same command is refused and grants nothing")
code, out = cli("list", "--org-id", str(cli_org))
check(code == 0 and "PM/CLI/001" in out and "active" in out, "list shows the sale")
code, out = cli("grant", "--owner-email", "nobody@example.com", "--tier", "scale", "--invoice", "I2", "--amount", "1",
                "--method", "neft", "--ref", "U")
check(code == 1 and "no user" in out, "an unknown owner email is refused")
code, out = cli("grant", "--org-id", str(cli_org), "--tier", "scale", "--invoice", "I3", "--amount", "12abc",
                "--method", "neft", "--ref", "U")
check(code == 2, "a junk amount is a usage error")
code, out = cli("grant", "--org-id", str(cli_org), "--tier", "scale", "--invoice", "I4", "--amount", "100", "--method", "neft")
check(code == 1 and "payment_ref_required" in out, "a paid sale without a payment reference is refused")
sid = AS.list_sales(cli_org)[0]["id"]
code, out = cli("revoke", "--sale-id", str(sid), "--reason", "test revoke")
check(code == 0 and "DRY RUN" in out and AS.get(sid)["status"] == "active", "revoke is a dry run by default")
code, out = cli("revoke", "--sale-id", str(sid), "--reason", "test revoke", "--yes")
check(code == 0 and "REVOKED" in out and "NOT taken back" in out, "revoke --yes applies and warns about credits")
check(AS.get(sid)["status"] == "revoked" and plan_of(cli_org) == "free", "the sale is revoked and the plan ends")
code, out = cli("list", "--status", "revoked")
check(code == 0 and "PM/CLI/001" in out, "list can filter by status")
empty_db = os.path.join(tempfile.mkdtemp(prefix="pm_empty_"), "empty.sqlite")
import sqlite3
sqlite3.connect(empty_db).close()
code, out = cli("list", env=dict(ENV, DATABASE_URL=f"sqlite:///{empty_db}"))
check(code == 1 and "tables aren't applied" in out and "Traceback" not in out,
      "on a database without the billing tables the CLI says so cleanly instead of a traceback")
env_no_db = {k: v for k, v in ENV.items() if k != "DATABASE_URL"}
code, out = cli("list", env=env_no_db)
check(code != 0 and "DATABASE_URL" in out, "no DATABASE_URL -> a clear message, and it never prints one")

print(f"OK — {passed} checks passed")

"""
test_billing_wallet_cutover.py — billing-v2 stage 2b: BILLING_SCOPE=wallet.

Under the default scope ("user") nothing changes — that is what the other
suites (and section 1 here) pin. Under "wallet":
  * a credit balance is the paying company's WALLET, shared by every member of
    every company drawing from it (a teammate no longer starts at 0);
  * spends are checked against the wallet and attributed to the company used;
  * a user's effective plan is the best of their own and their companies';
  * the ledger a user sees is limited to companies they belong to;
  * flipping back to "user" is lossless for single-user wallets.

    DATABASE_URL="sqlite:////tmp/billing_wallet_cutover.sqlite" \
      python3 tests/test_billing_wallet_cutover.py

Plain script, throwaway sqlite. A failure is a real regression in a money path.
NOT covered here: true concurrent spends on Postgres (SQLite can't model the
row lock) — the runbook has a step for that.
"""

import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "paisamap-etl", "etl"))

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/billing_wallet_cutover_default.sqlite")
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
import _pricing as P
from sqlalchemy import text

A.init_schema()
A.migrate_schema()
engine = A._require_engine()
passed = 0


def check(cond, msg):
    global passed
    assert cond, f"FAIL: {msg}"
    passed += 1


def scope(value):
    if value is None:
        os.environ.pop("BILLING_SCOPE", None)
    else:
        os.environ["BILLING_SCOPE"] = value


def person(tag, plan=None):
    u = A.upsert_user(f"sub-{tag}", f"{tag}@example.com", tag, None)
    org = A.create_default_organization_for_user(u["id"], f"{tag} Co")
    if plan:
        A.set_user_plan(u["id"], plan)
    return u["id"], org["id"]


def sql(stmt, **params):
    with engine.begin() as conn:
        return conn.execute(text(stmt), params)


# ═══ 1. the switch, and the default ══════════════════════════════════════════
scope(None)
check(A.billing_scope() == "user", "default scope is the legacy per-user behaviour")
for v, want in (("wallet", "wallet"), ("WALLET", "wallet"), (" wallet ", "wallet"),
                ("user", "user"), ("", "user"), ("walet", "user"), ("1", "user"), ("true", "user")):
    scope(v)
    check(A.billing_scope() == want, f"BILLING_SCOPE={v!r} -> {want}")
scope(None)

# ═══ 2. legacy data reads identically under both scopes ══════════════════════
carol, org_c = person("carol")
A.grant_credits(carol, 500, "bonus")
A.spend_credits(carol, 120, "forecast")
check(A.get_credit_balance(carol) == 380, "user scope: carol has 380")
scope("wallet")
check(A.get_credit_balance(carol) == 380, "wallet scope: a one-user company reads the same 380 (no visible change)")
check(A.get_credit_balance(carol, org_id=org_c) == 380, "...and with the company passed explicitly")
scope(None)

# ═══ 3. teammates pool one wallet ════════════════════════════════════════════
tina, org_t = person("tina")
uma, org_u = person("uma")
A.add_org_member(org_t, tina, "uma@example.com", "member")
A.grant_credits(tina, 500, "credit_purchase", org_id=org_t)          # the company buys 500
check(A.get_credit_balance(uma) == 0, "user scope: the invited teammate has 0 (the gap being fixed)")
scope("wallet")
check(A.get_credit_balance(uma, org_id=org_t) == 500, "wallet scope: the teammate sees the company's 500")
check(A.get_credit_balance(uma) == 0, "...but only in that company's context — her own company's wallet is separate")
new = A.spend_credits(uma, 30, "forecast", org_id=org_t)
check(new == 470, "spend returns the wallet balance after (470)")
check(A.get_credit_balance(tina) == 470 and A.get_credit_balance(uma, org_id=org_t) == 470,
      "owner and teammate both see 470 — one pool")
last = [r for r in A.list_credit_ledger(tina, org_id=org_t)][0]
check(last["user_id"] == uma and last["org_id"] == org_t and last["delta"] == -30, "the spend is logged to the teammate and the company")
try:
    A.spend_credits(uma, 1000, "forecast", org_id=org_t)
    check(False, "overspend must raise")
except A.InsufficientCreditsError as e:
    check(e.balance == 470 and e.required == 1000, "overspend is checked against the WALLET (470), not the person")
n_before = len(A.list_credit_ledger(tina, org_id=org_t))
check(A.get_credit_balance(tina) == 470 and n_before == 2, "a refused spend writes nothing")
# spending in her own (empty) company is refused even though the teammate's other company has credits
try:
    A.spend_credits(uma, 5, "forecast", org_id=org_u)
    check(False, "an empty wallet must refuse")
except A.InsufficientCreditsError as e:
    check(e.balance == 0, "an empty company wallet refuses; pooling does not leak across separate wallets")
scope(None)

# ═══ 4. the agency: one wallet, two clients ═════════════════════════════════
priya, org_ag = person("priya")
kushal, org_k = person("kushal")
acme = A.create_organization(priya, "Acme Retail")["id"]
zen = A.create_organization(priya, "Zen Cafes")["id"]
check(A.get_payer_org_id(acme) == org_ag and A.get_payer_org_id(zen) == org_ag,
      "companies an agency owner creates are linked under the agency wallet automatically")
loner = A.create_organization(priya, "Unlinked Co")["id"]
A.set_org_payer(loner, priya, None)          # this one should pay for itself
for org in (org_ag, acme):
    A.add_org_member(org, priya, "kushal@example.com", "member")     # Kushal: agency + Acme only, NOT Zen
A.set_org_payer(acme, priya, org_ag)
A.set_org_payer(zen, priya, org_ag)
A.grant_credits(priya, 1000, "credit_purchase", org_id=org_ag)
scope("wallet")
check(all(A.get_credit_balance(priya, org_id=o) == 1000 for o in (org_ag, acme, zen)),
      "agency, Acme and Zen all read the same 1000 wallet")
check(A.get_credit_balance(priya, org_id=loner) == 0, "an unlinked company has its own (empty) wallet")
A.spend_credits(priya, 40, "forecast", org_id=acme)
A.spend_credits(kushal, 25, "forecast", org_id=acme)
A.spend_credits(priya, 60, "report_generate", org_id=zen)
check(A.get_credit_balance(kushal, org_id=acme) == 875 == A.get_credit_balance(priya, org_id=zen), "1000 - 40 - 25 - 60 = 875 from any client's view")
check(A.usage_by_company(org_ag) == [{"org_id": acme, "name": "Acme Retail", "credits_used": 65},
                                     {"org_id": zen, "name": "Zen Cafes", "credits_used": 60}],
      "per-client usage: Acme 65, Zen 60, largest first")

# ═══ 5. what each person is allowed to SEE in the ledger ═════════════════════
full = A.list_credit_ledger(priya, org_id=org_ag)
check(len(full) == 4 and full[0]["balance_after"] == 875, "the agency owner sees all 4 rows, newest = current balance")
ks = A.list_credit_ledger(kushal, org_id=acme)
check({r["ref_id"] for r in ks} == {r["ref_id"] for r in full if r["org_id"] in (acme, org_ag) or r["user_id"] == kushal},
      "Kushal sees his own and his companies' rows...")
check(all(r["org_id"] != zen for r in ks), "...but never Zen's (a client he has no access to)")
check(len(ks) == 3, "3 visible rows (agency purchase + 2 Acme spends)")
bal_by_id = {r["id"]: r["balance_after"] for r in full}
check(all(bal_by_id[r["id"]] == r["balance_after"] for r in ks),
      "balance_after is the wallet's running balance for every viewer (the same number for the same row)")
check([r["balance_after"] for r in full] == [875, 935, 960, 1000], "running balance walks 1000 -> 960 -> 935 -> 875 in order")
scope(None)

# ═══ 6. rollback is lossless for single-user wallets ═════════════════════════
scope("wallet")
A.spend_credits(carol, 20, "forecast", org_id=org_c)
check(A.get_credit_balance(carol) == 360, "wallet scope: 380 - 20")
scope(None)
check(A.get_credit_balance(carol) == 360, "flipping back to user scope reads the same 360 — no drift")
check([r["balance_after"] for r in A.list_credit_ledger(carol)][:2] == [360, 380], "and the per-user chain is intact")
scope("wallet")
check(A.get_credit_balance(carol) == 360, "and forward again")
scope(None)

# ═══ 7. effective plan ═══════════════════════════════════════════════════════
owner, org_o = person("olga", plan="pro")          # buys pro -> mirrored onto her company
member, org_m = person("marek")                    # free, on his own
A.add_org_member(org_o, owner, "marek@example.com", "member")
client = A.create_organization(owner, "Olga Client")["id"]
A.set_org_payer(client, owner, org_o)
A.add_org_member(client, owner, "marek@example.com", "member")
check(A.get_effective_plan_for_user(member) == "free", "user scope: a free teammate stays free")
scope("wallet")
check(A.get_effective_plan_for_user(member) == "pro", "wallet scope: a seat in a paid company carries its plan")
check(A.get_effective_plan_for_user(owner) == "pro", "the owner is unchanged")
check(A.get_effective_plan_for_user(carol) == "free", "an unrelated user is unaffected")
lone_client_member, _ = person("lena")
A.add_org_member(client, owner, "lena@example.com", "member")     # only in the LINKED client company
check(A.get_effective_plan_for_user(lone_client_member) == "pro", "membership in a company linked under a paying account counts too")
# never lowers
big, _ = person("bigplan", plan="team")
A.add_org_member(org_o, owner, "bigplan@example.com", "member")
check(A.get_effective_plan_for_user(big) == "team", "a higher personal plan is never lowered by a lesser company")
# ...including a plan set by a manual database flip (no company mirror), like the owner's own account
flipped, _ = person("flipped")
sql("UPDATE users SET plan = 'team' WHERE id = :u", u=flipped)
A.add_org_member(org_o, owner, "flipped@example.com", "member")
check(A.get_effective_plan_for_user(flipped) == "team", "a manually flipped 'team' account is not lowered by being a member of a 'pro' company")
# a v2 tier on a company IS honoured now (the legacy->v2 mapping writes these); junk still fails closed
sql("UPDATE organizations SET plan = 'v2_scale' WHERE id = :o", o=org_o)
check(A.get_effective_plan_for_user(member) == "v2_scale", "a v2 company plan is carried by its members, as its own id")
check(A.get_effective_plan_for_user(flipped) == "v2_scale", "...and a v2 tier that gives more than a member's own legacy plan raises them (same entitlements, higher tier)")
sql("UPDATE organizations SET plan = 'v2_starter' WHERE id = :o", o=org_o)
check(A.get_effective_plan_for_user(flipped) == "team", "...but never lowers: a v2 tier that gives LESS than the member's own plan doesn't replace it")
sql("UPDATE organizations SET plan = 'gold' WHERE id = :o", o=org_o)
check(A.get_effective_plan_for_user(member) == "free", "junk plan ids fail closed")
sql("UPDATE organizations SET plan = 'pro' WHERE id = :o", o=org_o)
check(A.get_effective_plan_for_user(None) == "free", "no user -> free")
# the API-key path resolves through the same function
import hashlib
raw = "pmk_test_" + "x" * 30
A.create_api_key(member, hashlib.sha256(raw.encode()).hexdigest(), raw[:12], "t")
check(A.get_api_key_by_hash(hashlib.sha256(raw.encode()).hexdigest())["plan"] == "pro", "an API key inherits the effective plan")
scope(None)
check(A.get_api_key_by_hash(hashlib.sha256(raw.encode()).hexdigest())["plan"] == "free", "...and reverts under the legacy scope")

# ═══ 8. preflight — what must be true before flipping the switch ═════════════
pf = A.wallet_mode_preflight()
check(not any("differ from the per-user" in p for p in pf["problems"]),
      "an agency wallet shared by several users is NOT a problem — that is the point")
check(any("not linked under their owner" in p for p in pf["problems"]), "an unlinked extra company is flagged (it would start with an empty wallet)")
# a user whose credits are split across two wallets would see their balance change -> must block
split, org_split = person("split")
A.grant_credits(split, 100, "seed")
side = A.create_organization(split, "Split Side")["id"]
A.set_org_payer(side, split, None)
A.grant_credits(split, 50, "seed", org_id=side)
pf = A.wallet_mode_preflight()
wp = A.credit_wallet_parity()
check(any("differ from the per-user" in p for p in pf["problems"]) and set(wp["mismatches"]) == {org_split, side},
      "one user's credits split across two wallets is flagged, naming both wallets")
check(A.get_credit_balance(split) == 150, "(user scope still shows the combined 150 — this is exactly what would change)")
scope("wallet")
check(A.get_credit_balance(split, org_id=org_split) == 100 and A.get_credit_balance(split, org_id=side) == 50,
      "(wallet scope would show 100 and 50 — hence the preflight block)")
scope(None)
# the deliberate migration: link extra companies and merge their credits (same person, same pool as before)
res = A.link_extra_companies_to_primary()
check(res["linked"] >= 2 and res["ledger_rows_moved"] >= 1, "link_extra_companies_to_primary links unlinked extras and moves their rows")
check(A.get_payer_org_id(side) == org_split and A.get_payer_org_id(loner) == org_ag, "each extra company now resolves to its owner's wallet")
scope("wallet")
check(A.get_credit_balance(split, org_id=side) == 150 == A.get_credit_balance(split, org_id=org_split),
      "after the merge the user sees the same combined 150 from either company — no visible change")
scope(None)
check(A.link_extra_companies_to_primary() == {"linked": 0, "ledger_rows_moved": 0, "skipped_pays_for_others": 0}, "the migration is idempotent")
wp = A.credit_wallet_parity()
check(wp["mismatches"] == [], "and no wallet mismatches remain")
# a clean world: fresh DB state via separate accounts only
sql("UPDATE credits_ledger SET org_id = NULL, billing_org_id = NULL WHERE user_id = :u", u=carol)
scope("wallet")
check(A.get_credit_balance(carol) == 360, "wallet scope: rows with no company stamp still count toward their own user (safety net)")
scope(None)
sql("UPDATE credits_ledger SET org_id = :o WHERE user_id = :u", o=org_c, u=carol)      # company but no wallet yet
scope("wallet")
check(A.get_credit_balance(carol) == 360, "wallet scope: a row with a company but no wallet stamp resolves through its company")
scope(None)
sql("UPDATE credits_ledger SET org_id = NULL WHERE user_id = :u", u=carol)
pf = A.wallet_mode_preflight()
check(any("no company" in p for p in pf["problems"]), "unstamped ledger rows are reported")
A.backfill_organizations()
pf = A.wallet_mode_preflight()
check(not any("no company" in p or "no wallet" in p for p in pf["problems"]), "backfill clears the unstamped-row problems")
sql("UPDATE users SET plan = 'team' WHERE id = :u", u=carol)          # a manual flip, like the owner's real account
check({"user_id": carol, "user_plan": "team", "org_id": org_c, "org_plan": "free"} in A.wallet_mode_preflight()["info"]["plan_mismatches"],
      "a manually flipped plan shows up as an informational mismatch (never blocking)")
sql("UPDATE users SET plan = 'free' WHERE id = :u", u=carol)
# a fully clean world passes
clean = A.wallet_mode_preflight()
check(clean["ok"] and clean["problems"] == [], f"a clean world passes preflight, got {clean['problems']}")
# users with no company block the switch
ghost = A.upsert_user("sub-ghost", "ghost@example.com", "Ghost", None)["id"]
check(any("no primary company" in p for p in A.wallet_mode_preflight()["problems"]), "a user with no company blocks the switch")
A.backfill_organizations()

# the row lock is emitted on Postgres
from sqlalchemy.dialects import postgresql
orgs = A._get_tables()["organizations"]
from sqlalchemy import select
check("FOR UPDATE" in str(select(orgs.c.id).where(orgs.c.id == 1).with_for_update().compile(dialect=postgresql.dialect())),
      "the wallet-serialising lock compiles to FOR UPDATE on Postgres")

# ═══ 9. over HTTP: what a real user sees ═════════════════════════════════════
import server
app = server.app
app.testing = True


def call(uid, method, url, **kw):
    c = app.test_client()
    if uid:
        with c.session_transaction() as sess:
            sess["user_id"] = uid
    return c.open(url, method=method, **kw)


pro_project = A.create_project(tina, "Tina P", org_id=org_t)
empty_project = A.create_project(uma, "Uma P", org_id=org_u)

# user scope: the teammate is blocked (the bug); wallet scope: pooled credits let her through
with engine.connect() as _c:
    check(A._user_chain_balance(_c, uma) == -30, "(rollback caveat) a teammate who spent from a shared wallet has a negative per-user chain...")
check(A.get_credit_balance(uma) == 0, "...which the user scope displays clamped at 0, never negative")
r = call(uma, "GET", f"/api/forecast?project_id={pro_project['id']}&budget=30000")
check(r.status_code == 402 and r.get_json()["balance"] == 0,
      f"user scope: teammate is refused with balance 0, got {r.status_code} {r.get_data(as_text=True)[:200]}")
scope("wallet")
r = call(uma, "GET", f"/api/forecast?project_id={pro_project['id']}&budget=30000")
check(r.status_code != 402, f"wallet scope: the same teammate passes the credit check, got {r.status_code}")
r = call(uma, "GET", f"/api/forecast?project_id={empty_project['id']}&budget=30000")
check(r.status_code == 402 and r.get_json()["balance"] == 0, "...but an empty company's wallet still refuses")
r = call(uma, "GET", f"/api/expansion/recommend?project_id={empty_project['id']}&budget=100000")
check(r.status_code == 402, "expansion pre-check uses the wallet too")

r = call(uma, "GET", f"/api/credits?org_id={org_t}").get_json()
check(r["balance"] == A.get_credit_balance(tina) and isinstance(r["ledger"], list) and r["ledger"], "GET /api/credits?org_id= shows the pooled wallet + its ledger")
check(call(uma, "GET", f"/api/credits?org_id={acme}").status_code == 404, "...and 404s for a company she is not in")
check(call(uma, "GET", "/api/credits?org_id=999999").status_code == 404, "...same 404 for one that doesn't exist")
me = call(member, "GET", "/api/auth/me").get_json()["user"]
check(me["plan"] == "pro", "/api/auth/me reports the effective plan the server enforces")
scope(None)
me = call(member, "GET", "/api/auth/me").get_json()["user"]
check(me["plan"] == "free", "...and the legacy plan under the default scope")
check(call(uma, "GET", f"/api/credits?org_id={org_t}").get_json()["balance"] == 0, "under user scope ?org_id= changes nothing (still her own 0)")

# ═══ 10. privacy: a client's own staff never see the paying company's wallet ═══
# Azhar owns his OWN company and is a member of Acme (an agency-paid client company),
# but he is NOT a member of the agency that pays for Acme.
azhar, org_az = person("azhar")
A.add_org_member(acme, priya, "azhar@example.com", "member")
scope(None)
v = A.get_credit_view(azhar, acme)
check(v["balance"] == A.get_credit_balance(azhar) and v["paid_by"] is None,
      "legacy scope: the view is just the user's own balance (nothing hidden, nothing new)")
scope("wallet")
v = A.get_credit_view(azhar, acme)
check(v["balance"] is None, "wallet scope: a client's own staff get NO balance for the agency's wallet")
check(v["paid_by"] == {"org_id": org_ag, "name": "priya Co"}, "...but they are told who pays")
check(A.get_credit_view(kushal, acme)["balance"] == A.get_credit_balance(kushal, org_id=acme) is not None,
      "agency staff (members of the paying company) still see the number")
check(A.get_credit_view(priya, acme)["balance"] == A.get_credit_balance(priya, org_id=acme),
      "the paying company's owner sees it, working in a client company")
check(A.get_credit_view(azhar)["balance"] == A.get_credit_balance(azhar) and A.get_credit_view(azhar)["paid_by"] is None,
      "in his OWN company he sees his own wallet and no 'paid by'")
# the ledger: same rows he's entitled to, but never the payer's running total
lg = A.list_credit_ledger(azhar, org_id=acme)
check(len(lg) >= 2 and all(r["org_id"] == acme for r in lg), "he sees only Acme's rows (no purchase rows, no other clients)")
check(all(r["balance_after"] is None for r in lg), "...and none of them carry the payer's running wallet balance")
check(all(r["balance_after"] is not None for r in A.list_credit_ledger(kushal, org_id=acme)),
      "agency staff still get the running balance")
check(all(r["org_id"] != zen for r in lg), "and never a different client's rows")

# over HTTP: credits page, /me, and the 402 body
r = call(azhar, "GET", f"/api/credits?org_id={acme}")
b = r.get_json()
check(r.status_code == 200 and b["balance"] is None and b["paid_by"]["name"] == "priya Co"
      and all(x["balance_after"] is None for x in b["ledger"]), "/api/credits: no balance, names the payer, ledger stripped")
me = call(azhar, "GET", f"/api/auth/me?org_id={acme}").get_json()["user"]
check(me["credits"] is None and me["credits_paid_by"]["org_id"] == org_ag, "/api/auth/me?org_id= hides it too")
me = call(azhar, "GET", f"/api/auth/me?org_id={org_ag}").get_json()["user"]
check(me["credits"] == A.get_credit_balance(azhar) and me["credits_paid_by"] is None,
      "asking for a company he isn't in is ignored — no error, no leak, his own view")
me = call(priya, "GET", f"/api/auth/me?org_id={zen}").get_json()["user"]
check(me["credits"] == A.get_credit_balance(priya, org_id=zen), "the header follows the selected company (agency staff, in a client company)")
# a 402 for a client's staff must not reveal the payer's balance, and tells them who pays
starved = A.create_organization(priya, "Starved Client")["id"]          # linked under the agency...
A.add_org_member(starved, priya, "azhar@example.com", "member")
big_cost_project = A.create_project(azhar, "Az P", org_id=starved)
with engine.begin() as _c:                                              # drain the wallet to 0 to force a 402
    _c.execute(text("INSERT INTO credits_ledger (user_id, org_id, billing_org_id, delta, reason, balance_after, created_at) "
                    "VALUES (:u, :o, :o, :d, 'test_drain', 0, CURRENT_TIMESTAMP)"),
               {"u": priya, "o": org_ag, "d": -A.get_credit_balance(priya, org_id=org_ag)})
r = call(azhar, "GET", f"/api/forecast?project_id={big_cost_project['id']}&budget=30000")
body = r.get_json()
check(r.status_code == 403 and body["error"] == "budget_required" and body["paid_by"]["name"] == "priya Co" and "balance" not in body,
      f"a client's staff on an agency-paid company are refused BEFORE any balance talk: 403, names the payer, no balance — got {r.status_code} {body}")
check("priya Co" in body["detail"] and "budget" in body["detail"], "the refusal tells them what to do (ask the payer to set a budget)")
r2 = call(priya, "GET", f"/api/forecast?project_id={A.create_project(priya, 'Pr P', org_id=starved)['id']}&budget=30000")
check(r2.status_code == 402 and r2.get_json()["balance"] == 0, "the same 402 for the paying company's own staff still shows the (zero) balance")

# ═══ 11. the interim blocker: outside spenders can't draw an agency's wallet ═══
scope("wallet")
check(A.wallet_spend_allowed(azhar, acme) == (False, "budget_required"), "a client's own staff: not allowed to spend the agency's wallet")
check(A.wallet_spend_allowed(kushal, acme) == (True, None), "agency staff working in a client company: allowed")
check(A.wallet_spend_allowed(priya, acme) == (True, None), "the paying company's owner: allowed")
check(A.wallet_spend_allowed(azhar, org_az) == (True, None), "the same person in their OWN company: allowed")
check(A.wallet_spend_allowed(azhar) == (True, None), "with no company given -> their own primary company: allowed")
before = A.get_wallet_credit_balance(org_ag); n_rows = len(A.list_credit_ledger(priya, org_id=org_ag, limit=500))
try:
    A.spend_credits(azhar, 1, "forecast", org_id=acme)
    check(False, "the database layer must refuse too, not just the routes")
except A.SpendNotAllowedError as e:
    check(e.reason == "budget_required", "spend_credits itself refuses an outside spender (defence in depth)")
check(A.get_wallet_credit_balance(org_ag) == before and len(A.list_credit_ledger(priya, org_id=org_ag, limit=500)) == n_rows,
      "a refused spend writes nothing and moves nothing")
# funded wallet: still refused — it's about WHO, not whether there's money
acme_project = A.create_project(priya, "Acme P", org_id=acme)
A.grant_credits(priya, 50, "credit_purchase", org_id=org_ag)
for url in (f"/api/forecast?project_id={acme_project['id']}&budget=30000",
            f"/api/expansion/recommend?project_id={acme_project['id']}&budget=100000"):
    r = call(azhar, "GET", url)
    check(r.status_code == 403 and r.get_json()["error"] == "budget_required", f"{url.split('?')[0]}: refused with 403 even though the wallet is funded")
    r = call(priya, "GET", url)
    check(r.status_code != 403, f"{url.split('?')[0]}: the paying company's owner is not blocked")
    r = call(kushal, "GET", url)
    check(r.status_code != 403, f"{url.split('?')[0]}: agency staff are not blocked")
# legacy scope is untouched
scope(None)
check(A.wallet_spend_allowed(azhar, acme) == (True, None), "legacy scope: no gate at all")
r = call(azhar, "GET", f"/api/forecast?project_id={acme_project['id']}&budget=30000")
check(r.status_code == 402 and r.get_json()["balance"] == 0, "legacy scope: the old behaviour exactly (own balance, plain 402)")

scope(None)

print(f"OK — {passed} wallet-cutover checks passed")

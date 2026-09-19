"""
test_plan_mapping.py — billing-v2: map legacy accounts onto v2 tiers.

The guarantees: it sets only organizations.plan; it is inert until wallet mode;
in wallet mode it can only RAISE an account (never lower it); the default tier
is the lowest that keeps everything the account can do today; a stale run can't
overwrite a plan bought a moment ago; and it can be undone.

    DATABASE_URL="sqlite:////tmp/plan_mapping.sqlite" python3 tests/test_plan_mapping.py

Plain script, throwaway sqlite.
"""

import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "paisamap-etl", "etl"))

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/plan_mapping_default.sqlite")
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
import _plan_mapping as M
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


def sql(stmt, **params):
    with engine.begin() as conn:
        return conn.execute(text(stmt), params)


def scope(v):
    if v is None:
        os.environ.pop("BILLING_SCOPE", None)
    else:
        os.environ["BILLING_SCOPE"] = v


def person(tag, plan=None):
    u = A.upsert_user(f"sub-{tag}", f"{tag}@example.com", tag, None)
    org = A.create_default_organization_for_user(u["id"], f"{tag} Co")
    if plan:
        sql("UPDATE users SET plan = :p WHERE id = :u", p=plan, u=u["id"])
    return u["id"], org["id"]


def org_plan(o):
    return sql("SELECT plan FROM organizations WHERE id = :o", o=o).scalar()


def user_plan(u):
    return sql("SELECT plan FROM users WHERE id = :u", u=u).scalar()


# ═══ 1. the tier rule ═══════════════════════════════════════════════════════
check(M.propose_tier("pro") == "growth" and M.propose_tier("team") == "growth", "legacy pro and team -> Growth: the lowest tier that keeps the raised API limit AND the Pro columns")
check(M.propose_tier("free") is None and M.propose_tier("bogus") is None and M.propose_tier(None) is None, "free/unknown have nothing to keep -> no tier")
for legacy in ("pro", "team"):
    t = M.propose_tier(legacy)
    check(P.plan_strength(P.plan_id_v2(t)) >= P.plan_strength(legacy), f"the tier chosen for {legacy} never gives less")
    lower = M.MAPPABLE_TIERS[M.MAPPABLE_TIERS.index(t) - 1]
    check(P.plan_strength(P.plan_id_v2(lower)) < P.plan_strength(legacy), f"...and the tier below ({lower}) WOULD give less, so it is the minimum")
check([P.compat_plan(x) for x in ("free", "pro", "team", "bogus", "v2_trial", "v2_starter", "v2_growth", "v2_scale", "v2_pro", "v2_enterprise")]
      == ["free", "pro", "team", "free", "pro", "pro", "pro", "team", "team", "team"], "compat_plan: what the map and workspace see for every tier (Scale and above read as 'team')")
check("trial" not in M.MAPPABLE_TIERS and "free" not in M.MAPPABLE_TIERS and M.MAPPABLE_TIERS[0] == "starter", "trial and free can't be mapped to")

# ═══ 2. a realistic mix of accounts ═════════════════════════════════════════
owner, org_owner = person("owner", "pro")                # the founder's own account: hand-set legacy pro
mate = A.upsert_user("sub-mate", "mate@example.com", "mate", None)["id"]        # a free teammate of the owner
A.add_org_member(org_owner, owner, "mate@example.com", "member")
big, org_big = person("big", "team")                     # a legacy team account
orgonly, org_orgonly = person("orgonly")                 # legacy plan on the COMPANY only (users.plan free)
sql("UPDATE organizations SET plan = 'pro' WHERE id = :o", o=org_orgonly)
free1, org_free = person("free1")                        # nothing to map
member_pro = A.upsert_user("sub-mp", "mp@example.com", "mp", None)["id"]         # a member with a personal legacy plan, primary company not owned
sql("UPDATE users SET plan = 'pro' WHERE id = :u", u=member_pro)
already, org_already = person("already", "pro")
sql("UPDATE organizations SET plan = 'v2_scale' WHERE id = :o", o=org_already)
extra = A.create_organization(owner, "Owner Extra")["id"]                        # the owner's second company
sql("UPDATE organizations SET plan = 'pro' WHERE id = :o", o=extra)               # ...that somehow holds a legacy plan of its own
client, org_client = person("client", "pro")
sql("UPDATE organizations SET billing_org_id = :p WHERE id = :o", p=org_big, o=org_client)   # paid for by big

def rows_by_org():
    return {r["org_id"]: r for r in M.build_report(A)["rows"]}

rep = rows_by_org()
check(rep[org_owner]["action"] == "map" and rep[org_owner]["proposed_plan"] == "v2_growth" and rep[org_owner]["legacy_plan"] == "pro", "the owner's primary company maps to Growth by default")
check(rep[org_big]["action"] == "map" and rep[org_big]["legacy_plan"] == "team" and rep[org_big]["proposed_plan"] == "v2_growth", "a legacy team account too")
check(rep[org_orgonly]["action"] == "map", "a company whose own plan is legacy (owner's personal plan free) is mapped")
check(org_free not in rep, "a free account is not in the report at all")
check(rep[org_already]["action"] == "already_v2" and rep[org_already]["proposed_plan"] == "v2_scale", "a company already on v2 is left exactly as it is")
check(rep[org_client]["action"] == "skip" and "pays for this one" in rep[org_client]["reason"], "a company someone else pays for is skipped (its own plan isn't used)")
check(extra in rep and rep[extra]["action"] == "skip" and "primary" in rep[extra]["reason"], "the owner's extra company is reported and SKIPPED (it inherits through its payer), even with a legacy plan of its own")
check(rep[org_owner]["members"] == 2, "the report says how many people inherit")
check(not any(r["owner_user_id"] == member_pro for r in rep.values()), "a member's personal legacy plan alone maps nothing (it's a floor, and users.plan can't hold a v2 id)")

# ═══ 3. preview changes nothing; apply is exact and logged ══════════════════
before = sql("SELECT id, plan FROM organizations ORDER BY id").all()
users_before = sql("SELECT id, plan FROM users ORDER BY id").all()
res = M.apply(A, dry_run=True)
check(sql("SELECT id, plan FROM organizations ORDER BY id").all() == before and len(res["mapped"]) == 3, "a dry run reports 3 companies and writes nothing")
res = M.apply(A, dry_run=False)
check(sorted(r["org_id"] for r in res["mapped"]) == sorted([org_owner, org_big, org_orgonly]), "apply maps exactly those three")
check(org_plan(org_owner) == "v2_growth" and org_plan(org_big) == "v2_growth" and org_plan(org_orgonly) == "v2_growth", "their company plans are now v2_growth")
check(org_plan(org_already) == "v2_scale" and org_plan(org_free) == "free" and org_plan(org_client) == "free" and org_plan(extra) == "pro", "nothing else changed")
check(sql("SELECT id, plan FROM users ORDER BY id").all() == users_before, "users.plan is NEVER touched")
logged = [a for a in A.list_activity(owner) if a["action"] == "plan_mapped"]
check(len(logged) == 1 and logged[0]["metadata"]["from"] == "free" and logged[0]["metadata"]["to"] == "v2_growth" and logged[0]["metadata"]["price_book"] == P.PRICE_BOOK_VERSION,
      "each change is logged with from/to and the price-book version")
res2 = M.apply(A, dry_run=False)
check(res2["mapped"] == [] and len(res2["unchanged"]) >= 3, "running it again changes nothing (idempotent)")

# ═══ 4. nobody is lowered, in either scope ═════════════════════════════════
def snapshot(scope_value):
    scope(scope_value)
    try:
        return {u: P.entitlements(A.get_effective_plan_for_user(u)) for u in (owner, mate, big, orgonly, free1, member_pro, already, client)}
    finally:
        scope(None)

after_user, after_wallet = snapshot("user"), snapshot("wallet")
# 'before' is reconstructed by rolling back to a state with the legacy plans, so run the same snapshot then
M.rollback(A, dry_run=False)
before_user, before_wallet = snapshot("user"), snapshot("wallet")
M.apply(A, dry_run=False)
check(after_user == before_user, "user scope: not one account's entitlements differ before vs after the mapping (it's inert)")
for u, was in before_wallet.items():
    for k, had in was.items():
        check(after_wallet[u][k] or not had, f"wallet scope: user {u} keeps {k} ({had} -> {after_wallet[u][k]})")
check(after_wallet[mate]["pro_columns"] and after_wallet[mate]["api_elevated"] and not before_wallet[mate]["pro_columns"],
      "wallet scope: the owner's free teammate now inherits the company's tier (that's the point)")
check(not after_wallet[free1]["pro_columns"], "an unrelated free user is unaffected")
scope("wallet")
check(A.get_effective_plan_for_user(owner) == "v2_growth" and A.get_effective_plan_for_user(mate) == "v2_growth", "owner and teammate resolve to the v2 tier in wallet scope")
check(A.get_effective_plan_for_user(member_pro) == "pro", "a member with only a personal legacy plan keeps it")
scope(None)
check(A.get_effective_plan_for_user(owner) == "pro", "user scope still reads the personal plan")

# ═══ 5. overrides ═══════════════════════════════════════════════════════════
M.rollback(A, dry_run=False)
rep = M.build_report(A, {"owner@example.com": "pro", "ghost@example.com": "scale", "big@example.com": "enterprise-plus", "free1@example.com": "scale"})
rows = {r["org_id"]: r for r in rep["rows"]}
check(rows[org_owner]["proposed_plan"] == "v2_pro" and rows[org_owner]["override"] and rows[org_owner]["action"] == "map", "an override sets the owner's own account to the tier asked for")
check(any("ghost@example.com" in e for e in rep["errors"]), "an override for an unknown email is reported, not silently dropped")
check(any("free1@example.com" in e for e in rep["errors"]), "an override can't hand a paid tier to an account that isn't on a legacy paid plan (no accidental comps)")
check(any("enterprise-plus" in e for e in rep["errors"]) and rows[org_big]["action"] == "skip", "an override naming a tier that doesn't exist is an error, and that account is not mapped")
low = M.build_report(A, {"owner@example.com": "starter"})
lrow = {r["org_id"]: r for r in low["rows"]}[org_owner]
check(lrow["action"] == "blocked" and "api_elevated" in lrow["reason"], "an override that would take the raised API limit away is BLOCKED by default")
res = M.apply(A, {"owner@example.com": "starter"}, dry_run=False)
check(org_plan(org_owner) == "free" and [r["org_id"] for r in res["blocked"]] == [org_owner], "...and nothing is written for it")
res = M.apply(A, {"owner@example.com": "starter"}, allow_downgrade=True, dry_run=False)
check(org_plan(org_owner) == "v2_starter", "--allow-downgrade is the only way through")
M.rollback(A, dry_run=False)
check(org_plan(org_owner) == "free", "and it rolls back")

# ═══ 6. stale runs, purchases, rollback ═════════════════════════════════════
report = M.build_report(A)
sql("UPDATE organizations SET plan = 'team' WHERE id = :o", o=org_big)            # someone changes it between report and apply
real = M.build_report
M.build_report = lambda *a, **k: report           # a stale report, as if the plan changed after it ran
try:
    res = M.apply(A, dry_run=False)
finally:
    M.build_report = real
check(org_big in [r["org_id"] for r in res["changed_underneath"]] and org_plan(org_big) == "team", "a company whose plan changed after the report is left alone, not overwritten")
sql("UPDATE organizations SET plan = 'free' WHERE id = :o", o=org_big)
M.apply(A, dry_run=False)
check(org_plan(org_big) == "v2_growth", "(re-run maps it)")
# a legacy purchase after mapping must not clobber the v2 tier
A.set_user_plan(big, "team")
check(org_plan(org_big) == "v2_growth" and user_plan(big) == "team", "a later legacy purchase updates the personal plan but never overwrites the company's v2 tier")
scope("wallet")
check(A.get_effective_plan_for_user(big) == "v2_growth", "...and the person keeps the better of the two")
scope(None)
A.set_user_plan(free1, "pro")
check(org_plan(org_free) == "pro", "an unmapped company still mirrors a legacy purchase, as before")

# rollback semantics
res = M.rollback(A, dry_run=True)
check(len(res["restored"]) >= 3 and org_plan(org_owner) == "v2_growth", "a rollback preview writes nothing")
sql("UPDATE organizations SET plan = 'v2_pro' WHERE id = :o", o=org_orgonly)     # moved by hand since
res = M.rollback(A, dry_run=False)
check(org_orgonly in [r["org_id"] for r in res["left_alone"]] and org_plan(org_orgonly) == "v2_pro", "a company whose plan changed since the mapping is left alone")
check(org_plan(org_owner) == "free" and org_plan(org_big) == "free", "the rest go back to exactly what they were")
check(M.rollback(A, dry_run=False)["restored"] == [], "a second rollback does nothing")
check(any(a["action"] == "plan_mapping_rolled_back" for a in A.list_activity(owner)), "the rollback is logged too (the trail stays whole)")

# ═══ 7. the wallet-mode preflight no longer counts mapped accounts as mismatches ═
M.apply(A, dry_run=False)
mism = {m["org_id"] for m in A.wallet_mode_preflight()["info"]["plan_mismatches"]}
check(org_owner not in mism and org_big not in mism, "a mapped company (v2 tier giving at least what the owner's plan does) is not a plan mismatch")
sql("UPDATE organizations SET plan = 'v2_starter' WHERE id = :o", o=org_owner)
mism = {m["org_id"] for m in A.wallet_mode_preflight()["info"]["plan_mismatches"]}
check(org_owner in mism, "...but a v2 tier that gives LESS than the owner's own plan still is")

# ═══ 8. the command line ════════════════════════════════════════════════════
sql("UPDATE organizations SET plan = 'free' WHERE id = :o", o=org_owner)
env = dict(os.environ, DATABASE_URL=os.environ["DATABASE_URL"])
cli = [sys.executable, os.path.join(REPO, "paisamap-etl", "db", "plan_mapping.py")]
out = subprocess.run(cli + ["apply", "--override", "owner@example.com=pro"], env=env, capture_output=True, text=True)
check(out.returncode == 0 and "PREVIEW" in out.stdout and org_plan(org_owner) == "free", "apply without --yes is only a preview")
check("@" not in out.stdout, "the output never prints an email address")
out = subprocess.run(cli + ["apply", "--yes", "--override", "owner@example.com=starter"], env=env, capture_output=True, text=True)
check(out.returncode == 1 and "BLOCKED" in out.stdout, "a blocked mapping exits non-zero")
check(org_plan(org_owner) == "free", "(and it wrote nothing)")
out = subprocess.run(cli + ["apply", "--yes", "--override", "owner@example.com=pro"], env=env, capture_output=True, text=True)
check(out.returncode == 0 and "APPLIED" in out.stdout and org_plan(org_owner) == "v2_pro", "apply --yes writes")
out = subprocess.run(cli + ["rollback", "--yes"], env=env, capture_output=True, text=True)
check(out.returncode == 0 and "ROLLED BACK" in out.stdout, "rollback --yes works from the command line")
out = subprocess.run(cli + ["apply", "--override", "nonsense"], env=env, capture_output=True, text=True)
check(out.returncode != 0, "a malformed --override is refused")

print(f"OK — {passed} checks passed")

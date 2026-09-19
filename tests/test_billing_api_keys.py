"""
test_billing_api_keys.py — billing-v2: API keys belong to a COMPANY.

A key is attributed to the company it was made in. That company's owners and
admins (and those of the company that pays for it) can see every key with who
made it and how much it was used, and can revoke any of them; capacity is
counted per company, not per key; and a key dies the moment its owner leaves the
company (and stays dead if they are re-added).

    DATABASE_URL="sqlite:////tmp/billing_api_keys.sqlite" python3 tests/test_billing_api_keys.py

Plain script, throwaway sqlite. Run it under BILLING_SCOPE=wallet as well.
"""

import hashlib
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "paisamap-etl", "etl"))

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/billing_api_keys_default.sqlite")
if "sqlite" not in os.environ["DATABASE_URL"]:
    raise SystemExit("Refusing to run: DATABASE_URL doesn't look like a throwaway sqlite file.")
if not os.environ.get("CUSTOMER_DATA_KEY"):
    from cryptography.fernet import Fernet
    os.environ["CUSTOMER_DATA_KEY"] = Fernet.generate_key().decode()
os.environ.setdefault("INVOICES_DIR", tempfile.mkdtemp(prefix="pm_invoices_"))
os.environ["RATELIMIT_ENABLED"] = "1"

import _db
_db.enabled = lambda: False
import _auth_db as A
import _api_keys
import _ops
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


def person(tag):
    u = A.upsert_user(f"sub-{tag}", f"{tag}@example.com", tag, None)
    org = A.create_default_organization_for_user(u["id"], f"{tag} Co")
    return u["id"], org["id"]


def make_key(user_id, org_id=None, label=None):
    org, err = A.resolve_api_key_company(user_id, org_id)
    assert err is None, err
    raw, h, prefix = _api_keys.generate_key()
    rec = A.create_api_key(user_id, h, prefix, label, org_id=org)
    return rec, raw, h


def resolves(h):
    return A.get_api_key_by_hash(h)


# ═══ cast ═══════════════════════════════════════════════════════════════════
priya, org_ag = person("priya")                      # agency owner
adira, _ = person("adira")                           # agency admin
kushal, _ = person("kushal")                         # agency plain member
A.add_org_member(org_ag, priya, "adira@example.com", "admin")
A.add_org_member(org_ag, priya, "kushal@example.com", "member")
cy, org_cy = person("cy")                            # a client's owner
cyst, _ = person("cyst")                             # the client's staffer
A.add_org_member(org_cy, cy, "cyst@example.com", "member")
zed, org_zed = person("zed")                         # a stranger

# ═══ 1. a key is stamped with a company ═════════════════════════════════════
rec_p, _, h_p = make_key(priya, label="priya default")
check(rec_p["org_id"] == org_ag and rec_p["org_name"] == "priya Co", "a key made with no company chosen belongs to the maker's primary company")
check(A.revoke_api_key(rec_p["id"], priya), "(own-key revoke still works)")
rec, raw, h = make_key(kushal, org_ag, label="kushal's key")
check("key_hash" not in rec and "key" not in rec, "the record never carries the hash or the key")
check(resolves(h)["org_id"] == org_ag and resolves(h)["user_id"] == kushal, "resolving a key reports its company")

check(A.resolve_api_key_company(kushal, org_zed) == (None, {"error": "not_found"}), "a company you're not in is not_found (no hint it exists)")
for bad in ("5", True, 1.5, [1]):
    check(A.resolve_api_key_company(kushal, bad) == (None, {"error": "invalid_company"}), f"company {bad!r} is invalid")
own_two = A.create_organization(cy, "Cy Cafes")["id"]
rec2, _, _ = make_key(cy, own_two, "for cafes")
check(rec2["org_id"] == own_two, "a member of several companies picks which one the key belongs to")

# ═══ 2. who can see and revoke ══════════════════════════════════════════════
rec_a, raw_a, h_a = make_key(adira, org_ag, label="adira's key")
rec_c, raw_c, h_c = make_key(cyst, org_cy, label="client staff key")
view = A.list_company_api_keys(priya, org_ag)
check({k["label"] for k in view["keys"]} >= {"kushal's key", "adira's key"}, "an owner sees every key of the company")
check(all("key_hash" not in k for k in view["keys"]) and all("key_prefix" in k for k in view["keys"]), "the admin view shows prefixes, never a hash or key")
check({k["owner_name"] for k in view["keys"]} == {"kushal", "adira", "priya"} and all(k["owner_is_member"] for k in view["keys"]), "each key shows who made it")
check(A.list_company_api_keys(adira, org_ag)["keys"] and "error" not in A.list_company_api_keys(adira, org_ag), "an admin sees them too")
check(A.list_company_api_keys(kushal, org_ag) == {"error": "forbidden"}, "a plain member cannot list the company's keys")
check(A.list_company_api_keys(zed, org_ag) == {"error": "not_found"}, "a stranger gets not_found")
check(A.list_company_api_keys(priya, org_cy) == {"error": "not_found"}, "a payer's owner can't see a company it doesn't pay for")
check(A.list_company_api_keys(priya, org_zed) == {"error": "not_found"}, "...or one it has no link to")
check(A.revoke_company_api_key(kushal, org_ag, rec["id"]) == {"error": "forbidden"}, "a plain member cannot revoke a colleague's key")
check(resolves(h) is not None, "and it still works")

# the agency starts paying for the client: its admins can now see + revoke the client's keys
sql("UPDATE organizations SET billing_org_id = :p WHERE id = :o", p=org_ag, o=org_cy)   # (the approval flow is covered in test_billing_links)
check([k["label"] for k in A.list_company_api_keys(priya, org_cy)["keys"]] == ["client staff key"], "the company that pays for a company can see its keys")
check(A.list_company_api_keys(adira, org_cy).get("keys") is not None, "...its admins too")
check(A.list_company_api_keys(kushal, org_cy) == {"error": "forbidden"}, "...but not its plain members (they're inside the paying company, just not admins)")
check(A.list_company_api_keys(cyst, org_cy) == {"error": "forbidden"}, "the client's own staffer can't list the company's keys")
check(A.list_company_api_keys(cy, org_cy).get("keys") is not None, "the client's own owner can")
check(A.revoke_company_api_key(priya, org_cy, rec_c["id"]) == {"status": "ok"}, "the payer's owner can revoke a client's key")
check(resolves(h_c) is None, "a revoked key no longer resolves")
check(A.revoke_company_api_key(priya, org_cy, rec_c["id"]) == {"error": "not_found"}, "revoking twice is not_found")
check(A.revoke_company_api_key(priya, org_ag, rec_c["id"]) == {"error": "not_found"}, "revoking twice through the wrong company is not_found too")
rec_live, _, h_live = make_key(cyst, org_cy, label="live client key")
check(A.revoke_company_api_key(priya, org_ag, rec_live["id"]) == {"error": "not_found"} and resolves(h_live) is not None,
      "a LIVE key can't be revoked by naming a different company (an admin of A can't reach B's keys by guessing an id)")
check(A.revoke_company_api_key(priya, org_cy, rec_live["id"]) == {"status": "ok"}, "(through its own company it can)")
check(A.revoke_company_api_key(priya, org_ag, rec["id"]) == {"status": "ok"} and resolves(h) is None, "an owner revokes a member's key")
sql("UPDATE organizations SET billing_org_id = NULL WHERE id = :o", o=org_cy)
check(A.list_company_api_keys(priya, org_cy) == {"error": "not_found"}, "after detaching, the former payer sees nothing")

# ═══ 3. a key follows its owner's membership ════════════════════════════════
rec_k, raw_k, h_k = make_key(kushal, org_ag, label="second key")
_, _, h_own_co = make_key(kushal, label="kushal's own-company key")          # his OWN company (primary), not the agency
check(resolves(h_k) is not None, "a fresh key works")
check(A.remove_org_member(org_ag, priya, kushal) == {"status": "ok"}, "kushal leaves the agency")
check(resolves(h_k) is None, "their key stops working the moment they leave the company")
check(resolves(h_own_co) is not None, "...but leaving one company doesn't touch the same person's key in ANOTHER company")
A.add_org_member(org_ag, priya, "kushal@example.com", "member")
check(resolves(h_k) is None, "...and re-adding them does NOT bring it back to life (it was revoked, not just paused)")
rows = A.list_company_api_keys(priya, org_ag)["keys"]
check(next(k for k in rows if k["label"] == "second key")["revoked_at"] is not None, "the admin view shows it as revoked")
# a key made in company A doesn't survive leaving A even if they still belong to B
kk, _ = person("kk")
A.add_org_member(org_ag, priya, "kk@example.com", "member")
rec_kk, _, h_kk = make_key(kk, org_ag)
sql("DELETE FROM org_members WHERE org_id = :o AND user_id = :u", o=org_ag, u=kk)     # membership vanishes without going through remove_org_member
check(resolves(h_kk) is None, "even a membership removed behind our back kills the key (checked on every request)")

# ═══ 4. deleting a company revokes its keys ════════════════════════════════
tmp = A.create_organization(zed, "Zed Temp")["id"]
_, _, h_tmp = make_key(zed, tmp)
check(resolves(h_tmp) is not None, "a key in a second company works")
sql("UPDATE organizations SET billing_org_id = NULL WHERE id = :o", o=tmp)
check(A.delete_organization(tmp, zed), "the company is deleted")
check(resolves(h_tmp) is None, "its keys die with it")

# ═══ 5. legacy keys (no company) keep working ══════════════════════════════
raw_old, h_old, pre_old = _api_keys.generate_key()
sql("INSERT INTO api_keys (user_id, key_hash, key_prefix, created_at, usage_count_today) VALUES (:u, :h, :p, CURRENT_TIMESTAMP, 0)",
    u=zed, h=pre_old, p="pmk_old")
got = A.get_api_key_by_hash(pre_old)
check(got is not None and got["org_id"] is None and got["user_id"] == zed, "a key made before keys had a company still works, with no company")
# the deploy-before-the-column case: the lookup must degrade, not 500
sql("ALTER TABLE api_keys RENAME COLUMN org_id TO org_id_hidden")
try:
    got = A.get_api_key_by_hash(pre_old)
    check(got is not None and got["user_id"] == zed and got["org_id"] is None, "if api_keys.org_id doesn't exist yet, every existing key keeps working")
finally:
    sql("ALTER TABLE api_keys RENAME COLUMN org_id_hidden TO org_id")

# ═══ 6. usage: per key and per company ═════════════════════════════════════
_, _, h_u1 = make_key(adira, org_ag, label="u1")
_, _, h_u2 = make_key(priya, label="u2")
k1, k2 = resolves(h_u1), resolves(h_u2)
for _ in range(3):
    A.touch_api_key_usage(k1["key_id"])
for _ in range(2):
    A.touch_api_key_usage(k2["key_id"])
v = A.list_company_api_keys(priya, org_ag)
by = {k["label"]: k["usage_today"] for k in v["keys"]}
check(by["u1"] == 3 and by["u2"] == 2, "each key shows its own usage today")
check(v["usage_today"] == 5, "the company total is the sum of its live keys' usage (the number capacity is counted on)")
# a REVOKED key's traffic today, and one whose owner has left, are not the company's live capacity
_, _, h_dead = make_key(adira, org_ag, label="soon revoked")
kd = resolves(h_dead)
for _ in range(7):
    A.touch_api_key_usage(kd["key_id"])
check(A.list_company_api_keys(priya, org_ag)["usage_today"] == 12, "a live key's usage counts toward the total")
A.revoke_api_key(kd["key_id"], adira)
v2 = A.list_company_api_keys(priya, org_ag)
check(v2["usage_today"] == 5 and {k["label"]: k["usage_today"] for k in v2["keys"]}["soon revoked"] == 7,
      "a revoked key's usage stays visible on its own row but no longer counts toward the company total")
sql("UPDATE api_keys SET usage_reset_at = date(usage_reset_at, '-1 day') WHERE id = :i", i=k1["key_id"])
check({k["label"]: k["usage_today"] for k in A.list_company_api_keys(priya, org_ag)["keys"]}["u1"] == 0, "a counter left over from yesterday isn't today's usage")

# ═══ 7. capacity is per company, not per key ═══════════════════════════════
_ops._buckets.clear()
base_cap = _ops._LIMITS["data"][0]
free_key_a = {"key_id": 901, "org_id": 77, "plan": "free"}
free_key_b = {"key_id": 902, "org_id": 77, "plan": "free"}
allowed = 0
for i in range(base_cap * 3):
    ok, _ = _ops.check("1.2.3.4", "/api/export", api_key=free_key_a if i % 2 == 0 else free_key_b)
    allowed += ok
check(allowed <= base_cap + 2, f"two keys of one company share ONE bucket (allowed {allowed}, cap {base_cap}) — more keys buy no throughput")
_ops._buckets.clear()
other = {"key_id": 903, "org_id": 78, "plan": "free"}
for _ in range(base_cap):
    _ops.check("1.2.3.4", "/api/export", api_key=free_key_a)
check(_ops.check("1.2.3.4", "/api/export", api_key=other)[0], "another company's bucket is untouched")
_ops._buckets.clear()
lone = {"key_id": 904, "org_id": None, "plan": "free"}
lone2 = {"key_id": 905, "org_id": None, "plan": "free"}
for _ in range(base_cap):
    _ops.check("1.2.3.4", "/api/export", api_key=lone)
check(_ops.check("1.2.3.4", "/api/export", api_key=lone2)[0], "keys with no company keep a bucket each, as before")
_ops._buckets.clear()
n_free = sum(_ops.check("1.2.3.4", "/api/export", api_key={"key_id": 1, "org_id": 5, "plan": "free"})[0] for _ in range(base_cap * 8))
_ops._buckets.clear()
n_pro = sum(_ops.check("1.2.3.4", "/api/export", api_key={"key_id": 2, "org_id": 6, "plan": "pro"})[0] for _ in range(base_cap * 8))
check(n_pro > n_free * 3, f"a paid plan's company still gets the raised limit (free {n_free}, pro {n_pro})")
_ops._buckets.clear()
n_st = sum(_ops.check("1.2.3.4", "/api/export", api_key={"key_id": 3, "org_id": 7, "plan": "v2_starter"})[0] for _ in range(base_cap * 8))
n_gr = sum(_ops.check("1.2.3.4", "/api/export", api_key={"key_id": 4, "org_id": 8, "plan": "v2_growth"})[0] for _ in range(base_cap * 8))
check(n_st <= base_cap + 2 and n_gr > n_st * 3, f"a v2 tier gets the raised limit only where its price-book row includes the export API (starter {n_st}, growth {n_gr})")

print(f"OK — {passed} checks passed")

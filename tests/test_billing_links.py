"""
test_billing_links.py — billing-v2: who pays for a company.

A paying company ASKS (by email) to pay for another company; the person asked
picks which of their companies to link and approves; either side can detach at
any time; and the payer gets a usage statement (per company, per person, per
kind of action — never per project).

    DATABASE_URL="sqlite:////tmp/billing_links.sqlite" python3 tests/test_billing_links.py

Plain script, throwaway sqlite. A failure is a real regression in a money path.
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "paisamap-etl", "etl"))

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/billing_links_default.sqlite")
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


def req_row(rid):
    return sql("SELECT status, target_org_id, resolved_by FROM credit_link_requests WHERE id = :i", i=rid).first()


notices = []
A._dispatch_link_notices = lambda n: notices.extend(n)


def take():
    out = list(notices)
    notices.clear()
    return out


# ═══ cast ═══════════════════════════════════════════════════════════════════
priya, org_ag = person("priya")                     # the agency owner: pays
adira, _ = person("adira")                          # agency admin
kushal, _ = person("kushal")                        # agency plain member
A.add_org_member(org_ag, priya, "adira@example.com", "admin")
A.add_org_member(org_ag, priya, "kushal@example.com", "member")
cy, org_cy = person("cy")                           # a client's owner — owns two companies
cy2 = A.create_organization(cy, "Cy Cafes")["id"]   # (an owner's extra company links under their own wallet by default...)
A.set_org_payer(cy2, cy, None)                      # ...so make it self-paying, like a real independent company
cyst, _ = person("cyst")                            # cy's staffer
A.add_org_member(org_cy, cy, "cyst@example.com", "member")
zed, org_zed = person("zed")                        # a stranger

# ═══ 1. asking ═════════════════════════════════════════════════════════════
check(A.create_link_request(kushal, org_ag, "cy@example.com") == {"error": "forbidden"}, "a plain member can't send a request")
check(A.create_link_request(zed, org_ag, "cy@example.com") == {"error": "not_found"}, "a stranger gets not_found")
for bad in (None, "", "   ", "nope", "a@b", "a b@c.com", "x@y.z" * 60, 5):
    check(A.create_link_request(priya, org_ag, bad) == {"error": "invalid_email"}, f"email {bad!r} is refused")
r_known = A.create_link_request(priya, org_ag, "  CY@Example.com ", "  Hi\x00 Cy,\n\n we'd like to  pay   for your credits  ")
r_unknown = A.create_link_request(priya, org_ag, "nobody-here@example.com")
check(set(r_known) == set(r_unknown) == {"status", "request_id"} and r_known["status"] == r_unknown["status"] == "sent",
      "the answer is identical whether or not the email has an account (no way to probe who uses PaisaMap)")
sent = take()
check(len(sent) == 1 and sent[0]["to"] == "cy@example.com" and sent[0]["kind"] == "requested" and sent[0]["payer_name"] == "priya Co",
      "an email goes out ONLY to an address that already has an account (never to a stranger's inbox)")
check(sent[0]["note"] == "Hi Cy, we'd like to pay for your credits", "the note is cleaned: control characters and runs of whitespace collapsed")
long_note = A.create_link_request(priya, org_ag, "long@example.com", "x" * 500)
check(sql("SELECT note FROM credit_link_requests WHERE id = :i", i=long_note["request_id"]).scalar() == "x" * 200, "a note is capped at 200 characters")
again = A.create_link_request(adira, org_ag, "cy@example.com", "updated note")
check(again["request_id"] == r_known["request_id"], "asking the same address again refreshes the one open request (no duplicates)")
check(sql("SELECT COUNT(*) FROM credit_link_requests WHERE target_email = 'cy@example.com'").scalar() == 1, "still one row")
check(sql("SELECT note FROM credit_link_requests WHERE id = :i", i=r_known["request_id"]).scalar() == "updated note", "...with the new note")
take()
# limits
real_pending, real_day = A.LINK_REQUEST_MAX_PENDING, A.LINK_REQUEST_MAX_PER_DAY
A.LINK_REQUEST_MAX_PENDING = 4
check(A.create_link_request(priya, org_ag, "p4@example.com")["status"] == "sent", "under the open-request cap")
check(A.create_link_request(priya, org_ag, "p5@example.com") == {"error": "too_many_pending"}, "over the open-request cap is refused")
check(A.create_link_request(adira, org_ag, "cy@example.com", "updated note")["request_id"] == r_known["request_id"], "...but refreshing an existing one is still fine")
A.LINK_REQUEST_MAX_PENDING = real_pending
A.LINK_REQUEST_MAX_PER_DAY = 4
check(A.create_link_request(priya, org_ag, "d1@example.com") == {"error": "rate_limited"}, "a daily cap stops a burst of new requests")
A.LINK_REQUEST_MAX_PER_DAY = real_day
sql("DELETE FROM credit_link_requests WHERE target_email IN ('long@example.com','p4@example.com','nobody-here@example.com')")
take()
# a company that is itself paid for can't pay for others
acme = A.create_organization(priya, "Acme Retail")["id"]
check(A.get_payer_org_id(acme) == org_ag, "(Acme is paid for by the agency)")
acmeadmin, _ = person("acmeadmin")
A.add_org_member(acme, priya, "acmeadmin@example.com", "admin")
check(A.create_link_request(priya, acme, "someone@example.com") == {"error": "payer_has_payer"}, "a paid-for company can't ask to pay for another (no chains)")

# ═══ 2. what the person asked sees ══════════════════════════════════════════
A.grant_credits(cy, 40, "bonus", org_id=cy2)          # Cy Cafes holds credits of its own
inc = A.list_incoming_link_requests(cy)["requests"]
check(len(inc) == 1 and inc[0]["payer_name"] == "priya Co" and inc[0]["requested_by_name"] == "adira" and inc[0]["note"] == "updated note",
      "Cy sees who asks, who sent it, and the message (email matched case-insensitively)")
comp = {c["org_id"]: c for c in inc[0]["companies"]}
check(set(comp) == {org_cy, cy2}, "Cy can choose between the companies he OWNS")
check(comp[org_cy]["blocked"] is None and comp[cy2]["blocked"] == "company_has_credits",
      "one that holds its own credits is flagged blocked (linking would strand them)")
check(A.list_incoming_link_requests(zed) == {"requests": []}, "a stranger sees nothing")
check(A.list_incoming_link_requests(cyst) == {"requests": []}, "so does Cy's staffer — only the addressed person decides")
check(A.list_incoming_link_requests(999999) == {"requests": []}, "an unknown user id is harmless")

# ═══ 3. approving ═══════════════════════════════════════════════════════════
rid = r_known["request_id"]
check(A.approve_link_request(zed, rid, org_zed) == {"error": "not_found"}, "someone else can't approve it (same not_found as a missing id)")
check(A.approve_link_request(cy, 999999, org_cy) == {"error": "not_found"}, "a made-up id is not_found")
check(A.approve_link_request(cyst, rid, org_cy) == {"error": "not_found"}, "Cy's staffer, not the addressee, can't approve either")
check(A.approve_link_request(cy, rid, "1") == {"error": "invalid_company"} and A.approve_link_request(cy, rid, True) == {"error": "invalid_company"},
      "the company must be an integer id")
check(A.approve_link_request(cy, rid, org_zed) == {"error": "not_found"}, "he can't link a company he isn't in")
check(A.approve_link_request(cy, rid, cy2) == {"error": "company_has_credits"}, "a company holding its own credits can't be linked")
check(A.approve_link_request(cy, rid, org_ag) == {"error": "not_found"}, "...nor the paying company itself (he isn't in it)")
A.add_org_member(org_cy, cy, "kushal@example.com", "admin")            # kushal is an ADMIN (not owner) of Cy Co
check(A.approve_link_request(kushal, rid, org_cy) == {"error": "not_found"}, "an admin of the company who isn't the addressee can't approve")
check(req_row(rid).status == "pending" and A.get_payer_org_id(org_cy) == org_cy, "none of those attempts changed anything")
check(take() == [], "...or sent anything")
# the addressee must OWN the company
A.add_org_member(org_zed, zed, "cy@example.com", "admin")               # Cy is only an admin of Zed Co
check(A.approve_link_request(cy, rid, org_zed) == {"error": "forbidden"}, "an admin (not the owner) of a company can't link it")
res = A.approve_link_request(cy, rid, org_cy)
check(res == {"status": "ok", "org_id": org_cy} and A.get_payer_org_id(org_cy) == org_ag, "the owner approves: Cy Co is now paid for by the agency")
row = req_row(rid)
check(row.status == "approved" and row.target_org_id == org_cy and row.resolved_by == cy, "the request records what was approved and by whom")
n = take()
check(sorted(x["to"] for x in n) == ["adira@example.com", "priya@example.com"] and all(x["kind"] == "approved" for x in n),
      "the paying company's owner and admins are told (not plain members)")
check(A.approve_link_request(cy, rid, org_cy) == {"error": "not_found"}, "a request can't be approved twice")
check(A.list_incoming_link_requests(cy) == {"requests": []}, "and it leaves the inbox")

# rules that outlive the request
# a company someone already pays for must be DETACHED first — a request can't quietly take it
agencyb, org_agb = person("agencyb")
rB = A.create_link_request(agencyb, org_agb, "cy@example.com")
check(A.approve_link_request(cy, rB["request_id"], org_cy) == {"error": "already_linked"},
      "a company that someone already pays for can't be moved by another request (detach first, which tells its payer)")
check(A.get_payer_org_id(org_cy) == org_ag and req_row(rB["request_id"]).status == "pending", "...it stays where it was, and the request stays open")
# the choices offered leave out companies that are already paid for, and ones that pay for others
cy_hub = A.create_organization(cy, "Cy Hub")["id"]
A.set_org_payer(cy_hub, cy, None)
cy_child = A.create_organization(cy, "Cy Child")["id"]
check(A.set_org_payer(cy_child, cy, cy_hub) == {"status": "ok"}, "(setup: Cy Hub pays for Cy Child)")
incB = [x for x in A.list_incoming_link_requests(cy)["requests"] if x["id"] == rB["request_id"]][0]
check({c["org_id"] for c in incB["companies"]} == {cy2},
      "Cy is only offered a company nobody pays for and that pays for no one (not the linked Cy Co, not the paying Cy Hub, not the paid-for Cy Child)")
check(A.decline_link_request(cy, rB["request_id"]) == {"status": "ok"}, "(Cy declines the other agency's request)")
take()
r3 = A.create_link_request(priya, org_ag, "cy@example.com")
sql("UPDATE credits_ledger SET delta = 0 WHERE org_id = :o AND delta = 40", o=cy2)       # Cy Cafes spent its credits
take()
check(A.approve_link_request(cy, r3["request_id"], cy2)["status"] == "ok" and A.get_payer_org_id(cy2) == org_ag,
      "once Cy Cafes' credits are gone it can be linked too")
take()
# expiry
r4 = A.create_link_request(adira, org_ag, "exp@example.com")
exp_user, org_exp = person("exp")
sql("UPDATE credit_link_requests SET expires_at = :t WHERE id = :i", t=datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1), i=r4["request_id"])
check(A.approve_link_request(exp_user, r4["request_id"], org_exp) == {"error": "expired"}, "an expired request can't be approved")
check(A.list_incoming_link_requests(exp_user) == {"requests": []}, "...and isn't listed")
check([x["status"] for x in A.list_outgoing_link_requests(priya, org_ag)["requests"] if x["id"] == r4["request_id"]] == ["expired"],
      "the payer sees it as expired")
# the requester loses their role -> the request is void
gone_admin, _ = person("goneadmin")
A.add_org_member(org_ag, priya, "goneadmin@example.com", "admin")
lee, org_lee = person("lee")
r5 = A.create_link_request(gone_admin, org_ag, "lee@example.com")
sql("UPDATE org_members SET role = 'member' WHERE org_id = :o AND user_id = :u", o=org_ag, u=gone_admin)
check(A.approve_link_request(lee, r5["request_id"], org_lee) == {"error": "request_invalid"}, "if the requester was demoted meanwhile, approval is refused")
check(req_row(r5["request_id"]).status == "cancelled" and A.get_payer_org_id(org_lee) == org_lee, "...the request is voided and nothing linked")
# a payer that has since been placed under another company
mid, org_mid = person("mid")
top, org_top = person("top")
r6 = A.create_link_request(mid, org_mid, "lee@example.com")
A.add_org_member(org_top, top, "mid@example.com", "admin")
check(A.set_org_payer(org_mid, mid, org_top) == {"status": "ok"}, "(setup: mid's company is now itself paid for)")
check(A.approve_link_request(lee, r6["request_id"], org_lee) == {"error": "payer_has_payer"}, "a payer that is now itself paid for can't take on a client")
# a company that pays for others
sub, org_sub = person("sub")
r7 = A.create_link_request(priya, org_ag, "sub@example.com")
sub_client = A.create_organization(sub, "Sub Client")["id"]                               # sub pays for Sub Client
check(A.approve_link_request(sub, r7["request_id"], org_sub) == {"error": "already_a_payer"}, "a company that pays for others can't itself be moved under a payer")

# ═══ 4. declining and cancelling ════════════════════════════════════════════
dee, org_dee = person("dee")
r8 = A.create_link_request(priya, org_ag, "dee@example.com")["request_id"]
take()
check(A.decline_link_request(zed, r8) == {"error": "not_found"}, "someone else can't decline it")
check(A.decline_link_request(dee, r8) == {"status": "ok"} and req_row(r8).status == "declined", "the addressee declines")
n = take()
check(sorted(x["to"] for x in n) == ["adira@example.com", "priya@example.com"] and all(x["kind"] == "declined" for x in n), "the payer's admins are told")
check(A.decline_link_request(dee, r8) == {"error": "not_found"} and A.approve_link_request(dee, r8, org_dee) == {"error": "not_found"},
      "a declined request can't be reused")
r9 = A.create_link_request(priya, org_ag, "dee@example.com")["request_id"]
check(r9 != r8, "after a decline a fresh request can be made")
check(A.cancel_link_request(kushal, org_ag, r9) == {"error": "forbidden"}, "a plain member can't withdraw a request")
check(A.cancel_link_request(zed, org_ag, r9) == {"error": "not_found"}, "a stranger can't")
other_agency, org_oa = person("otheragency")
check(A.cancel_link_request(other_agency, org_oa, r9) == {"error": "not_found"}, "another company's admin can't cancel it by guessing the id")
check(A.cancel_link_request(adira, org_ag, r9) == {"status": "ok"} and req_row(r9).status == "cancelled", "an admin withdraws it")
check(A.approve_link_request(dee, r9, org_dee) == {"error": "not_found"}, "a withdrawn request can't be approved")
check(A.cancel_link_request(adira, org_ag, r9) == {"error": "not_found"}, "...or withdrawn twice")
out = A.list_outgoing_link_requests(priya, org_ag)["requests"]
check({x["status"] for x in out} >= {"approved", "declined", "cancelled", "expired"} and any(x["target_org_name"] == "cy Co" for x in out),
      "the payer's list shows each request's outcome, and which company was linked")
check(A.list_outgoing_link_requests(kushal, org_ag) == {"error": "forbidden"} and A.list_outgoing_link_requests(zed, org_ag) == {"error": "not_found"},
      "only the payer's admins may list")

# ═══ 5. what each person sees about who pays ═══════════════════════════════
v = A.billing_link_view(cy, org_cy)
check(v["paid_by"] == {"org_id": org_ag, "name": "priya Co"} and v["can_detach"] is True and v["can_manage_links"] is False and v["pays_for"] == [],
      "the client's owner sees who pays and may detach")
v = A.billing_link_view(cyst, org_cy)
check(v["paid_by"]["name"] == "priya Co" and v["can_detach"] is False, "the client's staffer sees who pays but can't detach")
v = A.billing_link_view(priya, org_ag)
check(v["paid_by"] is None and v["can_manage_links"] is True and {c["org_id"] for c in v["pays_for"]} == {acme, org_cy, cy2},
      "the payer's owner sees the companies it pays for")
v = A.billing_link_view(kushal, org_ag)
check(v["can_manage_links"] is False and v["pays_for"] == [], "a plain member of the payer doesn't get the list or the controls")
check(A.billing_link_view(zed, org_ag) == {"error": "not_found"}, "a stranger gets not_found")
# pays_for never lists a company that is not linked under it
check(org_zed not in {c["org_id"] for c in A.billing_link_view(priya, org_ag)["pays_for"]}, "unrelated companies never appear")

# ═══ 6. usage statement ═════════════════════════════════════════════════════
scope("wallet")
A.grant_credits(priya, 5000, "credit_purchase", org_id=org_ag)
A.set_credit_budget(priya, org_ag, org_cy, 1000, "calendar_month")
A.set_credit_budget(priya, org_ag, acme, 1000, "calendar_month")
proj = A.create_project(cy, "Secret Client Project", org_id=org_cy)
A.spend_credits(cy, 8, "forecast", ref_type="project", ref_id=proj["id"], org_id=org_cy)
A.spend_credits(cyst, 8, "forecast", ref_type="project", ref_id=proj["id"], org_id=org_cy)
A.spend_credits(cyst, 10, "report_generate", ref_type="report", ref_id=77, org_id=org_cy)
A.spend_credits(priya, 5, "expansion_recommend", org_id=org_ag)
st = A.usage_statement(priya, org_ag, "this_month")
check(st["scope"] == "wallet" and st["total_credits_used"] == 31, f"the payer's statement totals every spend on its wallet: 8+8+10+5, got {st['total_credits_used']}")
by = {c["org_id"]: c for c in st["companies"]}
check(by[org_cy]["credits_used"] == 26 and by[org_ag]["credits_used"] == 5 and by[acme]["credits_used"] == 0 and by[cy2]["credits_used"] == 0,
      "per company — including linked companies that spent nothing")
check(st["companies"][0]["org_id"] == org_cy, "largest spender first")
ppl = {p["user_id"]: p for p in by[org_cy]["people"]}
check(ppl[cyst]["credits_used"] == 18 and ppl[cy]["credits_used"] == 8 and ppl[cyst]["name"] == "cyst" and ppl[cyst]["email"] == "cyst@example.com",
      "per person, with who they are")
acts = {a["reason"]: a for a in ppl[cyst]["actions"]}
check(acts["forecast"] == {"reason": "forecast", "count": 1, "credits": 8} and acts["report_generate"]["credits"] == 10, "per kind of action")
blob = str(st)
check("ref_id" not in blob and "ref_type" not in blob and "Secret Client Project" not in blob and str(proj["id"]) not in {str(k) for k in ppl[cy]},
      "NO project or location detail anywhere in the statement")
check(not any(k for k in st if "balance" in k), "and no wallet balance")
check(st["total_credits_used"] == sum(c["credits_used"] for c in st["companies"]), "the total is the sum of the companies")
# purchases never count
A.grant_credits(cy, 500, "credit_purchase", org_id=org_cy)
check(A.usage_statement(priya, org_ag, "this_month")["total_credits_used"] == 31, "credits ADDED never show up as usage")
# who may see it
check(A.usage_statement(kushal, org_ag) == {"error": "forbidden"} and A.usage_statement(zed, org_ag) == {"error": "not_found"},
      "plain members and strangers can't")
mine = A.usage_statement(cy, org_cy)
check(mine["scope"] == "company" and [c["org_id"] for c in mine["companies"]] == [org_cy] and mine["total_credits_used"] == 26,
      "a client's own owner sees only THEIR company")
check(A.usage_statement(cyst, org_cy) == {"error": "forbidden"}, "their plain staff can't")
check(A.usage_statement(priya, org_ag, "yesterday") == {"error": "invalid_period"}, "unknown periods are refused")
# periods
check(A.usage_statement(priya, org_ag, "last_month")["total_credits_used"] == 0, "nothing spent last month yet")
old = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=40)
sql("UPDATE credits_ledger SET created_at = :t WHERE org_id = :o AND delta < 0", t=old, o=org_cy)
check(A.usage_statement(priya, org_ag, "this_month")["total_credits_used"] == 5, "moved out of this month...")
check(A.usage_statement(priya, org_ag, "last_30d")["total_credits_used"] == 5, "...and out of the last 30 days...")
sql("UPDATE credits_ledger SET created_at = :t WHERE org_id = :o AND delta < 0", t=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=3), o=org_cy)
check(A.usage_statement(priya, org_ag, "last_30d")["total_credits_used"] == 31, "last_30d picks recent spend up")
now = datetime(2026, 1, 15, 12, tzinfo=UTC)
s0, e0 = A.statement_window("this_month", now)
s1, e1 = A.statement_window("last_month", now)
check(e1 == s0 and s1 == datetime(2025, 11, 30, 18, 30, tzinfo=UTC) and s0 == datetime(2025, 12, 31, 18, 30, tzinfo=UTC),
      "last_month is the IST calendar month before this one (December, from a January date)")
check(A.statement_window("last_30d", now) == (now - timedelta(days=30), now) and A.statement_window("bad", now) is None, "windows: last_30d and unknown")

# ═══ 7. detaching ═══════════════════════════════════════════════════════════
A.grant_credits(priya, 0, "noop")
check(A.unlink_company(cyst, org_cy) == {"error": "forbidden"}, "the client's plain staff can't detach")
check(A.unlink_company(kushal, org_cy) == {"error": "forbidden"}, "an admin of the client (not owner, not a payer admin) can't detach")
check(A.unlink_company(zed, org_cy) == {"error": "not_found"} and A.unlink_company(zed, 999999) == {"error": "not_found"}, "strangers / missing companies: not_found")
check(A.unlink_company(priya, org_ag) == {"error": "not_linked"}, "a company nobody pays for has nothing to detach")
A.set_member_budget(cy, org_cy, cyst, 20, "calendar_month")
take()
check(A.unlink_company(cy, org_cy) == {"status": "ok"} and A.get_payer_org_id(org_cy) == org_cy, "the client's owner detaches: Cy Co pays for itself again")
n = take()
check(sorted(x["to"] for x in n) == ["adira@example.com", "priya@example.com"] and all(x["kind"] == "detached" for x in n),
      "the PAYER's admins are told (the other side of whoever detached)")
check(sql("SELECT COUNT(*) FROM credit_budgets WHERE org_id = :o AND kind = 'cap'", o=org_cy).scalar() == 0, "the payer's cap went with the link")
check(sql("SELECT COUNT(*) FROM credit_member_budgets WHERE org_id = :o", o=org_cy).scalar() == 1, "the company's own personal allowances stay")
check(A.get_credit_balance(cy, org_id=org_cy) == 0, "Cy Co's own wallet starts empty — nothing is moved across")
try:
    A.spend_credits(cy, 1, "forecast", org_id=org_cy)
    check(False, "an empty wallet must refuse")
except A.InsufficientCreditsError:
    check(True, "...so it can't spend until it buys credits")
check(A.usage_statement(priya, org_ag, "this_month")["total_credits_used"] == 31, "the payer's statement still shows what it paid for while linked")
check(A.unlink_company(cy, org_cy) == {"error": "not_linked"}, "detaching twice is refused")
check(A.unlink_company(adira, acme)["status"] == "ok" and A.get_payer_org_id(acme) == acme, "the PAYER's admin detaches a client too")
n = take()
check(all(x["kind"] == "detached" for x in n) and sorted(x["to"] for x in n) == ["acmeadmin@example.com", "priya@example.com"],
      "...and it is the CLIENT's owner and admins who are told (the other side)")
sql("DELETE FROM credit_link_requests WHERE 1=1")
# re-linking after a detach needs a new request, and works
back = A.create_link_request(priya, org_ag, "cy@example.com")
sql("UPDATE credits_ledger SET delta = 0 WHERE org_id = :o AND reason = 'credit_purchase'", o=org_cy)
sql("UPDATE credits_ledger SET delta = 0 WHERE billing_org_id = :o", o=org_cy)
check(A.approve_link_request(cy, back["request_id"], org_cy)["status"] == "ok" and A.get_payer_org_id(org_cy) == org_ag, "a detached company can be linked again through a new request")
take()
scope(None)

# ═══ 8. over HTTP ═══════════════════════════════════════════════════════════
import server
app = server.app
app.testing = True


def call(uid, method, url, **kw):
    c = app.test_client()
    if uid:
        with c.session_transaction() as sess:
            sess["user_id"] = uid
    return c.open(url, method=method, **kw)


base = f"/api/organizations/{org_ag}/link-requests"
check(call(None, "POST", base, json={"email": "h1@example.com"}).status_code == 401, "asking needs a login")
r = call(priya, "POST", base, json={"email": "h1@example.com", "note": "hello"})
check(r.status_code == 201 and r.get_json()["status"] == "sent", "POST creates a request (201)")
r_unknown = call(priya, "POST", base, json={"email": "cy@example.com"})
check(set(r.get_json()) == set(r_unknown.get_json()), "same response shape for a known and an unknown email")
check(call(priya, "POST", base, json={"email": "bad"}).status_code == 400, "bad email -> 400")
check(call(kushal, "POST", base, json={"email": "h2@example.com"}).status_code == 403, "plain member -> 403")
check(call(zed, "POST", base, json={"email": "h2@example.com"}).status_code == 404, "stranger -> 404")
check(call(priya, "POST", f"/api/organizations/{acme}/link-requests", json={"email": "h2@example.com"}).status_code == 201,
      "a company that was detached (no longer paid for) can ask to pay for others again")
r = call(priya, "GET", base)
check(r.status_code == 200 and any(x["target_email"] == "h1@example.com" for x in r.get_json()["requests"]), "GET lists the payer's requests")
check(call(kushal, "GET", base).status_code == 403, "outgoing list: plain member 403")
r = call(cy, "GET", "/api/organizations/link-requests/incoming")
check(r.status_code == 200 and len(r.get_json()["requests"]) == 1, "the incoming route resolves (it is not swallowed by the numeric routes)")
rid = r.get_json()["requests"][0]["id"]
check(call(zed, "POST", f"/api/organizations/link-requests/{rid}/approve", json={"org_id": org_zed}).status_code == 404, "someone else approving -> 404")
check(call(cy, "POST", f"/api/organizations/link-requests/{rid}/approve", json={"org_id": "x"}).status_code == 400, "junk company -> 400")
check(call(cy, "POST", f"/api/organizations/link-requests/{rid}/approve", json={}).status_code == 400, "missing company -> 400")
check(call(cy, "POST", f"/api/organizations/link-requests/{rid}/decline").status_code == 200, "decline over HTTP")
check(call(cy, "POST", f"/api/organizations/link-requests/{rid}/decline").status_code == 404, "...only once")
exp_req = A.create_link_request(priya, org_ag, "cy@example.com")["request_id"]
sql("UPDATE credit_link_requests SET expires_at = :t WHERE id = :i", t=datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1), i=exp_req)
check(call(cy, "POST", f"/api/organizations/link-requests/{exp_req}/approve", json={"org_id": org_cy}).status_code == 410, "an expired request -> 410")
new_req = A.create_link_request(priya, org_ag, "cy@example.com")["request_id"]
sql("UPDATE org_members SET role = 'member' WHERE org_id = :o AND user_id = :u", o=org_cy, u=cy)          # Cy is a plain member of Cy Co now
check(call(cy, "POST", f"/api/organizations/link-requests/{new_req}/approve", json={"org_id": org_cy}).status_code == 403, "not the owner -> 403")
sql("UPDATE org_members SET role = 'owner' WHERE org_id = :o AND user_id = :u", o=org_cy, u=cy)
check(call(cy, "POST", f"/api/organizations/link-requests/{new_req}/approve", json={"org_id": cy2}).status_code == 409,
      "approving a company that is already paid for -> 409")
r = call(cy, "GET", f"/api/organizations/{org_cy}/billing-link")
check(r.status_code == 200 and r.get_json()["paid_by"]["name"] == "priya Co", "GET billing-link")
check(call(zed, "GET", f"/api/organizations/{org_cy}/billing-link").status_code == 404, "billing-link for a stranger -> 404")
check(call(cyst, "POST", f"/api/organizations/{org_cy}/detach").status_code == 403, "detach by plain staff -> 403")
check(call(cy, "POST", f"/api/organizations/{org_cy}/detach").status_code == 200, "detach over HTTP")
check(call(cy, "POST", f"/api/organizations/{org_cy}/detach").status_code == 409, "detach again -> 409")
r = call(priya, "GET", f"/api/organizations/{org_ag}/usage-statement?period=last_30d")
check(r.status_code == 200 and r.get_json()["period"] == "last_30d", "GET usage-statement with a period")
check(call(priya, "GET", f"/api/organizations/{org_ag}/usage-statement?period=nope").status_code == 400, "bad period -> 400")
check(call(kushal, "GET", f"/api/organizations/{org_ag}/usage-statement").status_code == 403, "plain member -> 403")
check(call(zed, "GET", f"/api/organizations/{org_ag}/usage-statement").status_code == 404, "stranger -> 404")

# ═══ 9. the emails ══════════════════════════════════════════════════════════
import _email
sent = {}


class FakeSES:
    def send_email(self, **kw):
        sent.update(kw)


check(_email.send_billing_link_notice("a@example.com", "requested", "P", "", "https://x/y") is False, "with SES unconfigured a send just returns False")
_real_client = _email._client
_email._client = lambda: FakeSES()
os.environ["SES_FROM_EMAIL"] = "noreply@example.com"
check(_email.send_billing_link_notice("a@example.com", "requested", "<b>Agency</b>", "", "https://x/y?a=1&b=2", "<i>Bob</i>", "<script>alert(1)</script>") is True, "SES accepts a request notice")
h = sent["Message"]["Body"]["Html"]["Data"]
check("<script>" not in h and "<b>" not in h and "<i>" not in h and "&lt;script&gt;" in h, "names and the note are HTML-escaped")
check("asked to pay" in sent["Message"]["Subject"]["Data"], "the request subject says so")
for kind, word in (("approved", "accepted"), ("declined", "declined"), ("detached", "no longer paid")):
    _email.send_billing_link_notice("a@example.com", kind, "Agency", "Client", "https://x/y")
    check(word in sent["Message"]["Subject"]["Data"], f"the {kind} subject says so")
_email._client = _real_client
os.environ.pop("SES_FROM_EMAIL", None)
calls = []
_real_send = _email.send_billing_link_notice


def fake_send(to, kind, payer, company, url, actor=None, note=None):
    calls.append(to)
    if to == "bad@example.com":
        raise RuntimeError("smtp down")


_email.send_billing_link_notice = fake_send
A._send_link_notices([dict(to=t, kind="approved", payer_name="P", company_name="C") for t in ("a@example.com", "bad@example.com", "c@example.com")])
_email.send_billing_link_notice = _real_send
check(calls == ["a@example.com", "bad@example.com", "c@example.com"], "one failing recipient doesn't stop the others")

# ═══ 10. housekeeping ═══════════════════════════════════════════════════════
tmp, tmp_org = person("tmpuser")
tr = A.create_link_request(tmp, tmp_org, "someone@example.com")
check(A.delete_organization(tmp_org, tmp), "a company with only a request behind it can be deleted")
check(sql("SELECT COUNT(*) FROM credit_link_requests WHERE payer_org_id = :o", o=tmp_org).scalar() == 0, "...and its requests go with it")
print(f"OK — {passed} link-request checks passed")

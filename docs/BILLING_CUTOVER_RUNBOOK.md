# Billing cut-over runbook — shared wallets

**What this does.** Today every user has their own credit balance and their own
plan. After this, credits live in a company's *wallet*: everyone in the company
(and in any client company linked under it) spends from one pool, each spend is
logged against the company it was for, and a teammate inherits their company's
plan. Nothing changes for someone who is alone in one company.

**It ships switched off.** The new code reads a setting, `BILLING_SCOPE`. Unset
(or `user`) = exactly today's behaviour. Only `BILLING_SCOPE=wallet` turns the
new behaviour on. So deploying is safe, and the risky moment is one deliberate
step you take (step 6) after you've seen real numbers.

Every command below is run from your Mac, in the `paisa-map` folder. Nothing
prints a secret.

```
SSH() { ssh -i ~/.ssh/paisamap_lightsail ubuntu@paisamaps.com "$@"; }
```

---

## Step 1 — add five empty columns to the database  *(you)*

**Why first:** the new code asks for these columns. If the code goes live before
they exist, every order and company lookup fails. Old code simply ignores them,
so adding them first is harmless.

```
SSH "sudo bash -c 'set -a; . /etc/paisamap/db.env; set +a; exec /home/ubuntu/paisa-map/venv-flask/bin/python3 -'" < paisamap-etl/db/apply_billing_v2_columns.py
```

**Good looks like:** five lines saying `added: ...`, then `OK — all five columns present`.
Running it again prints `already there` and the same OK — it is safe to repeat.

**If it errors:** stop and tell me the message. Nothing else depends on it yet.

## Step 2 — deploy the code  *(me, when you say "columns done")*

I commit, push, and confirm `/api/health` shows the new version. The site
behaves exactly as before, because the switch is off. I'll re-check that
signing in, credits, and the map still work.

## Step 3 — look before touching anything  *(you, read-only)*

```
SSH "sudo bash -c 'set -a; . /etc/paisamap/db.env; set +a; cd /home/ubuntu/paisa-map; exec venv-flask/bin/python3 paisamap-etl/db/billing_cutover.py report'"
```

**How to read it:**

| Field | Meaning |
|---|---|
| `ready_to_flip` | `true` = safe to continue. `false` = read `blocking_problems`. |
| `blocking_problems` | Plain-English things to fix first. Usually "N ledger rows have no company" and/or "extra companies not linked" — step 4 fixes both. |
| `users_whose_balance_or_plan_would_change` | **The important one.** Anyone listed here would see a *different* balance or plan after the flip. You want `[]`. If someone is listed, do not flip — paste it to me. |
| `informational.plan_mismatches` | Owners whose personal plan differs from their company's plan. Expected for your own account (you're a hand-set `pro`). Not blocking. |

## Step 4 — the two one-time data steps  *(you, this one writes)*

```
SSH "sudo bash -c 'set -a; . /etc/paisamap/db.env; set +a; cd /home/ubuntu/paisa-map; exec venv-flask/bin/python3 paisamap-etl/db/billing_cutover.py apply'"
```

It does two things, then prints the report again:

1. **Stamps every old credit-ledger row** with its company and wallet (rows
   written before the new code have neither).
2. **Links each extra company under its owner's main company**, merging its
   credits in. Before wallets, a person's credits followed *them* across all
   their companies, so this keeps that true.

It is safe to run twice (the second run reports zeros). It never deletes rows or
changes an amount.

**Good looks like:** `"ready_to_flip": true` and an empty
`users_whose_balance_or_plan_would_change`.

## Step 5 — wait, and look at the site  *(you)*

Nothing has changed for users yet. Use the site normally, make sure the header
credits number looks right. There is no rush between steps 4 and 6.

## Step 5b — create the billing side tables  *(you; once the code is deployed, and before step 6)*

Budgets and link requests need three new tables: `credit_budgets` (company budgets + the reserve),
`credit_member_budgets` (personal allowances) and `credit_link_requests` (requests to pay for another company). The script uses the deployed code's own
definitions and only ever ADDS: it creates missing tables and adds missing nullable columns to a
table an earlier version of the script already made. It never alters or drops anything, so it is
safe to run any number of times — including once now and again after a later deploy.

```
SSH "sudo bash -c 'set -a; . /etc/paisamap/db.env; set +a; cd /home/ubuntu/paisa-map; exec venv-flask/bin/python3 paisamap-etl/db/apply_budgets_table.py'"
```

**Good looks like:** `created:` (or `added column:` / `already there:`) lines, then `OK — 3 billing tables present, all columns match`.

The site keeps working if you run it late: until the tables exist, only the budget screens (hidden
anyway while there is nothing to budget) can fail, and relinking or deleting a company skips the budget
cleanup. Step 3's report also lists a missing table as a blocker before the flip, and the flip itself
must not happen without both tables, because wallet mode reads them on every spend.

**80% / 100% alert emails** go to the owners and admins of the paying company and of the budgeted
company, once per level per period. They send only if email is configured on the server
(`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION` and `SES_FROM_EMAIL` in `/etc/paisamap/db.env`
— the same setting that makes invite emails send). Without it nothing breaks: the alert is logged and
the banner on the Billing page and Dashboard is still the warning. To check without printing any secret:

```
SSH "sudo grep -c '^SES_FROM_EMAIL=' /etc/paisamap/db.env"
```

`1` means it is set, `0` means invite and alert emails are not being sent.

## Step 5c — schema for company-attributed API keys and website verification  *(you; before or right after the deploy that carries them)*

Round 4 adds: a company on every API key (`api_keys.org_id`), proof that a company owns its website
(`org_domains` table) and requests addressed by website (`credit_link_requests.via` / `.target_domain`).

**Recommended, BEFORE the push (so the API-keys page never errors in between):** three empty columns,
harmless to the old code.

```
SSH "sudo bash -c 'set -a; . /etc/paisamap/db.env; set +a; exec /home/ubuntu/paisa-map/venv-flask/bin/python3 -'" < paisamap-etl/db/apply_billing_v2_round4_columns.py
```

**Good looks like:** three `added:` (or `already there:`) lines, then `OK — the round 4 columns are present`.

**After the deploy:** the script creates the new table, gives every existing API key the company of the person
who made it, and checks every column. Idempotent; only ever adds.

```
SSH "sudo bash -c 'set -a; . /etc/paisamap/db.env; set +a; cd /home/ubuntu/paisa-map; exec venv-flask/bin/python3 paisamap-etl/db/apply_billing_v2_round4.py'"
```

**Good looks like:** `created table: org_domains`, the `added column:` / `already there:` lines, one `api keys: N total, M still
without a company` line, then `OK — company-attributed keys, website verification and website requests are ready`.
A key whose maker has no company keeps working exactly as before (it just isn't in any company's admin view).

If the deploy reaches the server before this step: every existing API key still authenticates (the lookup falls back to
the old query), and the API-keys page and the Who-pays request list show an error until the columns exist.

## Step 5d — map legacy accounts onto v2 tiers  *(you; the LAST step before the flip — nothing before it depends on this)*

Sets `organizations.plan = v2_<tier>` on each company owned by an account that is on a legacy paid plan. It never touches
`users.plan`, so it changes **nothing anyone can see until step 6**, and even then only raises an account. The default
tier is the lowest that keeps everything the account can do today — **Growth** for both legacy `pro` and `team` (Growth is
the lowest tier that keeps the raised API limit). Use `--override` for anything else, e.g. your own account.

Look first (read-only; prints ids and tier names, never emails):

```
SSH "sudo bash -c 'set -a; . /etc/paisamap/db.env; set +a; cd /home/ubuntu/paisa-map; exec venv-flask/bin/python3 paisamap-etl/db/plan_mapping.py report --override YOUR_EMAIL=pro'"
```

Read the list. Anything under **BLOCKED** would give an account less than it has now — it is never written unless you add
`--allow-downgrade`. Then write it (same flags plus `--yes`; without `--yes` it is only a preview):

```
SSH "sudo bash -c 'set -a; . /etc/paisamap/db.env; set +a; cd /home/ubuntu/paisa-map; exec venv-flask/bin/python3 paisamap-etl/db/plan_mapping.py apply --yes --override YOUR_EMAIL=pro'"
```

Undo (puts back exactly what `apply` changed; a company whose plan has changed since is left alone and listed):

```
SSH "sudo bash -c 'set -a; . /etc/paisamap/db.env; set +a; cd /home/ubuntu/paisa-map; exec venv-flask/bin/python3 paisamap-etl/db/plan_mapping.py rollback --yes'"
```

After `apply`, step 3's report (`billing_cutover.py report`) shows, under "plan now / plan after flip", exactly who changes and to what.
While `BILLING_SCOPE` is unset, the Billing page still shows a mapped account the legacy plan cards (the mapping isn't read yet);
once it is `wallet`, that account sees "Growth" and no upgrade buttons.

## Step 6 — flip the switch  *(you; I can't do this one — server settings edits are blocked for me)*

```
SSH "printf '\nBILLING_SCOPE=wallet\n' | sudo tee -a /etc/paisamap/db.env >/dev/null && sudo systemctl restart paisamap"
```

Then check:

- `curl -s https://paisamaps.com/api/health` → `"status":"ok"`
- Sign in. **The credits number in the header should be the same as before.**
- Run the credits page. The numbers should match what you saw in step 3.
- Run one small action that costs credits (a forecast), and confirm the balance
  drops by exactly that cost, and the credits page shows the new row.

## Rollback — one line, any time  *(you)*

```
SSH "sudo sed -i '/^BILLING_SCOPE=/d' /etc/paisamap/db.env && sudo systemctl restart paisamap"
```

Balances read as they did before the flip, and this is lossless for anyone who
was alone in their company (checked: the same numbers before, during, and after).

**One real caveat, only once teams exist:** while wallet mode is on, a teammate's
spend is deducted from the shared pool but recorded against the *teammate*. After
a rollback, each person's total is again "their own grants minus their own
spends" — so whoever *bought* the credits shows their purchase minus only *their
own* spending, and does not see the teammates' spending deducted. A teammate who
never bought anything shows 0 (never a negative number). Nothing is lost from the
ledger, but the per-person totals stop meaning "what's left in the company".
So: rolling back is safe on day one and for solo accounts; once real teams have
been spending from a shared wallet, prefer fixing forward.

---

## What is NOT done yet

- **No screen or API to link a company under a paying account.** The database
  function exists and is tested; there is no button. Companies you create are
  linked under your own wallet automatically. Until the button exists, linking
  an agency's client company is one database call that has to be run on the
  server (I can prepare the exact command; running it needs your approval).
- **Your own plan** stays a hand-set `pro` until you run step 5d (the tier mapping is the last step before
  go-live, and is built — see 5d). One consequence until then: in wallet mode a teammate inherits their *company's*
  plan, and your company's plan is still `free` (only your personal plan is `pro`), so a teammate you invite would
  not inherit `pro` until 5d sets the company's plan. It never lowers anyone.
- **Done since first written:** the header, Dashboard and Billing page now follow
  the company selected in the switcher, and a client company's own staff no longer
  see the paying company's wallet balance (they see who pays instead).
- **Budgets (built; enforced only in wallet mode).** An owner/admin of the paying company
  sets a credit budget per company (Billing page -> "Credit budgets", shown once the company
  pays for another one): a period (billing cycle - the calendar month until subscriptions
  exist - calendar month, weekly, quarterly, one-off total, or until a date), an amount, and
  optionally a reserve the paying company keeps back for its own work. Warning at 80%, hard
  stop at 100%, no rollover. A person who only belongs to a *client* company paid for by
  another can spend **only** once that company has an active budget (otherwise: "ask <payer>
  to set a budget"). Once a budget is set it caps EVERYONE spending on that company, agency
  staff and the owner included. The paying company's own spending is never limited by its own
  reserve. Nothing changes for a solo account with no budget.
- **Personal allowances (built).** An owner/admin of a company — a client's own admin included — can
  give each colleague a spending allowance inside the company (Billing page -> "Personal allowances"),
  never above the company's own budget; the payer's admins can too. The lower of the personal
  allowance and the company budget binds. A client admin can only work inside an existing company budget.
- **Alert emails (built; need email configured — see step 5b).** 80% and 100% of a company budget, once
  per period, to the owners/admins of both companies. At-most-once: if a send is lost, the banner is the fallback.
- **Who pays: link requests, detach, usage statement (built).** A paying company's owner/admin asks by
  EMAIL (Billing -> "Who pays"); the person asked sees it on the Dashboard and in Billing, picks which
  company THEY OWN to link, and approves — nothing links without them. The answer to the asker is the
  same whether or not the email has an account, and an email goes out only to an address that already
  has one. Either side can detach at any time (the client's owner, or the payer's admins): the company
  then uses its own wallet, which starts at 0; nothing is moved; the payer's cap on it is dropped. A
  company someone already pays for must be detached before another request can take it, and a company
  holding credits of its own can't be linked until they're spent. The payer's **usage statement**
  shows credits spent per company, per person, per kind of action — never a project or location —
  and a client's own owner/admin sees the same for just their company.
- **Website verification + "request to connect" by website (built, round 4; step 5c).** A company lists a website
  (Company settings) and proves it owns it: a `<meta name="paisamap-site-verification">` tag in its home page's head, or
  a Google Search Console account already connected to the company that is a verified **owner** of the domain
  (GA4 and "full user" access are deliberately NOT accepted — agencies are routinely given them for a client's site).
  An unverified website is only "verification pending": nobody can find or connect to the company by it. A paying
  company can type a website; if a company has verified it (and left "let a company that types your website ask to
  connect" on, its default) the asker learns only that one exists — never its name, owner or email — and can send a
  request, which every owner of that company sees and only an owner can approve. One verified company per domain.
  Lookups are limited to 30 a day per person. Changing the website to a different domain drops the verification.
- **API keys belong to a company (built, round 4; step 5c).** Every key is attributed to the company chosen when it
  was made (default: the person's own). That company's owners/admins — and its payer's — see every key with who made it
  and today's usage, and can revoke any; all keys of one company share one rate limit (more keys buy no throughput); a key
  stops working the moment its owner leaves the company, and stays dead if they are re-added. API access remains a plan
  entitlement, not per-call credits.
- **Not built yet:** alerts for personal allowances (banner only); usage-statement export/PDF; DNS-record verification;
  re-checking a verified website later (a domain that changes hands keeps its verification until someone removes it);
  seat / company-count limits from the price book (they arrive with subscriptions).
- **Concurrent spends on Postgres are untested here.** The wallet is locked
  during a spend (the same technique the old per-user path used) and the test
  suite proves the rule, but SQLite cannot reproduce two simultaneous spends.
  At today's traffic the risk is negligible; revisit before heavy team use.
- **API keys inherit the company plan** in wallet mode (a member's personal key
  gets their company's plan, the same as their login does). If you'd rather keep
  keys strictly personal, say so and I'll change that one line.

## Where each piece lives

| | |
|---|---|
| The switch | `BILLING_SCOPE` in `/etc/paisamap/db.env` |
| Column script | `paisamap-etl/db/apply_billing_v2_columns.py` |
| Round 4 schema | `paisamap-etl/db/apply_billing_v2_round4.py` |
| Legacy -> v2 tier mapping | `paisamap-etl/db/plan_mapping.py` (logic in `_plan_mapping.py`) |
| Report + one-time steps | `paisamap-etl/db/billing_cutover.py` |
| Tests | `tests/test_billing_wallet_cutover.py` (both scopes, incl. the client-privacy checks), `tests/test_billing_org_staging.py`, round 4: `tests/test_billing_api_keys.py`, `tests/test_billing_domains.py`, `tests/test_billing_round4_http.py`, `tests/test_plan_mapping.py` |
| Decisions | `docs/PRICING_MODEL.md` decisions #15 and #16 and their session log |

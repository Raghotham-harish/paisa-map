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

## Step 5b — create the budgets table  *(you; do this any time after the budgets code is deployed, and before step 6)*

Budgets need one new table, `credit_budgets`. The script uses the deployed code's own
definition and only creates what is missing — it never alters or drops anything, and it is
safe to run twice.

```
SSH "sudo bash -c 'set -a; . /etc/paisamap/db.env; set +a; cd /home/ubuntu/paisa-map; exec venv-flask/bin/python3 paisamap-etl/db/apply_budgets_table.py'"
```

**Good looks like:** `created:  credit_budgets` (or `already there`), then `OK — credit_budgets present with 11 columns`.

The site keeps working if you run it late: until the table exists, only the budget screens
(which are hidden anyway while there is nothing to budget) can fail, and relinking or deleting a
company skips the budget cleanup. Step 3's report also lists a missing table as a blocker
before the flip, and the flip itself must not happen without it, because wallet mode reads
the table on every spend.

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
- **Your own plan** stays a hand-set `pro`, deliberately (the tier mapping is the
  last step before go-live). One consequence: in wallet mode a teammate inherits
  their *company's* plan, and your company's plan is still `free` (only your
  personal plan is `pro`), so a teammate you invite would not inherit `pro`
  until that mapping step sets the company's plan. It never lowers anyone.
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
- **Not built yet:** a budget can only be set by a paying admin (a client's own admin can't
  carve sub-budgets yet); no email when a budget hits 80% (the warning shows on the Billing
  page and Dashboard); a screen to link/unlink companies; the usage statement.
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
| Report + one-time steps | `paisamap-etl/db/billing_cutover.py` |
| Tests | `tests/test_billing_wallet_cutover.py` (both scopes, incl. the client-privacy checks), `tests/test_billing_org_staging.py` |
| Decisions | `docs/PRICING_MODEL.md` decisions #15 and #16 and their session log |

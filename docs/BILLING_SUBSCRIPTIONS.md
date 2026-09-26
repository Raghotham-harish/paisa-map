# Billing-v2 P2 + P13 + P12 rail — subscriptions, dunning/soft-lock, assisted sales

Status: **built and tested, dark.** Nothing here is reachable until
`BILLING_SUBSCRIPTIONS=1`. Branch `feat/billing-p2-subscriptions`; not pushed.
Round 1 = subscriptions (P2). Round 2 = dunning + soft-lock (P13). Round 3 = the sales-assisted
invoice/NEFT rail (assisted sales), below.

## What exists

| Piece | Where |
|---|---|
| Tables `subscriptions`, `razorpay_plans` | `paisamap-etl/etl/_auth_db.py` (+ `db/apply_subscriptions_tables.py` for prod) |
| State machine, idempotent charge handling, plan grant/revoke, sweep | `paisamap-etl/etl/_subscriptions.py` |
| Self-serve eligibility + per-cycle amount | `paisamap-etl/etl/_pricing.py` (`subscription_error`, `subscription_amount_paise`) |
| Routes + `subscription.*` webhook | `blueprints/billing.py` (`/api/billing/subscriptions[...]`) |
| Cron backstop: revokes ended plans, expires assisted sales, sends dunning emails | `paisamap-etl/db/subscription_sweep.py` |
| Assisted sales: logic + audit table `assisted_sales` | `paisamap-etl/etl/_assisted_sale.py` |
| Assisted sales: operator CLI (dry run by default) | `paisamap-etl/db/assisted_sale.py` |
| Tests: assisted sales 108 checks / 37 mutants, all killed | `tests/test_assisted_sale.py` |
| Tests: subscriptions 147 checks / 34 mutants (33 killed, 1 equivalent) | `tests/test_billing_subscriptions.py` |
| Tests: dunning + lock 93 checks / 35 mutants (33 killed, 2 redundant-guard equivalents) | `tests/test_billing_dunning.py` |

Routes (all `require_login`, owner/admin of the **paying** company only):
`GET /api/billing/subscriptions`, `POST /subscriptions` `{tier, org_id?}`,
`POST /subscriptions/verify` (after Checkout succeeds), `POST /subscriptions/cancel`.
The webhook is the existing `/api/billing/webhook`, now also handling `subscription.*`.

## How it behaves

- **Self-serve = monthly Starter and Growth only.** Scale and above are invoice/NEFT,
  and annual is far over the ₹15,000 UPI-Autopay/e-mandate cap, so both stay on the
  sales-assisted rail (PRICING §7).
- **Plan is granted only when a charge succeeds** (`subscription.charged` or a verified
  payment), never on "authenticated". The plan is `organizations.plan = 'v2_<tier>'`
  on the paying company; every linked company's members inherit it.
- **Idempotent by payment id.** Each charge becomes one local paid order
  (`razorpay_order_id = sub_charge_<payment id>`, `kind='plan_upgrade'`, no schema
  change to `orders`) plus one invoice. /verify, the webhook, and any redelivery
  converge on the same rows.
- **Ordering-safe.** An older webhook can't overwrite newer state; an ended
  subscription is never resurrected.
- **Never clobbers a hand-assigned plan.** Granting keeps a higher v2 tier; revoking
  only touches a plan that is still exactly the subscription's tier. A company already
  on the same or a higher tier by hand is told to talk to us (409 `plan_managed_directly`).
- **Cancel keeps access to the end of the paid cycle** (no partial-period refund). A
  subscription that never had a paid cycle cancels immediately.
- **Refuses to start** unless: flag on, `BILLING_SCOPE=wallet` (else a customer would
  pay for a plan nothing reads), live keys also need `BILLING_LIVE_PURCHASES=1`, and
  the tables exist (else a payment would have nowhere to be recorded).

## P13 — failed renewals, dunning and the soft-lock (round 2)

- **Past due** starts when a *renewal* fails (`subscription.pending`/`halted` after at
  least one paid cycle; a failed *first* charge granted nothing, so it isn't debt).
  The clock starts once and never restarts. Only a real charge clears it — a bare
  "active" status event does not.
- **Locked** = `DUNNING_DAYS` (3) after that, computed at read time from
  `past_due_since`, so it starts on the dot with no job needed. Only a *live*
  subscription can lock; once cancelled there is no debt.
- **The lock is a backend refusal, not a blur.** A locked company's plan is suspended
  for everyone it covers (`get_effective_plan_for_user`, so Pro columns and API-key
  elevation drop too); credit spending is refused everywhere (`_spend_block`, pre-check
  *and* the atomic charge) with **402 `account_locked`**; report PDF downloads are
  refused; forecast/expansion/report generation go through the same gate. A member's
  *own* paid plan is not suspended by someone else's lock.
- **Past due (even inside the grace) blocks new credit and report purchases** (402
  `unpaid_balance`) — otherwise a broken payment method lets an account top up for free.
- **Nothing is deleted**; saved projects/locations/reports stay visible, billing and
  invoices stay reachable. One successful charge restores everything.
- **Emails** (once each, to the paying company's owners/admins): past-due, locked,
  restored. They need SES creds on the server (still missing — see checklist U4); until
  then they log a warning and nothing else breaks.
- **`POST /subscriptions/recover`** replaces a failing subscription with a fresh one
  (Razorpay can't swap a failing mandate). The replacement is created first; the old
  one keeps access to the original lock time. Cancelling is also always allowed, and a
  cancelled company is simply a former customer, not "locked".
- `GET /api/auth/me` now carries `account: {past_due, locked, lock_at}` (or `null`) for
  the dashboard's overlay; `GET /subscriptions` carries `standing`.
- Also fixed (found while designing this): cancel-then-resubscribe before the old
  paid period ended used to let the *old* subscription's end revoke the plan the *new*
  one had just granted.

**Still not done for P13:** the frontend blur overlay and Billing UI ("update payment"
button), and the deferred top-up accrual ledger (mid-cycle top-ups billed on the next
renewal via Razorpay add-ons). Top-ups are a normal one-off checkout for now.

## Assisted sales — the invoice/NEFT rail (round 3)

For Scale and above, anything annual, and pilots: the customer pays against an invoice
outside Razorpay, and **you** record it on the server. There is deliberately **no web
endpoint** for this (no new login surface); it is a CLI that shows a dry run first.

```zsh
ASALE() { ssh -i ~/.ssh/paisamap_lightsail ubuntu@paisamaps.com \
  "sudo bash -c 'set -a; . /etc/paisamap/db.env; set +a; cd /home/ubuntu/paisa-map; \
   exec venv-flask/bin/python3 paisamap-etl/db/assisted_sale.py $*'"; }

ASALE grant --owner-email buyer@co.com --tier scale --months 12 \
      --invoice PM/2026-27/001 --amount 300000 --method neft --ref UTR123456789       # dry run
ASALE grant --owner-email buyer@co.com --tier scale --months 12 \
      --invoice PM/2026-27/001 --amount 300000 --method neft --ref UTR123456789 --yes # apply
ASALE grant --org-id 42 --tier growth --months 3 --method complimentary \
      --invoice PILOT-001 --amount 0 --note "3-month design-partner pilot" --yes
ASALE list [--org-id 42] [--status active]
ASALE revoke --sale-id 7 --reason "refunded, invoice cancelled" --yes
```

- `--amount` is **rupees actually received** (0 only for `--method complimentary`, which
  needs a `--note`). A paid sale needs `--ref` (UTR / cheque no.). `--credits` defaults to
  the tier's monthly credits × months.
- **One invoice number is recorded once** (unique), so re-running the same command
  grants nothing twice. The `assisted_sales` row is the audit trail: invoice, amount,
  method, reference, period, plan before, and who ran it (`SUDO_USER`).
- Sets the **paying** company's plan (a linked client is refused, naming its payer);
  refuses a company with a live *paid* subscription (no double billing) or already on a
  *higher* v2 plan. A repeat sale of the same tier **stacks** (starts when the current
  period ends); credits are added at once.
- **Access ends by itself at the period end** via the sweep cron, which hands the plan
  to whatever else still entitles the company (another sale, or a paid subscription)
  instead of revoking it from under them. **The hourly `subscription_sweep.py` cron must be
  installed or nothing ever expires.**
- `revoke` ends a sale now (refund/cancel). **Credits already granted are not taken back**
  — that is a separate, deliberate call.
- The customer's Billing page gets `assisted: {tier, label, period_start, period_end}`
  ("managed directly by PaisaMap until …"), with no invoice or payment details.
- **Not built:** a GST invoice for an assisted sale. Issue it outside (your CA/accounting
  tool) and record its number here — the in-app GST engine only invoices Razorpay payments.
  Credits granted here don't yet roll over/expire on the P3 schedule.

## Deploy order (every step is safe to do early; the flag is what opens it)

1. Push the branch → merge → deploy (code goes live, feature dark, old flows unchanged —
   rehearsed against a DB built by the deployed code).
2. Run `db/apply_subscriptions_tables.py` on the server (additive, idempotent; also creates `assisted_sales`).
3. Razorpay dashboard (the NEW account, ragho@paisamaps.com, live mode) → Webhooks →
   edit the existing `https://paisamaps.com/api/billing/webhook` one → also tick the
   `subscription.*` events (authenticated, activated, charged, pending, halted,
   cancelled, completed, paused, resumed). Same URL and secret — don't regenerate it.
4. Ask Razorpay to enable **Subscriptions + UPI Autopay** on the account (separate from
   live keys).
5. Cron the sweep hourly (`db/subscription_sweep.py`) — it also sends the dunning emails.
6. Flip `BILLING_SCOPE=wallet`, then `BILLING_SUBSCRIPTIONS=1` (test keys first).

## Decisions to challenge

- **U27 — settled (2026-09-23/26): prices are "+ GST".** A cycle charges the list price
  plus 18% when `PAISAMAP_GSTIN` is set (Growth ₹12,000 → ₹14,160, Starter ₹5,000 →
  ₹5,900); the UPI Autopay cap is judged on the price *with* GST. The split (SAC 998439,
  CGST+SGST in Karnataka / IGST elsewhere) and the buyer's GST details are frozen on
  `subscriptions.meta` at start and copied onto every charge's order, so each invoice
  goes through the same GST engine as one-off orders. Razorpay plans are cached by
  amount, so switching GST on creates a new plan rather than reusing the ex-GST one.
- **`SUBSCRIPTION_TOTAL_COUNT = 60`** monthly cycles (5 years) is a placeholder; a UPI
  mandate has its own expiry — confirm with Razorpay.
- **One live subscription per paying company**; changing tier = cancel and re-subscribe
  for now (no in-place upgrade/downgrade/proration yet).
- **`DUNNING_DAYS = 3` is measured from OUR first sight of the failure**, not from
  Razorpay's retry schedule (which I haven't verified). If Razorpay keeps retrying past
  day 3 and succeeds on day 4, the charge unlocks instantly.

## Not in this round (Gantt order)

P3 monthly credit grant + rollover (hooks on `apply_charge`), P4 trial, P6 paywall,
P5 seats, P7 signal tiers, Billing UI v2 (no frontend yet), a
reconcile job for a missed `charged` webhook, gap-free invoice numbering under
concurrent duplicate deliveries.

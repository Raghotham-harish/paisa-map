# Billing-v2 P2 + P13 — recurring subscriptions, dunning and soft-lock

Status: **built and tested, dark.** Nothing here is reachable until
`BILLING_SUBSCRIPTIONS=1`. Branch `feat/billing-p2-subscriptions`; not pushed.
Round 1 = subscriptions (P2). Round 2 = dunning + soft-lock (P13), below.

## What exists

| Piece | Where |
|---|---|
| Tables `subscriptions`, `razorpay_plans` | `paisamap-etl/etl/_auth_db.py` (+ `db/apply_subscriptions_tables.py` for prod) |
| State machine, idempotent charge handling, plan grant/revoke, sweep | `paisamap-etl/etl/_subscriptions.py` |
| Self-serve eligibility + per-cycle amount | `paisamap-etl/etl/_pricing.py` (`subscription_error`, `subscription_amount_paise`) |
| Routes + `subscription.*` webhook | `blueprints/billing.py` (`/api/billing/subscriptions[...]`) |
| Cron backstop: revokes ended plans, sends dunning emails | `paisamap-etl/db/subscription_sweep.py` |
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

## Deploy order (every step is safe to do early; the flag is what opens it)

1. Push the branch → merge → deploy (code goes live, feature dark, old flows unchanged —
   rehearsed against a DB built by the deployed code).
2. Run `db/apply_subscriptions_tables.py` on the server (additive, idempotent).
3. Razorpay dashboard → the existing webhook → tick the `subscription.*` events
   (authenticated, activated, charged, pending, halted, cancelled, completed, paused,
   resumed). Same URL and secret; live mode has its own webhook and secret.
4. Ask Razorpay to enable **Subscriptions + UPI Autopay** on the account (separate from
   live keys).
5. Cron the sweep hourly (`db/subscription_sweep.py`) — it also sends the dunning emails.
6. Flip `BILLING_SCOPE=wallet`, then `BILLING_SUBSCRIPTIONS=1` (test keys first).

## Decisions to challenge

- **U27 — GST treatment.** A cycle charges the *list price* and GST is carved out of it
  on the invoice (how one-off orders already work). If list prices should be ex-GST with
  18% added on top, change `_pricing.subscription_amount_paise` (Growth would charge
  ₹14,160, still under the ₹15,000 cap). Also unresolved: whether we're GST-registered at all.
- **`SUBSCRIPTION_TOTAL_COUNT = 60`** monthly cycles (5 years) is a placeholder; a UPI
  mandate has its own expiry — confirm with Razorpay.
- **One live subscription per paying company**; changing tier = cancel and re-subscribe
  for now (no in-place upgrade/downgrade/proration yet).
- **`DUNNING_DAYS = 3` is measured from OUR first sight of the failure**, not from
  Razorpay's retry schedule (which I haven't verified). If Razorpay keeps retrying past
  day 3 and succeeds on day 4, the charge unlocks instantly.

## Not in this round (Gantt order)

P3 monthly credit grant + rollover (hooks on `apply_charge`), P4 trial, P6 paywall,
P5 seats, P7 signal tiers, Billing UI v2 (no frontend yet), the
GST engine (CGST+SGST vs IGST — invoices still use the flat inclusive split), a
reconcile job for a missed `charged` webhook, gap-free invoice numbering under
concurrent duplicate deliveries.

# PaisaMap pricing & credits model

Owner: Raghotham · Drafted 2026-09-11 · Rev 6 (all decisions locked) · Status: **model agreed — not built**

The **3-lever model** (plan tier · credits · seats) plus a standalone
**signal-only** ladder (₹200 / ₹500). Shape and numbers are both settled now;
they still need validating against a live Razorpay account.

Current code: `paisamap-etl/etl/_pricing.py` holds every number (all
"placeholder"). Plans are **one-time `users.plan` flips**, not subscriptions.
Trials, monthly credit grants, quotas, annual billing, multi-currency are **not
built**. Depends on the Company layer — roadmap Phase C
(`docs/DASHBOARD_AUDIT_AND_ROADMAP.md`).

---

## 1. Decisions locked

| # | Decision | Set |
|---|---|---|
| 1 | **3-lever model** — plan tier, credits, seats. No hard project/keyword quotas; companies stay a hard limit. | 09-11 |
| 2 | **Both** a card-required trial **and** a free account — but the free account is **map + 3 core signals + save-locations only; the dashboard is paid/trial-only**. | 09-11 |
| 3 | Tier credits: **1,000 / 3,000 / 7,000 / 16,000 / 40,000+**. | 09-11 |
| 4 | Annual = **−20% price + 5% credits every month**. | 09-11 |
| 5 | New-keyword research = **12 credits** (subsidised; we keep the data). | 09-11 |
| 6 | **Pro signals are NOT free with login.** Anonymous + free account see the **3 core** signals only. Pro signals are a paid upgrade — see the signal-only ladder in §2. | 09-11 |
| 7 | **Signal-only tiers:** **₹200/mo** = 3 core + a ~10-signal pro subset; **₹500/mo** = all 20 pro signals (replaces ₹200, not additive). Neither includes the dashboard or credits. | 09-11 |
| 8 | Free accounts stay — anyone can sign in free (3-signal map + save locations); everything above is a paid upgrade. | 09-11 |
| 9 | Trial = **7 days**. Extra-company fee scales with tier — **₹2,500 / ₹2,500 / ₹3,000 / ₹5,000/mo** (Starter/Growth/Scale/Pro) — not a flat ₹6,000; see §3 note. | 09-11 |
| 10 | **Team viewers are free** and don't count against the seat limit (read-only dashboard, can't spend credits). | 09-11 |
| 11 | Non-INR pricing uses a **real-time FX rate** at checkout, not a fixed peg. | 09-11 |
| 12 | **Scale/Pro/Enterprise (and any annual commitment) get a sales-assisted invoice/PO path** — NEFT/RTGS against an invoice, not card-only checkout. Starter/Growth stay self-serve, defaulting to **UPI Autopay**. See §7. | 09-11 |
| 13 | Billing is **per-account anniversary** (day-N renewal), not calendar-month. Mid-cycle top-ups **grant instantly, bill on the next renewal** — no separate checkout per purchase. No per-cycle overage cap for now. See §8. | 09-11 |
| 14 | Failed renewal → **3-day dunning** (retry + email) → **soft-lock**: a blurred dashboard with data intact, not a hard account lockout — consistent with the existing trial-fail behavior (#2). New top-ups blocked while an unpaid balance exists. See §8. | 09-11 |

---

## 2. Access ladders — who gets what

**Two paid ladders.** The *signal ladder* (₹200 / ₹500) is for people who want
the data on the map but aren't running expansion projects — analysts,
consultants, lenders, researchers. The *dashboard ladder* (₹5k+) is the full
product and includes all signals. The signal ladder is a stepping stone to it.

| | **Anonymous** | **Free** (login) | **Signals Lite** ₹200/mo | **Signals Pro** ₹500/mo | **Trial** | **Dashboard** ₹5k+ |
|---|---|---|---|---|---|---|
| Sign-in | no | yes, free | yes | yes | yes + card | yes + subscription |
| Open map + score any pincode | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Signals on the map | **3 core** | **3 core** | 3 core + **~10 pro** | **all 20 pro** | all | all |
| Save locations / shortlist | — | ✓ | ✓ | ✓ | ✓ | ✓ |
| **Dashboard** — projects, forecast, expansion, reports, store data, compare | — | — | — | — | ✓ | ✓ |
| Credits | — | — | — | — | 500 (7 days) | plan bucket / month |
| Companies · seats | — | — | — | — | 1 · 2 | 1–∞ · 3–25 by tier |
| API / bulk export | — | — | — | — | — | Scale+ |

Notes:
- **3 core signals** = PPI, avg household income, avg household spend
  (`ppi_ml`, `est_monthly_income_hh`, `est_monthly_spend_hh`).
- **The ~10-signal "Lite" subset** (proposal — adjust freely): `bank_branches_per_lakh`,
  `upi_txn_value_per_capita`, `deposits_per_capita`, `msme_per_lakh`,
  `nsdp_per_capita`, `premium_poi_per_km2`, `radiance_mean`, `cars_per_1000`,
  `car_2w_ratio`, `luxury_share` — the banking / spend-power / commercial-density /
  affluence signals a site scout actually reads. **Signals Pro** adds the
  remaining 10 (branch-type splits, filers, factories, cropping intensity,
  schools, LMV, EV share, etc.).
- A free account is a **permanent** state — the top-of-funnel for nurture and the
  save-locations retention hook.
- The trial auto-converts to **Starter** on day 8 unless cancelled; a failed card
  drops the account to **Free** (never locked out — they keep their saved locations).
- Signals Lite/Pro are **month-to-month, no annual, no credits.** Upgrading to a
  Dashboard plan supersedes them (all 20 signals included).
- Dropped: the "pick any 10 pro signals for 60 credits" credit-sink idea — the
  ₹200/₹500 subscriptions replace it.

---

## 3. Lever 1 — plan tiers

Monthly billing. **Annual = −20% on price, +5% credits/month**, credits still
granted monthly (no front-loading a year).

| | **Trial** | **Starter** | **Growth** | **Scale** | **Pro** | **Enterprise** |
|---|---|---|---|---|---|---|
| Price / month | ₹0 · 7d | **₹5,000** | **₹12,000** | **₹25,000** | **₹50,000** | **₹1,00,000+** |
| Annual /mo equiv. | — | ₹4,000 | ₹9,600 | ₹20,000 | ₹40,000 | custom |
| **Credits / month** | 500 total | **1,000** | **3,000** | **7,000** | **16,000** | **40,000+** |
| Implied ₹/credit | — | 5.00 | 4.00 | 3.57 | 3.13 | ≤2.50 |
| Companies | 1 | 1 | 1 | **3** | **10** | unlimited |
| Extra company /mo | — | ₹2,500 | ₹2,500 | ₹3,000 | ₹5,000 | negotiated |
| **Seats included** | 2 | **3** | 6 | 12 | 25 | custom |
| Projects | 1 | soft ~5 active | soft ~15 | soft ~40 | soft ~100 | unlimited |
| Signals (map + models) | all 20 | all 20 | all 20 | all 20 | all 20 | all 20 |
| Keywords / project (in-catalog) | 5 | 15 | 15 | 30 | 50 | custom |
| Export / API | — | — | rate-limited | ✓ | ✓ | ✓ + SLA |
| Self-intelligence jobs (Phase G) | — | — | monthly | weekly | daily | daily |
| Support | — | email | email | priority | priority + call | dedicated + SLA |

**Below the dashboard ladder** (§2): a free account (3 core signals + save
locations), **Signals Lite ₹200/mo** (3 core + ~10 pro), **Signals Pro ₹500/mo**
(all 20 pro). None include the dashboard, credits, seats or companies.

- **Bigger tiers = more credits per rupee** (5.00 → 2.50). Upgrading always beats
  buying top-ups past a threshold — that is the expansion path.
- **Extra-company pricing must always undercut a fresh account.** At the old flat
  ₹6,000/mo, a Starter customer with 2 companies paid ₹11,000 (plan + extra
  company) for one shared setup versus ₹10,000 for two separate Starter
  accounts with their own credit pools — the fee was *more* than the plan it
  was attached to, so the rational move was to just sign up twice. Rev 4 fixes
  this: ₹2,500 / ₹2,500 / ₹3,000 / ₹5,000 across Starter→Pro is 50% → 21% → 12%
  → 10% of that tier's base price — monotonically cheaper as a share of spend
  the bigger the account gets (rewards staying consolidated), while staying
  comfortably below every tier's own base price (never rational to fragment
  into separate accounts instead).
- "Projects — soft ~N" means: create freely; the number is a fair-use guideline,
  not a hard block. Enforced only if an account is wildly beyond typical use
  (then: a conversation, or 100 credits per extra project).
- Margin check: at Starter, ₹5,000 − ~2.36% Razorpay − ~₹400 amortised
  data/infra ≈ **₹4,480 contribution (~90%)** before support. Blended target
  across tiers **≥ 82%** holds comfortably.

---

## 4. Lever 2 — credits

**1 credit ≈ ₹4 blended** (₹5.00 at Starter, ₹2.50 at Enterprise). One currency
for everything metered.

| Action | Credits | Basis |
|---|---|---|
| Explore map, score pincode, view intelligence, save locations | **0 — unlimited** | near-zero COGS; the adoption hook |
| **Forecast run** | **8** | unchanged from `_pricing.py` |
| **Expansion recommendation** | **5** | unchanged |
| **Report (PDF)** | **10** | unchanged |
| **Research a new keyword** (not in our data) | **12** | ₹8–40 cost, subsidised; we keep the data + it's queued for backfill |
| Add an in-catalog keyword within the project's default | **0** | |
| Extra project beyond fair use | **100** | rare; mostly a soft nudge |
| Self-intelligence autorun (Phase G, opt-in) | **15 / run** | future |

Rounding: metered add-ons round **up** to the whole credit after any ×1.35 markup.

**Overage — top-up packs** (priced 10–20% over the plan's implied rate, so
upgrading is the cheaper path past a threshold):

| Pack | Price | ₹/credit | Rolls over |
|---|---|---|---|
| 500 | ₹3,000 | 6.00 | 60 days |
| 2,000 | ₹10,000 | 5.00 | 60 days |
| 5,000 | ₹22,000 | 4.40 | 60 days |

Plan credits roll **one month** then expire; top-up credits roll **60 days**.
Two 5,000-packs in a month → a **suggestion banner** offering to move up a tier
(one click to accept — never automatic; the plan never changes without the
customer confirming).

---

## 5. Lever 3 — seats

Flat fee per user over the included count. No credit cost, no per-seat metering
of usage (usage is already metered by credits, which are pooled at the company).

| Tier | Included | Extra seat / month |
|---|---|---|
| Starter | 3 | ₹900 |
| Growth | 6 | ₹900 |
| Scale | 12 | ₹800 |
| Pro | 25 | ₹700 |
| Enterprise | custom | negotiated |

Roles (owner / admin / editor / viewer) come from roadmap Phase D. **Viewers are
free** — they don't count against the seat limit (read-only, can't spend credits).

---

## 6. New-keyword pipeline

When a customer adds a keyword we don't have data for:
1. Charge **12 credits**.
2. Queue the keyword for backfill (an internal list; research via an SEO data
   API or manually).
3. Once backfilled, the keyword's demand data appears for **every** customer —
   the 12 credits partly funded a shared asset, which is why it's subsidised
   below the ~₹40–150 true research cost.
4. If backfill fails / the keyword has no meaningful volume, **refund the 12
   credits** and mark it unavailable.

Keyword feature itself is unbuilt — roadmap Phase F/G.

---

## 7. Currency, GST & Indian payment rails

- **INR** reference. **GST 18%** on INR invoices — confirmed current for SaaS/
  software services regardless of amount, frequency, or customer size. The
  specific SAC code still needs a CA — sources disagree even on the broad
  classification (9983 "other professional/technical/business services" per
  one; 998314 specifically for "SaaS platforms" per another, with 998315/998316
  for hosting/maintenance) — this is exactly the kind of judgment call that
  needs a real CA looking at our actual service characterization, not a guess.
- **Domestic split matters for invoice line items, not the total**: same-state
  customer → CGST + SGST (9%+9%); different-state → IGST 18%. The invoice
  generator needs the customer's registered state to pick the right split, not
  just stamp a flat "GST 18%" line.
- **Razorpay does not auto-calculate or apply GST on subscription billing.**
  Checked directly — their own platform *fee* to us carries 18% GST (on the fee,
  not the transaction), and their separate Invoices product lets you *manually*
  add a GST line with a calculator, but neither is automatic tax logic wired
  into a Subscriptions billing cycle, and their auto-GST-payment feature (for
  paying our *own* GST liability) has been discontinued. **GST computation
  (CGST/SGST vs IGST, zero-rating for verified export) has to be built into our
  own invoice generation** (`_pricing.py` / billing-v2, not assumed free from
  the payment processor).
- Non-INR customers are charged in their own currency, converted at a
  **real-time FX rate** fetched at checkout (a rate provider — or Razorpay
  International's own conversion — cached ~60 min). No fixed peg. Indicative USD
  today: Starter ≈ $60, Growth ≈ $144, Scale ≈ $300, Pro ≈ $600; Signals Lite
  ≈ $2.4, Pro ≈ $6.
- Small FX-drift risk between the price a visitor sees and the charge — acceptable
  for monthly billing; re-quote on the checkout screen so what they confirm is
  what they pay.
- International invoices = **export of service, zero-rated** — confirm with a CA.

**Payment methods — recurring billing in India:**

- **UPI Autopay is the default self-serve method for Starter/Growth.** Under
  the RBI's 2026 e-mandate framework, recurring UPI (and card) payments up to
  **₹15,000 per cycle** can auto-debit silently after a one-time mandate setup
  — no OTP every month. Starter (₹5,000) and Growth (₹12,000) both fit
  comfortably under that cap, so UPI Autopay gives Indian SMB buyers a
  save-a-card-free "set and forget" option — genuinely smoother than requiring
  a corporate card, which many smaller buyers here don't have.
- **Scale/Pro/Enterprise (₹25,000–₹1,00,000+) and any annual charge exceed
  ₹15,000/cycle — silent autopay isn't available at all above that on either
  UPI or cards** (the 2026 framework applies the same ₹15k general cap and
  ₹1L enhanced cap to both rails, and SaaS subscriptions aren't in the
  enhanced-cap category — that's reserved for insurance/SIPs/credit-card
  bills). So above ₹15,000/cycle, the choices are: re-authenticate (OTP) every
  billing cycle, or the sales-assisted invoice/NEFT path (decision #12) — this
  isn't just a UX preference for those tiers, it's a real payment-rail
  constraint, which is exactly why Scale+ needs "talk to sales" rather than
  pure self-serve.

---

## 8. Billing cycle, deferred top-ups & failed-payment lock

**Billing cycle is per-account anniversary, not calendar-month.** Trial
converts (or a fresh subscription starts) on day N of some month → renewal
auto-charges on day N of every month after. No proration math for whatever
day someone happens to sign up — Razorpay Subscriptions does this natively.

**Mid-cycle credit top-ups are deferred, not charged as a separate
transaction.** Buying credits mid-cycle shouldn't mean a checkout redirect
every time — that's the exact friction usage-based billing (AWS, Twilio) is
built to avoid. Split *granting* from *collecting*:
- Clicking "add N credits" grants them to the account **instantly** — no
  checkout page, no separate payment event, no interruption.
- It's recorded as an accrued charge against the account.
- The **next scheduled renewal** fires one combined auto-debit: base plan fee
  + everything accrued since the last renewal, via the payment method already
  on file (UPI Autopay mandate or card).
- Once granted, top-up credits behave exactly as before (60-day rollover) —
  deferring *when money changes hands* doesn't change the credit ledger.
- **No cap on accrued overage per cycle for now** — simpler, and unlikely to
  bite often at Starter/Growth spend levels. The one edge case: if base +
  accrued overage crosses ₹15,000 in a heavy month, that renewal exceeds the
  silent-autopay ceiling (§7) and needs an OTP that cycle, or falls back to
  the sales-assisted path. Revisit adding a per-cycle cap only if this turns
  out to be common in practice, not pre-emptively.

**Failed renewal → 3-day dunning → soft-lock, not hard lockout.** Consistent
with the trial-failure behavior already decided (§1 #2: a failed card drops
the account to Free, never fully locked out) rather than a harsher rule just
because it's a renewal:
- Day 0 (renewal date): auto-charge attempt. If it fails, retry over **3
  days**, with an email notification on each attempt.
- Still unpaid after day 3 → **soft-lock**: a blur overlay over dashboard
  content (numbers, charts, tables) with a clear, unblurred "Your account is
  locked — update payment to restore access" CTA. Page chrome/nav stays
  visible so it's obvious their account and data still exist, nothing was
  wiped — only Billing/account-settings stay fully interactive, since they
  need an unobstructed path to actually fix it.
- **The blur must be paired with a real backend gate — it's cosmetic on its
  own and trivially bypassed** (open dev tools' network tab, or call
  `/api/export` / a report's PDF URL directly, and you'd get full data despite
  the blurred UI). Every data-serving endpoint — PDF/CSV downloads,
  `/api/export`, forecast/report generation, the map's signal API — has to
  independently check subscription status server-side and refuse (402/403)
  during lock, not rely on the frontend simply not rendering it.
- Saved data (projects, saved locations, past reports, uploaded store data) is
  **never deleted** while locked — paying reactivates instantly, nothing lost.
- **New top-up purchases are blocked while there's an unpaid balance from a
  previous failed cycle** — even the instant-grant kind — otherwise a broken
  payment method lets an account accrue free credits indefinitely, since
  nothing actually gets collected until the next successful charge.

---

## 9. What has to be built (sequenced)

| # | Needs | Depends on |
|---|---|---|
| P1 | **Company layer** — plan + credits + seats attach to a company | Roadmap **Phase C** |
| P2 | **Recurring subscriptions** — Razorpay Subscriptions or a monthly re-charge cron, replacing the one-time `users.plan` flip | P1 |
| P3 | **Monthly credit grant + rollover job** — grant on renewal, expire last month's plan credits, keep top-ups 60 days, +5% on annual | P2 |
| P4 | **Free account + Trial** — free login = 3 core signals + save locations, no dashboard; `trial_ends_at`, day-8 auto-convert to Starter, card-fail → drop to Free, day-7 nudge | P2 + Roadmap Phase C |
| P5 | **Seat enforcement** — count active non-viewer members vs the tier limit | P1, Roadmap **Phase D** |
| P6 | **Dashboard paywall** — gate every `/workspace/*` route except the map on an active dashboard subscription | P4 |
| P7 | **Signal tiers** — the ₹200 / ₹500 monthly SKUs; per-plan signal allow-list (3 core / +10 / all 20); enforce it in the map (`signalPalette`/`SIGNAL_DEFS` gating) and in `columns_for_plan` for `/api/export` and the models | P2 |
| P8 | **Keyword feature + backfill queue** — the model, "research a new one for 12 credits", the internal queue, the refund path | Roadmap Phase F/G |
| P9 | **Multi-currency** — real-time FX at checkout, per-currency invoicing, export-of-service handling | P2 |
| P10 | **Annual billing** — the −20% SKU, +5% monthly grant on an annual term | P2, P3 |
| P11 | `_pricing.py` — every number placeholder → decided; add `PLANS`, `SIGNAL_TIERS` (₹200/₹500 + allow-lists), keyword cost, top-up packs | all |
| P12 | **India payment rails** — UPI Autopay as the default Starter/Growth recurring method (≤₹15,000/cycle, silent after one-time mandate); a sales-assisted invoice/NEFT path for Scale/Pro/Enterprise + annual (can't silently autopay above ₹15k/cycle under the 2026 RBI e-mandate framework — not just a UX choice, a real rail constraint); our own GST computation (CGST+SGST vs IGST by customer state, zero-rating for verified export) since **Razorpay does not auto-apply GST on subscription billing** | P2 |
| P13 | **Deferred top-up billing + dunning/soft-lock** (§8) — a running "accrued charge" ledger per account so mid-cycle top-ups grant instantly and bill on the next renewal instead of a separate checkout; the 3-day retry-then-lock flow; a server-side subscription-status check on every data-serving endpoint (`/api/export`, PDF/report generation, the map's signal API) so the frontend blur overlay isn't the only thing standing between a lapsed account and real data; the "block new top-ups while an unpaid balance exists" guard | P2, P3 |

**P1–P4 + P6 ≈ one focused billing-v2 milestone** (the dashboard paywall + trial
+ monthly credits). P7 (signal tiers) is a small standalone add. P5, P8–P13 layer on.

---

## 10. Still open

Only one shape question left, plus the fine print:

1. **Which ~10 pro signals go in the ₹200 "Lite" tier?** §2 has a proposal —
   confirm or swap.
2. Top-up pack prices (₹3,000 / ₹10,000 / ₹22,000) — validate once real usage
   data exists.
3. GST SAC code — narrowed to the 9983-family (998314 SaaS-platform / 998315
   hosting / 998316 maintenance are the commonly-cited splits, sources vary) —
   and the export-of-service zero-rating; both need a real CA, not a guess.
4. Self-intelligence credit cost (15/run) — set properly once Phase G scopes the
   real per-run compute/LLM cost.

---

## Session log

| Date | Change |
|---|---|
| 2026-09-11 | First draft from the high-level intent (full 8-dimension model). |
| 2026-09-11 | Rev 2 — collapsed to the 3-lever model; access ladder; dropped the pro-signal credit-unlock. |
| 2026-09-11 | Rev 3 — all decisions locked. Pro signals are **paid, not free with login**: added the standalone **₹200 / ₹500 signal-only ladder**; free account = 3 core signals + save-locations; real-time FX (no peg); viewers free; trial 7 days; extra-company flat ₹6,000. |
| 2026-09-11 | Rev 4 — caught and fixed a real pricing bug: flat ₹6,000/mo extra-company fee exceeded the ₹5,000 Starter base price, so a 2-company customer paid *more* to stay in one account than to just open a second one. Replaced with a per-tier schedule (₹2,500 / ₹2,500 / ₹3,000 / ₹5,000, Starter→Pro) that's always below that tier's base price and shrinks as a % of spend the bigger the account (50%→10%) — see §3 note. |
| 2026-09-11 | Rev 5 — decision #12: Scale/Pro/Enterprise + annual get a sales-assisted invoice/NEFT path, not card-only checkout. Web-researched and rewrote §7: confirmed Razorpay does **not** auto-apply GST on subscription billing (has to be built into our own invoicing, CGST/SGST vs IGST by customer state); confirmed the 2026 RBI e-mandate framework caps silent UPI/card autopay at ₹15,000/cycle (no SaaS enhanced-cap exemption) — UPI Autopay set as the default for Starter/Growth (both fit under the cap), which is also why Scale+/annual structurally need decision #12, not just as a preference. Clarified the top-up→tier-upgrade nudge is a suggestion banner, never an automatic plan change. |
| 2026-09-11 | Rev 6 — new §8 (billing cycle, deferred top-ups, failed-payment lock), decisions #13–14: per-account anniversary billing; mid-cycle top-ups grant instantly and bill on the next renewal instead of a separate checkout (no per-cycle cap for now); failed renewal → 3-day dunning → soft-lock as a blurred dashboard (data intact, consistent with the existing trial-fail philosophy) rather than a hard lockout, paired with a real backend subscription check on every data-serving endpoint so the blur isn't just cosmetic; new top-ups blocked while an unpaid balance exists. Added as build item P13. |

# PaisaMap pricing & credits model

Owner: Raghotham · Drafted 2026-09-11 · Rev 3 (all decisions locked) · Status: **model agreed — not built**

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
| 9 | Trial = **7 days**. Extra-company fee = **flat ₹6,000/mo** (₹5,000 at Pro). | 09-11 |
| 10 | **Team viewers are free** and don't count against the seat limit (read-only dashboard, can't spend credits). | 09-11 |
| 11 | Non-INR pricing uses a **real-time FX rate** at checkout, not a fixed peg. | 09-11 |

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
| Extra company /mo | — | ₹6,000 | ₹6,000 | ₹6,000 | ₹5,000 | negotiated |
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
Two 5,000-packs in a month → a one-click "move up a tier" prompt.

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

## 7. Currency

- **INR** reference. **GST 18%** on INR invoices (confirm HSN/SAC with a CA).
- Non-INR customers are charged in their own currency, converted at a
  **real-time FX rate** fetched at checkout (a rate provider — or Razorpay
  International's own conversion — cached ~60 min). No fixed peg. Indicative USD
  today: Starter ≈ $60, Growth ≈ $144, Scale ≈ $300, Pro ≈ $600; Signals Lite
  ≈ $2.4, Pro ≈ $6.
- Small FX-drift risk between the price a visitor sees and the charge — acceptable
  for monthly billing; re-quote on the checkout screen so what they confirm is
  what they pay.
- International invoices = **export of service, zero-rated** — confirm with a CA.

---

## 8. What has to be built (sequenced)

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

**P1–P4 + P6 ≈ one focused billing-v2 milestone** (the dashboard paywall + trial
+ monthly credits). P7 (signal tiers) is a small standalone add. P5, P8–P11 layer on.

---

## 9. Still open

Only one shape question left, plus the fine print:

1. **Which ~10 pro signals go in the ₹200 "Lite" tier?** §2 has a proposal —
   confirm or swap.
2. Top-up pack prices (₹3,000 / ₹10,000 / ₹22,000) — validate once real usage
   data exists.
3. GST HSN/SAC code + the export-of-service zero-rating — confirm with a CA.
4. Self-intelligence credit cost (15/run) — set properly once Phase G scopes the
   real per-run compute/LLM cost.

---

## Session log

| Date | Change |
|---|---|
| 2026-09-11 | First draft from the high-level intent (full 8-dimension model). |
| 2026-09-11 | Rev 2 — collapsed to the 3-lever model; access ladder; dropped the pro-signal credit-unlock. |
| 2026-09-11 | Rev 3 — all decisions locked. Pro signals are **paid, not free with login**: added the standalone **₹200 / ₹500 signal-only ladder**; free account = 3 core signals + save-locations; real-time FX (no peg); viewers free; trial 7 days; extra-company flat ₹6,000. |

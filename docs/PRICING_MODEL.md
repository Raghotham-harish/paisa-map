# PaisaMap pricing & credits model

Owner: Raghotham · Drafted 2026-09-11 · Rev 2 (decisions locked) · Status: **model agreed — not built**

The **3-lever model** (plan tier · credits · seats) with the six open decisions
resolved. A proposal for the *numbers* still, but the *shape* is settled.

Current code: `paisamap-etl/etl/_pricing.py` holds every number (all
"placeholder"). Plans are **one-time `users.plan` flips**, not subscriptions.
Trials, monthly credit grants, quotas, annual billing, multi-currency are **not
built**. Depends on the Company layer — roadmap Phase C
(`docs/DASHBOARD_AUDIT_AND_ROADMAP.md`).

---

## 1. Decisions locked (2026-09-11)

| # | Decision |
|---|---|
| 1 | **3-lever model** — plan tier, credits, seats. No hard project/keyword quotas; companies stay a hard limit. |
| 2 | **Both** a card-required trial **and** a free tier — but the free tier is **map + signals only; the dashboard is paid/trial-only** (see §2). |
| 3 | Tier credits follow the **sloped curve** (Starter 1,000 → Enterprise 40,000+), not flat. |
| 4 | Annual sweetener = **+5% credits every month**. |
| 5 | New-keyword research = **12 credits** (subsidised below manual cost; we keep the data). |
| 6 | Non-logged-in visitors get **3 signals on the open map**. Logged-in free ("Explorer") gets **all signals** on the map. Neither gets the dashboard. |

---

## 2. Access ladder — who gets what

The paywall is the **dashboard/workspace** (projects, forecast, expansion,
reports, store data, compare, saved locations, connections, API). The map and
its signals are the top-of-funnel lure.

| | **Anonymous** | **Explorer** (free) | **Trial** | **Paid** (Starter+) |
|---|---|---|---|---|
| Sign-in | no | yes | yes + card | yes + subscription |
| Open map + score any pincode | ✓ | ✓ | ✓ | ✓ |
| Signals on the map | **3 core** (PPI, income, spend) | **all** (incl. the 20 pro signals) | all | all |
| Save locations / shortlist | — | ✓ | ✓ | ✓ |
| **Dashboard** — projects, forecast, expansion, reports, store data, compare, connections | — | — | ✓ | ✓ |
| Credits | — | — | 500 (7 days) | plan bucket / month |
| Companies | — | — | 1 | 1–∞ by tier |
| API / bulk export | — | — | — | Scale+ |

Notes:
- Explorer is **permanent and free** — no expiry. It's the "get the flavour"
  state. Conversion happens when someone needs a forecast or a report.
- The trial auto-converts to **Starter** on day 8 unless cancelled; if the card
  fails or is removed, the account drops to **Explorer** (not locked out).
- The old "pick any 10 pro signals for 60 credits" idea is **dropped** — pro
  signals now come free with login. The credit sinks are forecast / expansion /
  reports / new keywords / extra projects.

---

## 3. Lever 1 — plan tiers

Monthly billing. **Annual = −20% on price, +5% credits/month**, credits still
granted monthly (no front-loading a year).

| | **Explorer** | **Trial** | **Starter** | **Growth** | **Scale** | **Pro** | **Enterprise** |
|---|---|---|---|---|---|---|---|
| Price / month | Free | ₹0 · 7d | **₹5,000** | **₹12,000** | **₹25,000** | **₹50,000** | **₹1,00,000+** |
| Annual /mo equiv. | — | — | ₹4,000 | ₹9,600 | ₹20,000 | ₹40,000 | custom |
| **Credits / month** | — | 500 total | **1,000** | **3,000** | **7,000** | **16,000** | **40,000+** |
| Implied ₹/credit | — | — | 5.00 | 4.00 | 3.57 | 3.13 | ≤2.50 |
| Companies | — | 1 | 1 | 1 | **3** | **10** | unlimited |
| Extra company /mo | — | — | ₹6,000 | ₹6,000 | ₹6,000 | ₹5,000 | negotiated |
| **Seats included** | 1 | 2 | **3** | 6 | 12 | 25 | custom |
| Projects | — | 1 | soft ~5 active | soft ~15 | soft ~40 | soft ~100 | unlimited |
| Signals (map) | — | all | all | all | all | all | all |
| Keywords / project (in-catalog) | — | 5 | 15 | 15 | 30 | 50 | custom |
| Export / API | — | — | — | rate-limited | ✓ | ✓ | ✓ + SLA |
| Self-intelligence jobs (Phase G) | — | — | — | monthly | weekly | daily | daily |
| Support | — | — | email | email | priority | priority + call | dedicated + SLA |

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
- Other currencies via **Razorpay International** at a pegged FX (₹83.5/USD),
  quarterly review. Indicative USD: Starter **$60**, Growth **$145**, Scale
  **$299**, Pro **$599**.
- International invoices = **export of service, zero-rated** — confirm with a CA.

---

## 8. What has to be built (sequenced)

| # | Needs | Depends on |
|---|---|---|
| P1 | **Company layer** — plan + credits + seats attach to a company | Roadmap **Phase C** |
| P2 | **Recurring subscriptions** — Razorpay Subscriptions or a monthly re-charge cron, replacing the one-time `users.plan` flip | P1 |
| P3 | **Monthly credit grant + rollover job** — grant on renewal, expire last month's plan credits, keep top-ups 60 days, +5% on annual | P2 |
| P4 | **Explorer + Trial** — logged-in-free gets all map signals but no dashboard routes; `trial_ends_at`, day-8 auto-convert to Starter, card-fail → drop to Explorer, day-7 nudge | P2 + Roadmap Phase C |
| P5 | **Seat enforcement** — count active non-viewer members vs the tier limit | P1, Roadmap **Phase D** |
| P6 | **Dashboard paywall** — gate every `/workspace/*` route except the map on `plan != explorer` | P4 |
| P7 | **Keyword feature + backfill queue** — the model, "research a new one for 12 credits", the internal queue, the refund path | Roadmap Phase F/G |
| P8 | **Multi-currency** — Razorpay International, FX peg config, export invoicing | P2 |
| P9 | **Annual billing** — the −20% SKU, +5% monthly grant on an annual term | P2, P3 |
| P10 | `_pricing.py` — every number placeholder → decided; add `PLANS` (credits, seats, companies), `EXPLORER` tier, keyword cost, top-up packs | all |

**P1–P4 + P6 ≈ one focused billing-v2 milestone** (the paywall + trial + monthly
credits). P5, P7–P10 layer on.

---

## 9. Still to decide (numbers, not shape)

1. Exact credit grants — is **1,000 / 3,000 / 7,000 / 16,000** right, or shift?
2. Extra-company fee — **₹6,000/mo** at Starter–Scale, or scale it with tier?
3. Trial length — **7 days** enough for a buyer to run a real forecast + report,
   or 14?
4. Do **viewers** stay free, or count at a reduced rate (₹200/mo)?
5. USD peg — fixed at ₹83.5, or a small buffer (₹85) so FX moves don't erode margin?

---

## Session log

| Date | Change |
|---|---|
| 2026-09-11 | First draft from the high-level intent (full 8-dimension model). |
| 2026-09-11 | Rev 2 — six decisions locked; collapsed to the 3-lever model; added the access ladder; dropped the pro-signal-unlock SKU (pro signals now free with login). |

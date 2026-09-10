# PaisaMap pricing & credits model

Owner: Raghotham · Drafted 2026-09-11 · Status: **proposal — not decided, not built**

Turns the high-level pricing intent into a coherent model: a credit unit,
per-action costs derived from our real COGS + margin, five plan tiers mapped to
the ₹5k–₹100k price points, add-ons, overage, trial, annual, and multi-currency.
Ends with a simpler alternative and the list of what has to be built first.

Current code: `paisamap-etl/etl/_pricing.py` holds every number today (all
flagged "placeholder"). Plans are **one-time `users.plan` flips**, not
subscriptions. Trials, monthly credit grants, quotas, annual billing and
multi-currency are **not built**. Pricing also depends on the Company layer
(roadmap Phase C) — see `docs/DASHBOARD_AUDIT_AND_ROADMAP.md`.

---

## 1. Objective & principles

- **Land low, expand with usage.** A cheap way in; cost rises as the customer
  gets more value (more forecasts, more locations, more seats, more companies).
- **Credits are the meter.** One currency for everything variable. The plan fee
  buys a monthly credit bucket + structural limits (seats, companies).
- **Margin rule.** Genuinely variable costs (keyword research, geocoding at
  scale, LLM jobs, payment fees) are priced at **our cost × 1.35** (35% markup,
  mid-point of the 25–40% target). The plan fee itself is **value-based** —
  our marginal cost per forecast/report is ~₹0, so cost-plus there would
  under-price a decision worth lakhs. Target **blended gross margin ≥ 82%**.
- **Everything monthly.** Annual = **20% off**, credits still granted monthly so
  usage stays paced.
- **INR is the reference currency.** Other currencies at a pegged FX, quarterly review.

---

## 2. What it actually costs us (COGS estimates)

| Cost | Type | Estimate | Notes |
|---|---|---|---|
| Forecast / expansion / location score / report | marginal | **₹0.05–0.60 each** | pre-computed data + a few CPU-seconds. Effectively zero. |
| Data acquisition + monthly enrichment (YAH/spider, RBI, PhonePe Pulse, govt sets) | fixed | ~₹15–40k/mo of effort + compute | amortised across all customers |
| Infra (Lightsail box, Postgres, storage) | fixed | ~₹2–4k/mo today | scales slowly |
| Payment processing (Razorpay) | variable | **~2.36% of each charge** (2% + 18% GST on the fee) | on every plan fee and top-up |
| Geocoding — free tier (Nominatim, 1 req/s) | marginal | ₹0 | fine to a few hundred stores/day |
| Geocoding — paid, at scale | variable | **~₹0.35–0.45/address** | only bites past the free tier |
| **New keyword research** (keyword not in our data) | variable | **₹8–40 via an SEO data API**, or ₹50–150 manual | we keep the data as an asset |
| Self-intelligence autorun (Phase G, if LLM-backed) | variable | **₹2–15 / customer / run** | opt-in feature, future |
| Support / success | semi-fixed | grows with customer count | |

**Takeaway:** the plan fee is not cost-plus — it funds the fixed data +
infra + dev base and the margin. Cost-plus (×1.35) governs only the metered
add-ons: keyword research, geocoding overage, LLM runs.

---

## 3. The credit unit

**1 credit ≈ ₹4 blended list value** (₹5.00 at the entry tier, sliding to ₹2.50
at the top — a normal volume discount that also makes upgrading the cheaper path
than buying top-ups).

Credits are spent on metered actions (§4). A plan grants a monthly bucket;
unused **plan credits roll over one month max** (not use-it-or-lose-it — that
reads as punitive to a B2B buyer), then expire. **Top-up credits roll 60 days.**

---

## 4. Per-action credit costs

| Action | Credits | Basis |
|---|---|---|
| Explore the map, score any pincode, view intelligence | **0 — unlimited** | near-zero COGS; this is the adoption hook |
| **Forecast run** | **8** | keep current |
| **Expansion recommendation** | **5** | keep current |
| **Report (PDF)** | **10** | keep current |
| **Pro-signal pack unlock** — pick any 10 of the 20 pro signals, 30 days, per company | **60** | "for X credits, open any 10" |
| Add an **in-catalog keyword** to a project | **0** within the project's quota · **1** over quota | |
| **Research a new keyword** (not in our data) | **12** | ₹8–40 cost × 1.35, rounded; we keep the data |
| **Extra seat** beyond the plan's included users | **200 credits/user/mo** *or* ₹900 flat/user/mo | "more users → x credits" |
| **Extra project** beyond the monthly quota | **100 each** | |
| **Extra company** | **not credits — a flat plan-dependent fee**, see §5 | "fixed cost per plan" |
| Self-intelligence autorun (opt-in, future) | **15/run** or bundled into higher tiers | Phase G |

Rounding rule: metered add-ons round **up** to the nearest whole credit after
the ×1.35 markup.

---

## 5. Plan tiers

Monthly billing. Annual = **−20%** on the price, credits granted monthly.
All five paid tiers map to the price caps you set.

| | **Trial** | **Starter** | **Growth** | **Scale** | **Pro** | **Enterprise** |
|---|---|---|---|---|---|---|
| Price / month | ₹0 · 7 days | **₹5,000** | **₹12,000** | **₹25,000** | **₹50,000** | **₹1,00,000+** |
| Annual / month equiv. | — | ₹4,000 | ₹9,600 | ₹20,000 | ₹40,000 | custom |
| Credits / month | **500 total** | **1,000** | **3,000** | **7,000** | **16,000** | **40,000+** |
| Implied ₹/credit | — | 5.00 | 4.00 | 3.57 | 3.13 | ≤2.50 |
| Companies | 1 | 1 | 1 | **3** | **10** | unlimited |
| Extra company | — | — | — | ₹6,000/mo | ₹5,000/mo | negotiated |
| Users included | 2 | **3** | 6 | 12 | 25 | custom |
| Extra seat | — | ₹900/user/mo | ₹900 | ₹800 | ₹700 | negotiated |
| New projects / month | 1 | **3** | 8 | 20 | 50 | custom |
| Keywords / project | 5 | **15** | 15 | 30 | 50 | custom |
| Pro signals | — | 10 (1 unlock incl.) | 10 (1 unlock incl.) | **all 20** | all 20 | all 20 |
| Data export / API access | — | — | rate-limited | yes | yes | yes + SLA |
| Self-intelligence jobs | — | — | — | monthly | weekly | daily |
| Support | — | email | email | priority | priority + call | dedicated + SLA |

Notes:
- **Bigger plans get more credits per rupee** (5.00 → 2.50). Standard SaaS; it
  makes "upgrade" cheaper than "keep buying top-ups", which is the expansion path
  we want.
- Your original credit numbers were 500 / 1,000 / 3,000 / 5,000. Kept the first
  three; bumped ₹25k from 5,000 → 7,000 and added a ₹50k tier at 16,000 so the
  ₹/credit curve slopes the right way at every step. If you'd rather ₹25k = 5,000
  credits, that's ₹5/credit — no volume discount at that step — flag it.
- "3 projects **/month**" is a creation quota, not a cap on how many exist. A
  Starter customer accumulates projects over time; they just can't spin up more
  than 3 in a calendar month without spending 100 credits each.

---

## 6. Overage — when the monthly credits run out

Top-up packs, priced ~10–20% above the plan's implied rate so that past a
threshold, upgrading a tier is always the better deal:

| Pack | Price (INR) | ₹/credit | Rolls over |
|---|---|---|---|
| 500 credits | ₹3,000 | 6.00 | 60 days |
| 2,000 credits | ₹10,000 | 5.00 | 60 days |
| 5,000 credits | ₹22,000 | 4.40 | 60 days |

An account that buys two 5,000-packs in a month is shown a one-click "move to
the next tier and stop buying top-ups" prompt.

---

## 7. Currency

- **INR** is the reference. **GST 18%** on INR invoices (confirm HSN/SAC with a CA).
- Other currencies via **Razorpay International** at a pegged FX (₹83.5/USD as of
  drafting), reviewed quarterly. Indicative USD: Starter **$60**, Growth **$145**,
  Scale **$299**, Pro **$599**.
- International invoices treated as **export of service (zero-rated)** — confirm
  with a CA before relying on it.

---

## 8. Trial & conversion

- **7 days, 500 credits, 1 company / 1 project / 5 keywords**, no pro signals,
  no export.
- **Card required at signup** → auto-converts to **Starter** on day 8 unless
  cancelled. Cleaner B2B funnel, and the day-7 nudge ("your forecast for X is
  ready — keep it") converts well.
- Prospects who won't put a card down get a **"Book a demo"** path (larger deals)
  or a permanently-free **Explorer** view — public map + scoring only, 0 monthly
  credits, no workspace.
- Trial credits do **not** roll over.

---

## 9. Annual

- **−20%** on the sticker price, one invoice, **credits still granted monthly**
  (so an annual customer can't front-load a year of usage in month one).
- One sweetener, pick one: (a) **+5% credits** each month, or (b) **one free
  tier-bump on seats**. Recommend (a) — simpler, and it compounds engagement.

---

## 10. "Is this too cumbersome?" — a simpler alternative (recommended to weigh)

The model above has **8 metered dimensions** (credits, companies, seats,
projects/mo, keywords/project, pro-signal unlocks, new-keyword research,
reports). That's a lot to explain on a pricing page and a lot to meter and
enforce in code.

**Collapse to 3 levers:**

1. **Plan tier** — one row per tier: price, included credits, included seats,
   included companies. (Keep the five price points.)
2. **Credits** — the single currency for *everything* variable: forecasts,
   reports, expansions, pro-signal unlocks, new keywords, extra projects. No
   separate quotas for projects or keywords — they just cost credits past a
   generous soft limit.
3. **Seats** — a flat ₹/user/month over the included count.

Companies stay a hard structural limit (real abuse vector). Everything else
becomes "unlimited within fair use, or costs credits." Same land-and-expand
flywheel, roughly a third of the metering to build, and a pricing page a buyer
understands in 20 seconds.

**Recommendation:** ship the 3-lever version first. Add project/keyword quotas
later only if data shows abuse.

---

## 11. What has to be built (and in what order)

Pricing v2 can't ship before these. Sequenced against the dashboard roadmap:

| # | Needs | Depends on |
|---|---|---|
| P1 | **Company layer** — plans attach to a company, not a user | Roadmap **Phase C** |
| P2 | **Recurring subscriptions** — Razorpay Subscriptions (or a monthly cron that re-charges + re-grants), replacing the one-time `users.plan` flip | P1 |
| P3 | **Monthly credit grant + rollover job** — grant on renewal, expire last month's plan credits, keep top-ups 60 days | P2 |
| P4 | **Trial** — `trial_ends_at`, the day-8 auto-convert, the day-7 nudge, the no-card Explorer fallback | P2 |
| P5 | **Metering & enforcement** — seat count, company count, (project/keyword quotas only if we keep §5 not §10) | P1, Roadmap **Phase D** (roles) |
| P6 | **Keyword feature** — the keyword model itself doesn't exist yet; "add up to N", "research a new one for 12 credits", and the queue that documents new keywords for us to backfill | Roadmap **Phase F/G** |
| P7 | **Multi-currency** — Razorpay International, FX peg config, export-of-service invoicing | P2 |
| P8 | **Annual billing** — the −20% SKU, monthly grant on an annual term | P2, P3 |
| P9 | Move every number in `_pricing.py` from placeholder → decided; add the new keys | all above |

Rough build size: **P1–P4 ≈ one focused milestone**; P5–P9 layer on after.

---

## 12. Open decisions for you

1. **§5 hard quotas** or **§10 three-lever** model? (Recommend §10.)
2. ₹25k tier = **7,000 credits** (my curve) or **5,000** (your number, flat rate)?
3. Trial: **card required** (recommend) or no-card + Explorer fallback, or both?
4. Annual sweetener: **+5% monthly credits** (recommend) or a free seat bump?
5. Is a **subsidised** new-keyword price (12 credits ≈ ₹48, below the manual
   cost) acceptable given we keep the data? Or price it at full manual cost
   (~40 credits)?
6. Does "Explorer" (permanently free, map-only) fit the brand, or is PaisaMap
   trial-then-paid only?

---

## Session log

| Date | Change |
|---|---|
| 2026-09-11 | First draft from the high-level intent. |

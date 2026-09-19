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
| 15 | **One wallet per paying account; companies draw from it.** Credits are bought into the paying account's wallet (an agency's, say) and any company linked under it spends from that same wallet; every spend is logged against the company it was done in, so usage per client is reportable. **No free per-company credits** — minting credits on company creation would be farmable and clashes with the 500-credit trial; if agencies later want to stop one client draining the wallet, add an optional per-company spending *limit* funded from the wallet. A person in several *separate* paying accounts still picks which one at checkout ("Buying for: …", defaulting to the company selected in the switcher); only a company's owner/admin can buy for it. Legacy-account → v2-tier mapping (incl. the owner's own `pro` account) is **deliberately left open** as the last step before go-live. | 09-19 |
| 16 | **Client-company billing design (agreed 2026-09-19; mostly NOT built yet).** *Three relationships:* (1) agency-created client — auto-linked under the agency's wallet; (2) client pays itself — the client's owner adds the agency's people as members, spends draw the client's own wallet, and the client's owner buys; (3) agency pays for an existing client — the agency requests, the client's owner approves, either side can detach at any time. **Never auto-link by URL:** a URL match only *suggests* "this company exists — request to connect?"; real linking needs the owner's approval and a verified website (verification tag in the site's head, or an existing GA4/Search Console connection); an unverified entry stays "verification pending". A traffic-tracking script (GA/Hotjar-style) is a separate product with consent obligations — not now. *Privacy:* a client's own staff see who pays and their own usage, never the payer's wallet balance (DONE). *Budgets (credits, never rupees):* per client company, chosen period (weekly / monthly / quarterly / one-off total / until a date), warn at 80% then **hard stop** (no surprise billing), set by the paying admin; the client's admin may set sub-budgets only inside the parent's cap (v1 may be admin-only); a budget is **mandatory for spenders outside the paying company**, and an optional wallet reserve floor keeps the agency's own work running when clients drain it. *Detach:* immediate; the client's own wallet starts empty. *Who pays:* owners/admins only for now (no separate billing role). *Statements:* what an agency shares with a client is a **usage statement**, not our tax invoice (that is addressed to the payer's GSTIN) — needs the CA review already open in §10. *API keys:* individually attributable and admin-visible/revocable; capacity counted per company; API access stays a plan entitlement, not per-call credits, unless decided otherwise. | 09-19 |
| 17 | **Budgets (built 2026-09-19; wallet scope only).** In credits, one per *company* (the wallet company can cap itself too), set only by an owner/admin of the company that pays. Period **type** is stored, never dates: billing cycle (**the default** — it follows the paying company's renewal date, and until subscriptions exist falls back to the IST calendar month), calendar month, weekly (Monday), quarterly, one-off total, until a date. Windows are IST; **no rollover**. Warn at 80% (banner on Billing + Dashboard), **hard stop at 100%** — spending exactly to the cap is allowed, one credit past is refused, and the check runs in the same transaction as the wallet balance check. Once set it caps **everyone** spending on that company (agency staff and the owner too — raise it to go on); it is *mandatory* only for spenders outside the paying company (no budget = they can't spend). An optional **reserve** floor keeps credits back for the paying company: spends attributed to any *other* company can't take the wallet below it; the payer's own spending isn't limited by it. A cap belongs to whoever set it as payer: it is ignored if that company no longer pays, and dropped when the company is relinked. A lapsed until-a-date budget behaves as no budget. **Round 2 (same day): personal allowances** — an owner/admin of the company itself (a client's own admin) or of the wallet that pays can give a member a per-person allowance inside the company (`credit_member_budgets`); never above the company's budget, a client admin needs an active company budget to work inside, the payer's admins needn't, and the *lower* of allowance and company cap binds; allowances belong to the company so they survive a relink. **80% / 100% alert emails** to the owners/admins of both companies, at most once per level per window (reset when the budget is raised or a new period starts), sent after the spend commits and never able to fail it; they carry only the company's own figures, never the wallet balance; they need SES configured (same as invite emails). Not yet: alerts for personal allowances. | 09-19 |

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
| 2026-09-18 | **Build started (billing-v2 step 1 + stage 2a) — working tree only, not deployed.** Checked the P1 prerequisite in code first: Phase C shipped the company *container* but **not** the plan/credits cutover — `spend_credits`/`get_credit_balance` still key on `user_id`, plan is enforced from `users.plan` (CHECK `free/pro/team`), and `organizations.plan` / `credits_ledger.org_id` were staged columns nothing read. So P1 is real, unbuilt, money-touching work. **Step 1 (P11, additive):** `_pricing.py` now carries the decided v2 price book (`TIERS`, `SIGNAL_TIERS`, `TOPUP_PACKS`, trial/annual/dunning constants, `PRICE_BOOK_VERSION`) beside the untouched legacy placeholders that live checkout still reads; pinned by tests against this doc's numbers and its design invariants (extra-company fee < base price and shrinking; rupees/credit falling up the ladder; self-serve exactly when a tier fits the ₹15,000 UPI cap). **Found and fixed a design bug before it shipped:** legacy `'pro'` (₹999) and v2 Pro (₹50,000) are the same string and would share `organizations.plan` — v2 ids are stored namespaced (`v2_pro`), `parse_plan()`/`tier_rank()` keep the vocabularies apart, unknown ids fail closed. **Stage 2a (stamping only, no read-behaviour change):** every `credits_ledger` row and every order is stamped with its company (project-scoped spends attribute to the *project's* company); orders also record `price_book_version` so cohorts that saw different prices stay separable (this doc has no elasticity/willingness-to-pay data — no customers yet); `set_user_plan` mirrors to the buyer's *own* company only; new read-only `get_org_credit_balance` / `credit_org_parity()` audit whether a company-level balance would match today's per-user one. Signup now creates the company before granting the bonus so that row is stamped too. **Deploy gate:** nothing runs `migrate_schema` automatically, and the new `orders` columns are in the `Table` definition, so the two nullable columns must exist on prod *before* this code ships (the five nullable columns listed in the 09-19 row below — old code ignores them). After deploy, re-run `backfill_organizations()` to stamp ledger rows written since C3, then `credit_org_parity()`. Next: stage 2b (read cutover) needs the decision on which company a non-project action bills, and how legacy accounts map to v2 tiers. |
| 2026-09-19 | **Wallet model adopted (decision #15) and built into stage 2a — still working tree only.** Companies now carry an optional `billing_org_id` (the paying account; NULL = pays for itself; exactly one level deep, no chains/cycles); every ledger row stores the wallet it belongs to and the company that used it; `set_org_payer()` (owner of the company + owner/admin of the payer; refuses self-links, chains, and moving a payer under another); `resolve_purchase_wallet()` + optional `org_id` ("Buying for") on `/api/billing/orders/credits|plan` (403 for non-owner/admin, 400 for a non-integer/boolean), recorded on the order and on the credit-purchase ledger row; read-only `get_wallet_credit_balance()` and `usage_by_company()` (the per-client usage view an agency re-bills from); `backfill_organizations()` now also stamps wallets on legacy rows. Still recording only: balances/plan enforcement are per-user. 128 billing checks (agency scenario with two employees + two clients, fake payment gateway over HTTP) mutation-tested (payer resolution, owner check, member-can-buy each caught); isolation 70/70 and security 60/60 green. **Prod migration required BEFORE deploy (nothing runs `migrate_schema` automatically; the new columns are in the `Table` definitions so every order/org read would fail without them):** `ALTER TABLE orders ADD COLUMN IF NOT EXISTS org_id INTEGER; ALTER TABLE orders ADD COLUMN IF NOT EXISTS billing_org_id INTEGER; ALTER TABLE orders ADD COLUMN IF NOT EXISTS price_book_version TEXT; ALTER TABLE credits_ledger ADD COLUMN IF NOT EXISTS billing_org_id INTEGER; ALTER TABLE organizations ADD COLUMN IF NOT EXISTS billing_org_id INTEGER;` Then deploy, then `backfill_organizations()` and `credit_org_parity()`. Not built yet: any UI/route to link a company under a payer (DB function only), and the read cutover (stage 2b). |
| 2026-09-19 | **Stage 2b (read cutover) built — behind `BILLING_SCOPE`, default `user` = today's behaviour; working tree only, not deployed.** `BILLING_SCOPE=wallet` makes a balance the WALLET's sum (shared by every member of every company drawing from it), checks/serialises spends on the wallet company's row, shows a user only the ledger rows of companies they belong to (with the wallet's running balance), and resolves a user's plan as the best of their own and their companies' legacy plans (never lowers; v2/junk ids ignored fail-closed; API keys resolve the same way). **Found by re-running the legacy suites under wallet scope, missed by the new tests:** an *extra* company would have started with an empty wallet (today credits follow the user) — so `create_organization` now links an additional company under its creator's wallet by default, `set_org_payer` refuses to link a company that holds its own credits (they'd be stranded), `link_extra_companies_to_primary()` is the one-time migration for existing extras (merges their rows into the owner's wallet), and companies that pay for others or have billing history can't be deleted (409). `wallet_mode_preflight()` / `credit_wallet_parity()` list everything that must be true before flipping. Per-user `balance_after` chains are kept in both modes so rollback is lossless for solo accounts; the per-user display is clamped at 0. **Dress rehearsal:** a database built by the *currently deployed* code (unstamped signup-bonus rows, an extra company, a hand-flipped `pro` owner, a teammate, an order) upgraded through the five-column script, `billing_cutover.py apply` (idempotent), and the new code — no user's balance or plan differs between scopes. 138 + 78 billing checks (+70 isolation, +60 security) green in default scope; 11 mutations across both files all caught (two initial survivors led to tighter tests). Runbook: `docs/BILLING_CUTOVER_RUNBOOK.md`. **Not built:** UI/route to link a company under a payer; header/`credits` screens passing the switcher's company; real concurrent-spend test on Postgres. **Consequence to remember:** in wallet mode a teammate inherits their COMPANY's plan, and the owner's company plan is still `free` while their personal plan is a hand-set `pro` — so that stays for the deferred legacy-mapping step. |
| 2026-09-19 | **Wallet-balance privacy + selected-company credits shipped to the working tree (still not deployed).** New `get_credit_view()`: a user who works in a client company but is not a member of the company that pays for it gets `balance: null` and a `paid_by` name — never the payer's total — on `/api/credits`, `/api/auth/me`, and the 402 bodies of forecast/expansion/report; the ledger rows they may see carry no running wallet balance (`balance_after: null`); members of the paying company still see everything. `/api/auth/me?org_id=` and `/api/credits?org_id=` follow the company selected in the switcher (a company you're not in is ignored, no error, no leak). Frontend: Dashboard credits tile and Billing page follow the selected company and show "paid by X"; a hidden balance renders as "—"; one shared `insufficientCreditsMessage()` words every 402 ("Ask X to top up" when the balance is hidden); the Buy-credits block is replaced by a "paid for by X" note when the company's wallet belongs to someone else (a purchase there would have credited the buyer's own company). Verified in a real browser with a seeded agency/client scenario: agency staff see 835 with running balances; the client's person sees "— · paid by Priya Agency", no balance chips, no buy buttons. 4 privacy mutations all caught. No schema change, so the pending five-column migration is unchanged. |
| 2026-09-19 | **Interim spend gate (wallet scope only), working tree.** Until budgets exist, someone who is *outside* the company that pays for a wallet (a client's own staff on an agency-paid company) cannot spend it at all: `wallet_spend_allowed()` is the single seam budgets will plug into; `spend_credits` raises `SpendNotAllowedError` (defence in depth) and forecast / expansion / report routes return **403 `budget_required`** with a plain-language detail *before* any balance talk. Members of the paying company, and anyone in their own company, are unaffected; the legacy scope has no gate. 16 new checks, 5 mutations caught. **Not covered by a test:** the report route's copy of the gate (a saved location is needed to get past its earlier no-locations check) — it is the same two lines as forecast/expansion. |
| 2026-09-19 | **Budgets built (decision #17) — working tree, not deployed.** New `credit_budgets` table (created by `paisamap-etl/db/apply_budgets_table.py`; runbook step 5b), `budget_window()` (IST calendar, month/week/quarter/anniversary with day-clamping), `_spend_block()` run inside `spend_credits` after the wallet lock, `wallet_spend_block()` for the route pre-checks (the three spending routes now pass the action so the budget is checked against its real cost), `set_credit_budget` / `delete_credit_budget` / `set_wallet_reserve` / `list_wallet_budgets`, `/api/organizations/<wallet>/budgets[/<org>]` + `/reserve`, and a `budget` field on `/api/credits` and `/api/auth/me`. UI: a budget meter for a company's own staff and a Budgets manager for the payer's admins. `_cycle_anchor()` is the single seam to change when subscriptions ship. 144 new checks (windows, cap boundary, reserve boundary, permissions, stale caps, HTTP) — **26 hand-written mutants, all killed** (4 survived the first pass and exposed real test gaps: purchases counted as spend, spend under another wallet, cross-wallet delete, zero-budget %). Verified in a real browser (payer admin edits, client staffer sees 85% then 100% and never the wallet balance). Relinking/deleting a company tolerates a missing table so deploy order doesn't matter. |
| 2026-09-19 | **Budgets round 2 built — working tree, not deployed.** Personal allowances (`credit_member_budgets`; `set/delete/list_member_budget(s)`; `/api/organizations/<org>/member-budgets[/<user>]`; `member_budget` on `/api/credits` + `credits_member_budget` on `/api/auth/me`; UI: `MemberBudgetsManager` for company admins, a personal meter for the person, a shared `BudgetEditor`) and 80% / 100% alert emails (`credit_budgets.notified_window_start/notified_level` dedupe, `_budget_crossing` inside the spend transaction, `_email.send_budget_notice` with HTML-escaped company name, dispatched after commit on a background thread and swallowed on failure). `spend_credits` is now a thin wrapper over `_spend_credits_tx`. `apply_budgets_table.py` now creates both tables and adds missing nullable columns, so it can run before or after this deploy (rehearsed upgrading the round-1 table with a live row). 208 checks; **51 hand-written mutants total, all killed** (25 new: allowance rules, alert dedupe/thresholds/recipients, email escaping and wording). Browser-verified (client admin sets/updates/removes allowances, capped at the company budget; staffer sees their own meter; client without a budget is told to ask the payer). |

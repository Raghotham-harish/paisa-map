# Target customer — decision brief

This is the open question flagged in the growth plan: **who actually buys
pincode-level purchasing-power data, and in what form?** It sits upstream of
pricing (Track 1 · P4) and outreach (P5) — both are guesswork until it's
answered. This brief lays out the candidates and what each one implies. It does
not pick one; that's a call for whoever owns the business.

## What PaisaMap can credibly sell today

- A Purchasing Power Index + ~20 economic signals for ~15,600 pincodes,
  nationwide, refreshed weekly.
- Location scoring, benchmarking, comparison, opportunity/suitability, an
  expansion-portfolio recommendation, and (new) an investment→revenue forecast.
- A self-serve workspace where a business uploads its own store data and gets it
  scored against the signals.

What it can't credibly sell: real-time data, transaction-level data, footfall,
competitor intelligence, or anything requiring a licence not yet cleared (see
`DATA_LICENSING.md`).

## Candidate segments

| Segment | What they'd pay for | Fit | Friction |
|---|---|---|---|
| **Retail / F&B / pharmacy chains — site selection** | "Where do I open store #40?" — the expansion forecast + comparison, joined to their own store P&L | **Strongest.** The whole workspace is already built for this exact job. | Long sales cycles; they may already use a CBRE / Nielsen / an internal GIS team. Need 2–3 named logos as proof. |
| **Lending / NBFC / fintech — risk & TAM** | Pincode-level affordability as a feature in credit models; market-sizing for a new product | High. Pure data-API buyer, less hand-holding, higher willingness to pay. | Regulatory scrutiny of model inputs; they'll want documented provenance and stability (the licensing review matters most here). |
| **FMCG / CPG — distribution & GTM planning** | Which pincodes to prioritise for a premium SKU launch; distributor territory design | Medium-high. Big budgets, but they buy from Nielsen/Kantar/Bizom and expect that depth. | We're thinner on retail-audit and share data than the incumbents. |
| **Commercial real estate — leasing & valuation** | Demand evidence for a catchment; tenant-mix planning | Medium. | Fragmented buyers; each deal is small. |
| **Consultancies / GIS shops — resale** | White-labelled signals feeding their own client work | Medium. Fast to close, but low margin and they become a channel, not a customer. | Commoditises the data; pushes toward the ODbL/licensing questions sooner. |

## The shape-of-product fork

1. **Self-serve SaaS** (what's built): monthly plans, credit top-ups, the
   workspace is the product. Best fit for the retail-chain segment. Scales
   without a sales team but caps deal size.
2. **Data API / feed**: authenticated, metered, tiered — the Track 1 · P2–P4
   work that doesn't exist yet. Best fit for lending/fintech. Higher ACV,
   needs the licensing review done first.
3. **Bespoke / consulting**: we run the analysis, deliver a report. Highest
   price per engagement, doesn't scale, but it's the fastest way to learn what
   any of the above segments actually value.

These aren't mutually exclusive, but the **first paying customer's segment
should decide the sequencing** — build the API next only if the first serious
buyer needs an API.

## Questions to answer before P4/P5 start

1. Which one segment do we pursue for the **first 3 paying customers**? (Not
   "all of them.")
2. SaaS, API, or bespoke as the **wedge**?
3. Do we have warm intros in that segment, or is it cold outreach?
4. What's the **one number** a buyer in that segment would check to decide if
   our data is good enough? (e.g. a lender checks PPI vs. bureau data
   correlation; a retailer checks our score vs. their best/worst store.) Build
   that proof point before selling.
5. Price anchor: what do they pay today for the nearest substitute?

## Recommendation (weak prior, override freely)

Pursue **retail/F&B site-selection, self-serve SaaS + one or two bespoke
engagements** as the wedge — it's what the product already does, the value is
demonstrable against the customer's own P&L, and the bespoke engagements fund
the learning. Treat the data API as Track 1 · P2 *after* a customer in that
segment asks for programmatic access, not before.

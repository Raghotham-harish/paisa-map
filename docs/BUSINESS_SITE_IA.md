# PaisaMaps business site — information architecture & URL plan

Owner decisions (15 Oct 2026):
- The map stays at `paisamaps.com/`. The business/marketing site lives under **`/business/`**.
- Audience order: **Retail & F&B first** → Real estate & hotel chains → Lenders / NBFC / fintech →
  any business (generic) → Agencies, business-strategy firms, consultants.
- Two lenses: **vertical** (by industry → `/business/solutions/…`) and **horizontal** (by job to be
  done → `/business/use-cases/…`). Every industry page links to its use cases and vice versa.
- Real product screenshots, carousels, sliders and bento grids. Layout references: Google Maps
  Platform (hero + media, tabbed sections, tall stat cards, card carousels) and ArcGIS Online
  (product-screenshot hero, 4-up value props, alternating screenshot/text rows with floating UI
  panels, support trio, FAQ accordion, closing CTA band).
- Mobile first.

## Why a separate site

The map is a JavaScript application: search engines see almost no text, and it can't tell a
buyer who it's for, what it costs or why to trust it. The business site is plain, pre-rendered
HTML (one file per URL): fast, readable by search engines and link previews, and built from the
same price book the billing code uses, so prices can't drift.

## URL structure (all lowercase, hyphenated, trailing slash)

| URL | Page | Purpose |
|---|---|---|
| `/business/` | Home | Who it's for, what it does, proof from real screens, pricing teaser, CTA |
| `/business/product/` | Platform overview | One tour of map → signals → workspace → forecast → reports → API |
| `/business/product/map/` | Live map | The map views (PPI choropleth, heatmap, bubbles, bivariate, top-N, suitability) |
| `/business/product/dashboard/` | Workspace | Projects, store-data upload, compare, expansion, reports; post-login journey |
| `/business/product/signals/` | Signals & data | What we capture, from where, how often, coverage, what's modelled vs measured |
| `/business/product/forecast/` | Expansion forecast | Budget → candidate sites → revenue range, honestly labelled |
| `/business/product/api/` | Data API & exports | CSV/XLSX/JSON export, API keys, rate tiers |
| `/business/solutions/` | Solutions hub | Industry cards (vertical) + use-case cards (horizontal) |
| `/business/solutions/retail-fnb/` | Retail & F&B | Lead segment: where to open the next store |
| `/business/solutions/real-estate-hospitality/` | Real estate & hotel chains | Catchment demand for projects, leasing, hotel sites |
| `/business/solutions/lending-fintech/` | Lenders, NBFC, fintech | Pincode affordability for sourcing, limits, market sizing |
| `/business/solutions/any-business/` | Any business | SMEs, D2C, franchises, services — the generic path |
| `/business/solutions/agencies-consultants/` | Agencies & consultants | Strategy firms, agencies, consultants working for many clients |
| `/business/use-cases/site-selection/` | Site selection | Rank candidate pincodes for a new location |
| `/business/use-cases/market-sizing/` | Market sizing | Households × spend for a region, a city, a radius |
| `/business/use-cases/catchment-analysis/` | Catchment analysis | Who lives within X km of a site, what they spend |
| `/business/use-cases/credit-affordability/` | Credit & affordability | Area-level affordability as a lending input |
| `/business/use-cases/territory-planning/` | Territory planning | Sales/distribution territories by spending power |
| `/business/use-cases/portfolio-benchmarking/` | Portfolio benchmarking | Score existing stores/branches against their areas |
| `/business/how-it-works/` | How it works | Pre-login journey (map, free account) and post-login journey (workspace) |
| `/business/why-paisamaps/` | Why PaisaMaps | USPs, benefits, an honest comparison with the alternatives |
| `/business/pricing/` | Pricing | From the price book: Free, Signals Lite/Pro, Trial, Starter → Enterprise, credits, seats |
| `/business/faq/` | FAQ | Product, data, pricing, security, account |
| `/business/support/` | Help & support | How to get help, response times, guides, status |
| `/business/contact/` | Talk to us | Book a demo / sales / partnerships |
| `/privacy`, `/terms`, `/refund`, `/contact` | Legal & company | **Kept at their current URLs** (Razorpay's site review lists them); footer-linked |
| `/business/sitemap.xml` | Sitemap | All business URLs; referenced from `/robots.txt` |

## Navigation

- **Header:** Product ▾ · Solutions ▾ (Industries | Use cases) · Pricing · Resources ▾ (How it works,
  Why PaisaMaps, FAQ, Support) · **Open the map** (secondary) · **Talk to us** (primary).
  On mobile: a full-screen menu with the same groups as accordions; CTAs pinned at the bottom.
- **Footer:** Product, Solutions, Use cases, Company (Contact, Support), Legal (Privacy, Terms,
  Refunds), and a line stating that scores are modelled estimates.

## Page templates

1. **Home** — hero (headline + map-screenshot carousel), audience switcher (tabs: Retail & F&B ·
   Real estate & hotels · Lenders & fintech · Any business · Agencies & consultants), "what you get"
   bento grid, map-views slider, how-it-works strip, signal sources marquee, pricing teaser, FAQ
   (5), closing CTA band.
2. **Product page** — hero with screenshot, 3–4 value props, alternating screenshot/text rows,
   related use cases, CTA.
3. **Industry page** — hero for that buyer, their questions → our answers, the views they'll use
   (carousel), use-case cards, data used, FAQ for that segment, CTA.
4. **Use-case page** — the job, step-by-step with screens, which industries, which signals, CTA.
5. **Utility pages** — pricing (cards + comparison table + credits + seats), FAQ (accordion,
   FAQPage schema), how it works (pre-/post-login journeys), support, contact (form).

## Honesty rules for copy

- Numbers come from the data or the price book (15,331 pincodes scored = 80% of India Post's
  19,097; 3 core metrics + 15 signals; weekly refit). No invented customers, logos or testimonials.
- Scores are **modelled estimates**; property rate is labelled "est., city-level"; licensing limits
  (no OSM-derived data in paid tiers) stated plainly.
- No claims of real-time, transaction-level, footfall or competitor data.

## SEO

One `<h1>` per page, unique title/description, canonical URL, Open Graph + Twitter cards,
JSON-LD (Organization, SoftwareApplication with offers on pricing, FAQPage on FAQ pages,
BreadcrumbList), `sitemap.xml`, image `alt` text, compressed WebP images with explicit sizes,
no layout-shifting web fonts (`font-display: swap`).

## Build & serving

- Source in `site/` (Jinja2 templates + a Python content module + CSS/JS); `python3 site/build.py`
  writes static HTML to `business/`. Pricing is read from `_pricing.py` at build time.
- Served by nginx at `/business/` (needs one new `location` block — owner runs a script, like the
  security-header one). Flask also serves it for local development.
- Screenshots are captured from the real app with Playwright (seeded demo account locally).

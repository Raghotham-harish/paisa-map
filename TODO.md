# TODO

## Map-first workspace (branch `feat/map-first-workspace` → PR)

**P1 + the project-setup wizard are built and Playwright-verified locally**
(commit `90b8592`) but **not deployed**. `/workspace/map` embeds `index.html`
at `/?embed=1` in an iframe with a `postMessage` bridge; React docks a filter
bar / map controls / live Location Intelligence panel / KPI strip / compare
modal around it. New `/projects/new` 4-step wizard with a searchable
MultiSelect (type-and-Enter custom values). Design canvas:
https://claude.ai/code/artifact/840c310c-ad6b-41e3-b975-d41f9c1c173d

Deploy checklist (do in order, none done):

- [ ] Merge the PR and deploy (`deploy.sh` already builds `workspace/` — no
      change needed there).
- [ ] Run the schema migration on prod Postgres over SSH — 7 new additive
      `projects` columns (industry, signals, target_pincodes, catchment_km,
      total_investment, outcome_goal, time_horizon_months):
      `sudo bash -c 'set -a; source /etc/paisamap/db.env; set +a; venv-flask/bin/python3 -c "import sys; sys.path.insert(0,\"paisamap-etl/etl\"); import _auth_db; _auth_db.migrate_schema()"'`
      then confirm with `inspect(engine).get_columns("projects")`.
- [ ] **Check nginx does NOT send `X-Frame-Options: DENY` for `/`** — the
      `/workspace/map` iframe loads `/?embed=1` and needs at least `SAMEORIGIN`
      (workspace is same-origin, so `SAMEORIGIN` is fine). The repo sets no
      framing header; if nginx sends `DENY`, relax it to `SAMEORIGIN` (or add
      `add_header Content-Security-Policy "frame-ancestors 'self'"`) for the
      `location /` block. nginx config lives only on the server.
- [ ] Real-account browser smoke on prod once deployed: `/workspace/map` →
      click a pincode → panel shows real score → Save → appears in
      `/workspace/locations`; compare 2 → ranked modal; wizard `/projects/new`
      → finish → lands on `/map?project_id=<id>`.
- [ ] Copy the updated `deploy.sh` to `/home/ubuntu/deploy.sh` if `deploy.sh`
      was touched (it was NOT in this change — noting the standing rule only).

Follow-ups (not blockers):

- [ ] Wire the MapControls layer toggles (choropleth / rings / clusters / my
      stores) through the bridge — needs matching one-line inbound handlers in
      `index.html`'s embed bridge (`ringsLayer.addTo/removeFrom` etc.). Left as
      display-only copy for P1.
- [ ] Surface `industry`/`segment` edits inline in the FilterBar instead of
      bouncing to the wizard `?edit=` route.
- [ ] Custom signals typed in the wizard are captured as free-text strings on
      the project but do NOT become real data columns — real custom-signal
      *generation* is a separate piece (overlaps Phase 05B).
- [ ] Salesforce connector — placeholder "coming soon" card only in the wizard.

## P3 — Forecast & investment split (BUILT on this branch, NOT deployed)

Shipped on `feat/map-first-workspace`:

- `paisamap-etl/etl/_forecast_model.py` — the model. Cross-sectional, NOT
  ramp-fitted (no time series exists). Reachable household spend per pincode
  (district census pop split by a night-lights/POI/fin-density proxy ÷ 4.6) ×
  catchment aggregation with linear distance decay → capture rate **calibrated
  on the customer's own stores** (pure-Python OLS on PPI-pct + driver-fit, R²
  gate → pooled median fallback, <3 stores → `sufficient_data:false`, no
  charge). Diminishing returns from (a) greedy revenue-per-rupee ordering +
  (b) circle-overlap cannibalisation. Quality floor: no candidate weaker than
  0.8 × the customer's weakest store by PPI percentile.
- `GET /api/forecast?project_id=&budget=` (`blueprints/forecast.py`) — one
  endpoint, returns reach curve + recommended ₹ split by state + payback +
  market-capture % + lever-fit radar + assumptions. Charged 8 credits, only on
  a real forecast. Registered in `server.py`.
- 2 new `projects` columns: `gross_margin_pct`, `revenue_period` (monthly/
  annual) — `_MIGRATIONS` + `PROJECT_EDITABLE_FIELDS` + wizard step 4 fields +
  `projects.py` `_wizard_fields` validation.
- `workspace/src/pages/Forecast.tsx` (`/forecast` route + nav item), inline-SVG
  reach curve + radar, no chart lib. `LocationPanel` Forecast button now
  navigates to `/forecast?project_id=`. `api.ts` types + `getForecast`.

Verified: standalone model test + full HTTP e2e (real Flask + sqlite, cookie
mint, credit charge 100→92→84, 404/401, budget override, insufficient path
free) + Playwright screenshots of all 3 states (forecast / insufficient /
wizard step 4). `tsc --noEmit` + `vite build` clean.

- [ ] Deploy: the 2 new `projects` columns need `migrate_schema()` on prod
      Postgres (same SSH recipe as the other map-first columns above — fold
      into that same migration run).
- [ ] Add `forecast` to the pricing display if `/api/billing/pricing` is
      surfaced anywhere the cost matters (it already flows through `CREDIT_COSTS`).
- [ ] Real-account browser smoke once deployed: upload store data → `/forecast`
      → Run → curve + split + radar render with real numbers.
- [ ] Model is v1 — revisit `AVG_HOUSEHOLD_SIZE`/`DEFAULT_GROSS_MARGIN_PCT`/
      `RAMP_MONTHS`/`CANNIBALISATION_SHARE` once a real customer's forecast can
      be sanity-checked against their actual results.

## Design canvas (5 artboards) — shipped vs pending

Design canvas: https://claude.ai/code/artifact/840c310c-ad6b-41e3-b975-d41f9c1c173d
Artboards: Workspace · Main (map intelligence) · Setup (wizard) · Forecast · Compare.
P1 (`90b8592`) built the shells; P3 (`e34b589`) built the forecast model + page.
Below is what each artboard shows that is NOT yet real.

### 1 · Workspace (dashboard)
- [ ] "Pick up where you left off" hero — show last-selected pincode, compare-basket
      count, and "forecast ready at ₹X" with a **Resume forecast** button. Today's
      hero card is generic (no session-resume state).
- [ ] Recent-activity list embedded on the dashboard (exists as its own /activity page).

### 2 · Main / map intelligence (the flagship — biggest gap set)
- [ ] **Wire MapControls layer toggles through the bridge** (choropleth / hotspot
      clusters / distance rings / my stores) — still display-only copy. Needs
      one-line inbound handlers in `index.html`'s embed bridge. *(also listed above)*
- [ ] **Competitor locations layer** — a whole new data layer in the design's map
      controls; no competitor data anywhere in the app yet.
- [ ] **Market tiers panel** (Core / Edge / Expansion counts for pincodes in view) —
      classify visible pincodes into tiers; show counts in the left rail + a tier
      badge on the location panel. The forecast model already has a PPI-percentile
      quality floor that could seed the tier thresholds.
- [ ] **Viewport-scoped KPI strip** — design shows Population in view / Signal
      coverage / Avg opportunity / Priority zones / **Est. reachable buyers**.
      "Population in view" and "reachable buyers" can now be powered by
      `_forecast_model.estimate_households`. Today's KpiStrip only shows what the
      bridge emits (pincodes in view, metric avg, median income, top-tier zones).
- [ ] **Metric selector** ("Opportunity score" as a map metric, not just a raw
      signal layer) — the filter bar has a Metric chip but switching it doesn't
      drive an opportunity choropleth; the map only knows raw signal columns.
- [ ] Location panel: "Top drivers of **your** revenue" (the design shows the
      project's own Pearson drivers here) — currently shows "what drives the model"
      (global feature importance). `/api/expansion/drivers` already computes the
      real per-project version.

### 3 · Setup (wizard)
- [ ] Step 2 integration cards should reflect **real connection state** per project
      (LINKED + account/property, CSV upload status + mapped columns) — currently
      static "Connect"/"Upload" links.
- [ ] Step 3 custom lever → **suggest matching govt/public datasets** to bind it to
      (design: type "cold chain" → "Cold storage capacity (govt)" etc.). Today
      custom signals are free-text only, never become columns. *(also above)*
- [ ] Arbitrary CSV columns (e.g. **footfall**) — `CANONICAL_FIELDS` is
      store_name/address/pincode/revenue/rent/capex only; extras land in
      `extra_fields` and nothing reads them.
- [ ] "Save draft" (wizard is create-on-finish; no partial save).
- [ ] Salesforce connector — "coming soon" placeholder only. *(also above)*

### 4 · Forecast (page shipped `e34b589`, these are the remaining artboard bits)
- [ ] **"Sweet spot" callout** on the growth curve — mark the optimal investment
      point distinct from the user's budget line (model returns
      `diminishing_returns_from`; turn that into an explicit recommended number).
- [ ] **The "Nudge"** — a concrete prose recommendation ("move ₹15 L from A to B →
      +₹40 L reachable spend, payback 14→13 mo; A is near saturation"). This is a
      marquee feature of the artboard; needs a marginal-reallocation pass over the
      portfolio.
- [ ] **Hotspot bubble chart** (x = market size, y = opportunity, bubble = reachable
      buyers, Core/Edge/Expansion bands). Today the page shows a site list instead.
- [ ] **Comparative radar** — design overlays the top 3 candidate locations on a
      fixed 7-lever radar ("which candidate fits your levers best"). Today's radar
      is a single polygon (project signals vs revenue).
- [ ] **"Levers you're under-using"** explicit list (derived from the weak radar
      spokes).
- [ ] **Segment/category market capture** — design says "*segment* market capture
      4.2% → 6.8%"; the model deliberately reports share of ALL household spend
      (no category-share data). Revisit if a category-spend source appears.

### 5 · Compare (modal shipped `90b8592`)
- [ ] "Retail rent / sqft" row + "**Rent headroom vs nearby %**" row (modal has PPI
      "vs nearby" only).
- [ ] "**Strongest driver**" per location (modal shows "Top signals" — global, not
      per-project).
- [ ] "**Save all to project**" bulk action from the compare basket.
- [ ] **Saved-locations shortlist panel** inside the compare view — status tags
      (Approved/Reviewing/Shortlist/Rejected), notes, and ₹-allocated per location.
- [ ] "**Generate expansion report — 5 credits**" CTA from the compare view.

## Product roadmap — open threads not tracked in a phase above

Workspace roadmap artifact: https://claude.ai/code/artifact/241ab985-f950-446c-a4cc-7d30210e4070
(7/8 phases shipped; below are the deferred/unblocked items and the older
B2B + Paisa Buzz growth plan, whose own artifact `511014c7…` is gone — see
`project_growth_plan_two_tracks` in memory.)

### Track 1 — PaisaMap B2B (from the growth plan; only cosmetic pieces shipped)
- [ ] **Real API-key auth + tiered access backend** — still not started. Today's
      Pro-gating is server-side for `/api/export` columns only; there's no API-key
      issuance, no rate limiting, no per-key quota. This is the actual gate for
      selling data access.
- [ ] Developer portal (API docs, key management, usage).
- [ ] Public pricing page for the data/API product (distinct from workspace plans).
- [ ] Enterprise pilot outreach (12-week Gantt in the — now deleted — growth plan).
- [ ] Re-run the 4 state-level signal fetchers (agriculture / education / industrial
      / economic) — stale since the pincode set was ~275; now ~15k+. Mechanical
      (state-level values are uniform per state), not a new data hunt.

### Track 2 — Paisa Buzz (deliberately dormant)
- [ ] Gated on a registered company entity existing first (user's explicit call).
      Then: RBI Account Aggregator TSP selection (Setu/Finvu/OneMoney/CAMS),
      DPDP Act data-fiduciary obligations, consent architecture. No app code
      until the entity question is answered.

### Workspace roadmap — deferred items now unblocked or still open
- [ ] Anonymous search/comparison rate-limiting for signed-out map visitors —
      was deferred "until Phase 02 ships"; Phase 02 shipped, so revisitable.
      Still no rate-limiting infra anywhere in the app.
- [ ] Historical Trend / time-series — permanently blocked until a pincode
      snapshot table exists (everything is overwrite-on-refit today).
- [ ] Phase 06+ (deferred until paying customers): watchlists + change-detection
      alerts, team workspaces + RBAC (`organizations`/`org_members` still unused),
      white-label reports, "find markets like my best stores" similarity search,
      cannibalisation / white-space analysis, NL "Ask PaisaMap" copilot,
      scenario/what-if simulator, standalone ROI modelling.

### Secondary infra (bundle into a future push)
- [ ] Remove the stray `paisamap.bak-20260806` from nginx `sites-enabled/`
      ("conflicting server name" warning on every reload).
- [ ] Apply the pending Lightsail kernel upgrade (deliberate reboot window).

## Enterprise readiness (the 5 gaps from the growth-plan review)

Growth-plan artifact (reconstruction):
https://claude.ai/code/artifact/ce7caba8-be97-4759-a394-3b3829f00777
Gap 2 (datastore) is done — Postgres dual-write. The other four below.

### Observability — foundations SHIPPED 2026-09-07, rest open
- [x] `paisamap-etl/etl/_ops.py` — in-process request/error counters, optional
      Sentry hook (guarded), token-bucket rate limiter.
- [x] `server.py`: `before_request` rate-limit, `after_request` security headers
      + 5xx counter, `errorhandler(Exception)` that logs + counts + Sentry-captures.
- [x] `/api/health` enriched (commit, DB reachability, ratelimit flag, uptime);
      new `/api/metrics` (aggregate counters, no data).
- [x] `deploy.sh` installs `sentry-sdk` and writes `.deployed_commit`.
- [ ] **Set `SENTRY_DSN` in `/etc/paisamap/db.env`** (+ optional `SENTRY_ENV`,
      `SENTRY_TRACES_SAMPLE_RATE`) — Sentry is inert until this is set. Free tier
      is fine to start.
- [ ] External uptime check hitting `/api/health` (UptimeRobot / BetterStack
      free tier) with alerting to email/Slack.
- [ ] Structured request logging to a file nginx/journald can ship somewhere
      (today it's `print()` to stdout → journald only).
- [ ] Multi-worker note: `_ops` counters + buckets are per-process. Fine on the
      current single-process setup; revisit if gunicorn workers > 1.

### Security hardening — first pass SHIPPED 2026-09-07, rest open
- [x] Per-IP token-bucket rate limiter on `/api/*` (`_ops.check`), 429 +
      `Retry-After`, three tiers (geo / data / default), **fails open**, and
      **off unless `RATELIMIT_ENABLED=1`** so deploying it is inert.
- [x] Baseline security headers on every response (`X-Content-Type-Options`,
      `Referrer-Policy`, `X-Frame-Options: SAMEORIGIN`, CSP `frame-ancestors 'self'`).
- [ ] **Flip `RATELIMIT_ENABLED=1`** in prod after watching `/api/metrics`
      `requests_ratelimited` for a day at the default caps (tune the
      `RATELIMIT_*_CAP` / `_RPS` env vars if legit traffic trips it).
- [ ] Secrets audit: everything sensitive is in `/etc/paisamap/db.env` (600,
      root) — confirm nothing secret is in the repo, GitHub Actions, or the
      systemd unit inline; rotate `SECRET_KEY` and the DB password once; generate
      the distinct `RAZORPAY_WEBHOOK_SECRET` (already tracked under Phase 03).
- [ ] A real WSGI server (gunicorn/uwsgi) in front of Flask instead of
      `app.run()` — `deploy.sh`/systemd currently run the dev server.
- [ ] `fail2ban` or nginx `limit_req` as a second layer at the edge (the app
      limiter only sees requests nginx already forwarded).
- [ ] HSTS header (nginx, once certain the apex + www are HTTPS-only — they are).
- [ ] Dependency scanning (`pip-audit` / Dependabot) — no lockfile today; deps
      are an inline `pip install` list in `deploy.sh`. Consider a real
      `requirements.txt` with pinned versions.

### Data licensing — inventory DONE 2026-09-07, clearances open
- [x] `docs/DATA_LICENSING.md` — every source, its licence, commercial-resale
      posture, risk flags.
- [ ] 🔴 **OSM / ODbL for `premium_poi_per_km2`** — get advice on produced-work
      vs. derivative-database, or drop/replace the column for any sold tier.
- [ ] 🔴 **Nominatim** — the server-side proxy breaches the usage policy at
      scale; plan a self-hosted Nominatim or a paid geocoder before the API
      product or before real traffic growth.
- [ ] ⚠️ Read **RBI website terms** (Handbook of Statistics, BSR) — yes/no on
      commercial redistribution of derived figures. Same for **UDISE+** bulk export.
- [ ] ⚠️ Switch the vehicle signals to the **MoRTH annual-report** provenance
      (not the live VAHAN scrape) for anything sold.
- [ ] 🕰️ Refresh or footnote `cropping_intensity_pct` (data is 2012-13).
- [ ] Document where `CITY_PRIORS` (seed for `rate_per_sqft`) came from.
- [ ] Publish the attribution block (draft in the doc) once the above clear.

### Auth / multi-tenancy / org billing — NOT started (needs design first)
`organizations` + `org_members` tables exist and are unused. This is Phase 06+
territory; don't start it before a paying customer needs seats.
- [ ] Decide the model: per-seat vs. flat-org pricing; who can invite; whether
      projects belong to a user or an org.
- [ ] Org creation + member invite/accept/remove flow (email invite tokens).
- [ ] Move project/report/location ownership from `user_id` to `org_id` (or add
      org scoping alongside) — a real migration touching every workspace query.
- [ ] Role-based permissions (owner / editor / viewer) enforced in `_session.py`.
- [ ] Seat-based billing: Razorpay subscription with quantity, proration on
      seat add/remove, an org-level invoice.
- [ ] Team plan today is a `users.plan` string only — reconcile with real org billing.

### Target-customer definition — decision brief written, unanswered
- [x] `docs/TARGET_CUSTOMER.md` — candidate segments, product-shape fork, the
      5 questions to answer, a weak recommendation (retail site-selection wedge).
- [ ] **Pick one segment for the first 3 paying customers** and the wedge
      (SaaS / API / bespoke). This unblocks Track 1 · P4 (pricing) and P5 (outreach).
- [ ] Build the one proof point a buyer in that segment would check before
      believing the data (e.g. PPI vs. bureau-data correlation for lenders;
      score vs. the customer's own best/worst store for retail).

## Phase 03 — Monetisation

**Shipped and live as of 2026-09-06** — credits spend/purchase, Razorpay
integration (test mode), plan upgrade, one-off report purchase, GST invoices,
and real server-side plan enforcement (incl. closing the `/api/export`
Pro-column leak). Verified via direct API calls (order → mark paid → credits
granted/plan set/invoice+PDF generated, idempotent against a redelivered
webhook) and a real headless-browser pass confirming the actual Razorpay
Checkout modal opens live and `/workspace/credits`/`/workspace/billing`
render correctly with real pricing-config data. Webhook signature verification
also confirmed against a real signed payload (fixed a real bug found this way —
`razorpay.Utility.verify_webhook_signature` needs an instantiated `Utility`,
not a bare class call).

- [ ] **Manually complete one real card payment end-to-end** through the
      Razorpay test-mode checkout UI (`4111 1111 1111 1111`, any future
      expiry/CVV) on `/workspace/credits` — the one thing browser automation
      couldn't drive (Razorpay's checkout form resists scripted input by
      design). Confirms the success-handler → `/api/billing/verify` →
      credit-grant path fires from a real completed payment, not just the
      already-verified direct-API simulation of that same path.
- [ ] Generate a **distinct** `RAZORPAY_WEBHOOK_SECRET` (currently reused from
      the API key secret as a stopgap) via Razorpay dashboard → Webhooks →
      Edit, then update `/etc/paisamap/db.env` and restart `paisamap`.
- [ ] Add `PAISAMAP_GSTIN` / `PAISAMAP_BILLING_ADDRESS` to `db.env` once
      GST-registered — invoices currently correctly say "Not yet registered."
- [ ] Swap test-mode Razorpay keys for live ones once KYC/business
      verification is complete — pure env-var swap in `db.env`, no code change.

## Phase 05B — GA4 / Search Console OAuth

**Fully working end-to-end in production as of 2026-09-06**, verified with a real
Google account — both connections show real data (Search Console pulled actual
search queries for manekhadya.com; GA4 connected and reports "no data" honestly,
since that property has no ecommerce tracking configured, not a bug). All setup
items closed:

- [x] `GOOGLE_CLIENT_SECRET` added to `/etc/paisamap/db.env`.
- [x] Redirect URI `https://paisamaps.com/api/analytics/oauth/callback` registered.
- [x] Consent-screen scopes + test user configured.
- [x] Real end-to-end test — both providers connected, property/site picked,
      report/ecommerce/location-tags all pulling real data.
- [x] Fixed two real production bugs surfaced only by this real-account test
      (neither existed in the mocked test suite, since both are proxy/DNS-layer
      issues, not app logic): (1) nginx never forwarded `X-Forwarded-Proto`, so
      Flask always built the OAuth redirect_uri as `http://` — fixed via
      `ProxyFix` in `server.py` (commit `aff9a0b`) + `proxy_set_header
      X-Forwarded-Proto $scheme;` added to nginx (server-side, not tracked in
      this repo). (2) `www.paisamaps.com` was served directly instead of
      redirecting to the apex, so a request landing on `www` built a redirect_uri
      Google didn't recognize — fixed with a dedicated 443 redirect block on the
      server. (3) Also enable a third GCP API if not done — **Google Analytics
      Admin API** (`analyticsadmin.googleapis.com`) — needed for the property
      picker, separate from the Data API.

- [ ] (Later, not a blocker) Verify `paisamaps.com` in Search Console under this
      GCP project — needed only when submitting for Google's standard
      sensitive-scope verification.

See `project_phase05b_ga4_search_console_oauth.md` in memory for full context.

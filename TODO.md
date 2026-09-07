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

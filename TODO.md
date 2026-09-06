# TODO

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

# TODO

## Phase 05B — GA4 / Search Console OAuth: GCP console setup (blocks real connections)

The OAuth authorization-code flow (`blueprints/analytics_connections.py`, commit
`2ffbdf1`) is live in production but inert until these are done in the GCP console —
none of this is code, all of it needs your Google account access:

- [ ] Add a `GOOGLE_CLIENT_SECRET` for the existing OAuth client (or create a new
      one), then add it to `/etc/paisamap/db.env` on the server.
- [ ] Register `https://paisamaps.com/api/analytics/oauth/callback` as an exact
      "Authorized redirect URI" on that OAuth client.
- [ ] Add `analytics.readonly` + `webmasters.readonly` scopes to the OAuth consent
      screen, and add your Google account as a test user (GCP "Testing" publish
      status allows this immediately — no verification wait needed to test).
- [ ] (Later, not a blocker) Verify `paisamaps.com` in Search Console under this
      GCP project — needed only when submitting for Google's standard
      sensitive-scope verification.

See `project_phase05b_ga4_search_console_oauth.md` in memory for full context.

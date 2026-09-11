# Dashboard module — end-to-end audit & multi-session roadmap

Owner: Raghotham · Started 2026-09-11 · Status: **Phase A complete (7/7), B–H not started**

This is the living source of truth for the dashboard overhaul. Each session:
pick the next unchecked item(s) in the current phase, ship, tick the box, add a
dated note. Phases A–B are the visible UX wins and run first; C–E build the
Company→Project hierarchy; F–H are heavier R&D tracks.

Related existing docs: `DESIGN.md` (design rules), `TODO.md` (§ "Enterprise
readiness", § "Auth / multi-tenancy" — this roadmap supersedes those two
sections), `docs/DATA_LICENSING.md`, `docs/TARGET_CUSTOMER.md`.

---

## 1. What we have today

**13 workspace screens** (`workspace/src/pages/`), routed flat in `App.tsx`:
Dashboard, MapWorkspace, Projects, ProjectWizard, SavedLocations, CustomerData
("Store Data"), Forecast, Connections, Reports, Activity, Credits, Billing,
ApiKeys.

**Data model** (`paisamap-etl/etl/_auth_db.py`): everything is `user_id`-scoped.
`projects` is one flat table carrying *both* company-ish fields (`website_url`,
`business_type`, analytics consent, and OAuth connections + store data are keyed
by `project_id`) *and* project fields (`signals`, `target_pincodes`,
`total_investment`, `catchment_km`, `gross_margin_pct`…).

**`organizations` + `org_members(role)` + `users.org_id` + `projects.org_id`
tables already exist** — created months ago, **zero blueprint endpoints, zero
UI, nothing populates them.** A dormant scaffold.

**Synthesis engine**: `_signals_data` loads ~30 signal columns from
`ppi_ml_refined.csv`; `intelligence.py` / `expansion.py` / `_forecast_model.py`
/ the map consume subsets. GA4/GSC connect via `analytics_connections.py`
(tokens encrypted at rest via `_token_crypto`).

Forecast just got a full visual rebuild (`chartTheme.ts`, new card system) —
**that is the reference bar; every other screen is well below it.**

---

## 2. Audit findings

### 2.1 Navigation & workflow — "the journey is fragmented"

| # | Finding | Heuristic |
|---|---|---|
| N1 | **No global project context.** Forecast, Connections, Store Data, Map each have their *own* independent project `<select>`. Switch project on Forecast → open Map → it resets to `projects[0]`. Only `mapSession` (localStorage) persists a project, and only the Dashboard reads it. | H3, H6 |
| N2 | **No "return to where I was."** Mid-forecast → click "upload store data to calibrate" → land on `/customer-data` with no path back to the forecast you were building. Same wizard→connections, dashboard→map→forecast. Deep-links (`?project_id=`) exist but are applied inconsistently. | H3 |
| N3 | **No breadcrumbs / no location indicator.** You can't tell which project (or, later, which company) you're operating on without opening a dropdown. | H1, H6 |
| N4 | **No project detail page.** `Projects` is a flat list with click-to-expand inline edit. There is nowhere that shows "this project: its map, its saved locations, its last forecast, its reports, its connections". | H6, H8 |
| N5 | "Credits" and "Billing" are two separate nav items that are both "money". "Store Data" (nav) vs `/customer-data` (route) vs "customer locations" (API) — three names for one thing. | H2, H4 |
| N6 | 12 flat nav items, no grouping. Will not scale to Company settings + Members + Demand + … | H8 |

### 2.2 Null / edge / variation states — "when the filter is zero the whole card goes off"

| # | Finding |
|---|---|
| E1 | **Filter → 0 results destroys card chrome.** Forecast `HotspotBubbles` / `SignalRevenuePanel` return a bare `<p>` losing the filter controls + header context. `LocationFactorTable` / `LocationCompareRadar` return `null` → a card with a heading and an empty body. Charts do `if (points.length < 2) return null` in ~6 places. |
| E2 | **Loading = plain `"Loading…"` text**, no skeletons → hard layout jump when data lands (the "UI jump" complaint). |
| E3 | **Card height varies with optional fields.** "Which locations yield well" rows grow/shrink with `overlap −X%`, `revenue capped`, `biz-meta` presence → ragged list (the screenshot). |
| E4 | Whole panels vanish silently: no `nudge` → card gone; `investment_split` empty → different card; `factorSites < 2` → compare radar card absent, no explanation. |
| E5 | Unhandled promise rejections: `Projects.onDelete`, several `.then()` chains with no `.catch()`. |

### 2.3 Consistency & standards

| # | Finding | Heuristic |
|---|---|---|
| C1 | Every list page re-implements layout. `Projects` = `.project-list` + inline-edit; `SavedLocations`/`Reports`/`Forecast` = `.list`; row actions, spacing, meta all differ. | H4 |
| C2 | Destructive actions fire on one click, no confirm, no undo — `Projects` "Delete", `SavedLocations` "Remove". Irreversible (CASCADE). | H5 |
| C3 | Button labels inconsistent ("Delete" vs "Remove"), edit is inline-expand here / navigate-to-wizard there. | H4 |
| C4 | Error display: red `<p>` lines, inconsistent placement; some paths uncaught. | H9 |
| C5 | Project selectors are bare name-only `<select>` — no "12 locations · forecast 3 days ago" context. | H6 |

### 2.4 Visual polish — "most screens are just bland"

- **No per-project map thumbnails** — the Dashboard hero has one generic hand-drawn SVG shared by everyone.
- No sparklines / micro-charts / stat chips in list rows. Rows are text-only.
- Icons only in the sidenav. List rows, chips, section headers have none.
- `.pill` chips are flat, no icons.
- Uppercase-mono kickers everywhere; heavy, dated.
- Forecast's new `chartTheme` + card system is not propagated anywhere else.

### 2.5 Hierarchy gap — Company → Project

The user's model:

```
Account
 └─ Company  (website, analytics connections, store data, USERS + roles)
     └─ Project  (signals, keywords, goals, investments, campaigns,
                  target audience, avg price)  ← forecast runs per project
```

Today: `Account (user) → Project` only. `website_url`, OAuth connections,
store data all sit on the *project* and would need to move to the *company*.
`organizations`/`org_members` are the right tables but completely unwired.

### 2.6 Missing analytics — demand intelligence

Nothing exists for: **existing demand trend**, **demand creation opportunity**,
**demand gen gap**, **demand forecast**. Needs a signal-history store (we have
enrichment cadence but not per-pincode value history over time), keyword/search
volume aggregation (GSC connects but only shows in the Connections page), and a
demand-side model alongside `_forecast_model`.

### 2.7 Signal synthesis — are we "encashing on all signals"?

No coverage map of signal → customer-visible output. Known dead weight:
`cropping_intensity_pct` surfaces only in one SWOT row; **GSC keyword data and
GA4 geography connect but never feed forecast / suitability / demand** — they're
display-only. No feedback loop: a customer's uploaded store revenue calibrates
*their own* capture rate but never enriches the shared signal set. The user
wants (a) a bidirectional coverage audit, (b) anonymised re-synthesis of
customer analytics + store performance into our enrichment, (c) possibly
scheduled AI/ML "self-intelligence" jobs.

### 2.8 Security & data protection

| # | Finding |
|---|---|
| S1 | **Customer store data (`customer_locations`: address, revenue, rent, capex) is plaintext at rest.** Revenue/rent are commercially sensitive. OAuth tokens *are* encrypted (`_token_crypto`) — extend that pattern. |
| S2 | Cross-account isolation looks sound (`get_project(id, user_id)` gates the joins) but there is **no automated test** proving every endpoint/table is isolated. |
| S3 | No data-access audit log (the `activity_log` is user-facing actions only). |
| S4 | Anonymised re-synthesis (2.7c) needs a consent + k-anonymity framework before any customer data feeds the shared set. `analytics_consent_at` exists for GA4 only. |
| S5 | Rate limiting exists only on API-key routes. Secrets rotation / pen-test checklist per `TODO.md` § "Security hardening" still open. |

---

## 3. Roadmap

Ordering: **A → B first** (visible wins, no schema risk). **C → D → E** is the
hierarchy backbone (one migration, do it once, carefully). **F, G, H** are
parallel R&D tracks that can start once C lands.

### Phase A — Dashboard UX foundation  ·  status: **complete (7/7)**

- [x] **A1** Shared primitives: `<DataList>` + `<DataRow>` (`workspace/src/components/DataList.tsx` — title / subtitle / chips / trailing / footer / expanded slots), `<StatChip>` (`StatChip.tsx`), `<MicroBar>` + `<Sparkline>` (`MicroViz.tsx`, reuse `chartTheme.ts` colours/stroke weights). `.project-list` and `.list` CSS fully retired in favour of `.data-list`/`.data-row`; migrated all 9 call sites (Projects, SavedLocations, Reports ×2, ApiKeys ×2, Activity, Billing, Credits, CustomerData ×5, Forecast). `<DataCard>` skipped — `.card` already covers that role, no duplicate needed.
- [x] **A2** `<AsyncBoundary>` (`AsyncBoundary.tsx`) + `<RowSkeleton>`/`<CardSkeleton>` (`Skeleton.tsx`): resolves loading (shimmer skeleton, kills the E2 jump) → error (`.async-error` banner + retry button) → empty (keeps caller's header/filters mounted, fixing E1) → children, in that order. Wired into every top-level list/project gate that used to be a bare `"Loading…"` string with an unhandled `.then()`: Projects, SavedLocations, Reports, ApiKeys, Activity, Billing, Credits, Connections, Forecast, and CustomerData's three gates (projects/locations/drivers). Each now has a real error state + retry instead of hanging forever on a failed fetch (partial E5). Deliberately left alone: CustomerData's `pastUploads` (never had a loading gate, just `.catch(()=>{})` added) and Forecast's chart-level `return null` guards (~6 spots, A6/A2-followup scope, not a page-level async gate) — both are real remaining E1/E4 items but out of this session's scope.
- [x] **A3** `<WorkspaceProvider>` (`lib/workspace.tsx`) — one shared project list + active-project id, persisted to `localStorage` (`pm.workspace.v1`), loaded once instead of per-page. New `<ProjectSwitcher>` in a new persistent top bar (`App.tsx`'s `.topbar`, above `main.content`, hidden on the `/map` bleed route) — the one place a project is now chosen from. `useSyncProjectFromUrl()` mounted once inside the provider: a `?project_id=` deep link still wins on arrival and writes into shared context (N2). Migrated the three pages N1 called out as each keeping an independent `<select>` — Forecast, Connections, CustomerData — to read `useWorkspace()` and dropped their own selects/fetches/local project state entirely; each page's per-project side effects (Forecast's prev-curve/budget reload, CustomerData's upload-reset, Connections' consent/connections reload) now key off the shared `activeProjectId` via `useEffect` instead of an `onChange` handler. Company-level context (the other half of A3's original scope) deferred to Phase C — no `organizations` plumbing exists yet to switch between. Not done: Dashboard's separate `mapSession` (last map pincode) and the map iframe's own state are a different concern (per-viewer map resume, not project selection) and were left alone.
- [x] **A4** `<ReturnToLink>`/`<ReturnBanner>` (`components/ReturnTo.tsx`): a cross-page CTA captures its current location as `return_to`/`return_label` query params; the destination renders a "← Back to {label}" banner reading them back (N2). Wired the exact cases N2 named — Forecast's "Upload store data" CTA (`ForecastPreview.tsx`) and the wizard's "Manage"/"Connect"/"Upload" cards (`ProjectWizard.tsx`) — into Store Data and Connections, which both render `<ReturnBanner>`. Dashboard's resume hero (`map-hero` block) turned out to already cover the "Resume rail driven by real recent context" bullet — last map session, saved-location count, "Resume forecast"/"Resume on the map" CTAs were already there; only cleanup done was pointing its project list at `useWorkspace()` instead of its own `listProjects()` call, so it shares A3's context instead of duplicating the fetch. Breadcrumb bar (N3) deliberately **not** built — A3's persistent top-bar switcher already shows the active project without opening anything, which was N3's actual complaint; a second breadcrumb saying the same thing would be redundant chrome, not a fix.
- [x] **A5** `usePendingDelete()` (`lib/undo.ts`) + `<UndoToastStack>` (`components/UndoToast.tsx`): a delete hides the row immediately and defers the actual API call 5s, so a misclick is free to reverse (no separate blocking `confirm()` needed — the undo window **is** the confirmation, and it's non-blocking unlike a native dialog); starting a second delete commits any still-pending one first, so at most one is ever in flight per hook instance. Wired into the two C2 named exactly (Projects "Delete", SavedLocations "Remove") plus CustomerData's location/upload deletes (two independent hook instances there — same id could otherwise collide across the two tables). Left ApiKeys' "Revoke" and Connections' "Disconnect" on their existing native `confirm()` rather than converting them — they carry real security semantics (killing live credentials/tokens) where a silent 5s optimistic-hide is the wrong model; got a real `.catch()` added instead (see below). Reviewed button vocabulary (C3) across all destructive actions — concluded the labels are already domain-differentiated on purpose (Remove = unshortlist, Delete = destroy a record, Revoke/Disconnect = domain-specific) and left them as-is rather than force a cosmetic rename. `EmptyState.dependency` wired into the two remaining "needs a project first" empty states that didn't have it yet (Connections, Forecast) — all four now consistent with Reports/CustomerData. `.catch()` swept everywhere: a script-verified pass over every `pages/*.tsx` found and fixed every bare `try{}finally{}` with no `catch` (SavedLocations' status/notes updates, ApiKeys' revoke, three Connections handlers + its per-project `load()`, Reports' share/unshare) plus one real bug — CustomerData's upload-status poller had no catch at all, so a single transient failure mid-poll became an infinite silent-failure loop (fixed: stops polling and surfaces an error instead).
- [x] **A6** Rebuilt "Which locations yield well" in `Forecast.tsx`: PPI/CapEx/payback as `<StatChip>` icon chips (`ti-chart-bar`/`ti-currency-rupee`/`ti-hourglass`), a 4th always-present fixed-size slot for the revenue-cap/catchment-overlap caveat (an amber `ti-alert-triangle` with a native tooltip when active, invisible placeholder otherwise) — chip *count* never varies row-to-row, which is what actually fixes the E3 ragged-height problem, not a CSS height hack. `within_budget` stays a `pill` status lozenge (already distinct, just kept) next to a new `<MicroBar>` scaled to `monthly_revenue / max(monthly_revenue)` across all recommended sites. **Note on "sparkline"**: `ForecastSite` has no per-site time series in the API (`api.ts`'s `ForecastSite` — single `monthly_revenue` number, no history) — a literal sparkline would have had to fabricate a trend that doesn't exist, which conflicts with this codebase's honesty-labelling convention (proxy/sample values get called out explicitly elsewhere, e.g. the Forecast rewrite's SWOT/spider-chart work). Used the already-built `<MicroBar>` (a real relative-magnitude bar, from A1) instead of `<Sparkline>` — same "reachable-revenue at a glance" job, no fabrication. Sites now split into `within_budget` (always shown) and the rest, collapsed behind a "Show N more — need a bigger budget" toggle (`useState`, default collapsed) reusing the existing `.fc-stale-link` treatment.
- [x] **A7** Sidenav regrouped into three labelled sections in `App.tsx` (`NAV_SECTIONS` replaces the flat 12-item `NAV` array) — **Workspace** (Dashboard, Map, Projects, Saved Locations, Forecast, Reports), **Data** (Store Data, Connections), **Account** (Activity, Billing, API Keys). Credits genuinely folded into Billing (N5), not just visually grouped: `Credits.tsx` deleted, its balance/buy-credits/ledger UI moved into `Billing.tsx` as a "Credits" section between the existing "Plan" and "Invoices" sections (each with its own `AsyncBoundary`/error state — a failure loading invoices doesn't blank the credits ledger and vice versa); `/credits` now a redirect to `/billing` (`<Navigate replace>`) so old links/bookmarks still land somewhere real. "Store Data" naming: the user-facing name was already consistent (nav label and page `<h1>` both already said "Store Data") — the mismatch N5 flagged is `/customer-data` (route slug) vs "customer locations" (internal API/type naming), both invisible to a user who navigates by clicking, not typing URLs; renaming either would touch the backend, several hardcoded `ReturnToLink` targets, and risk breaking bookmarks for zero user-visible benefit, so left alone.

### Phase B — Visual production polish  ·  status: not started

- [ ] **B1** Per-project **map thumbnails**: server-side static snapshot (saved locations + suitability tint) cached per project, or a lightweight client `<MiniMap>` canvas. Show on Dashboard, Projects list, Project detail.
- [ ] **B2** Micro-viz everywhere: sparklines / stat chips / mini bar rows in every list row (adopt `chartTheme`); Dashboard stat tiles get trend arrows.
- [ ] **B3** Screen-by-screen redesign to the Forecast bar: Dashboard, Projects (+ **new Project detail page**, N4), SavedLocations, Reports, CustomerData, Connections, Credits/Billing, Activity, ApiKeys.
- [ ] **B4** Iconography + chip enrichment pass (status pills get icons, meta becomes labelled chips).
- [ ] **B5** Adopt the Forecast/Compare popup input patterns the user liked (filter selects, action bars) as shared components.

### Phase C — Company → Project hierarchy  ·  status: not started  ·  blocked-by: design sign-off

- [ ] **C1** Decide the model (per-seat vs flat-org pricing; do projects belong to a company always?). Write a 1-page brief, get sign-off.
- [ ] **C2** `blueprints/organizations.py` + `_auth_db` functions: create company, list, update, delete; `company` = { name, website, analytics connections, store data, members }.
- [ ] **C3** Migration: wrap every existing user's projects in an auto-created default company; move `website_url` + OAuth connections + `customer_uploads`/`customer_locations` ownership from `project_id` → `company_id` (keep project-level overrides where they make sense).
- [ ] **C4** Company switcher + "Company settings" screen; project list + all queries scoped to the active company.
- [ ] **C5** Re-point Forecast / expansion / reports: still per-project, but store data + connections now shared across the company's projects.

### Phase D — Users, roles & permissions  ·  status: not started  ·  blocked-by: C

- [ ] **D1** Email invite flow (invite token, accept/decline), `org_members.role` = owner / admin / editor / viewer.
- [ ] **D2** Permission middleware in `_session.py` — role check on every mutating endpoint; read/write/admin matrix.
- [ ] **D3** Member-management UI (invite, change role, remove) under Company settings.
- [ ] **D4** Data-access audit log (S3) — who viewed/exported what, admin-visible.

### Phase E — Project-level actions  ·  status: not started  ·  blocked-by: C

- [ ] **E1** Link generated reports to the project; show them on the Project detail page; "Save report" from Forecast/Compare.
- [ ] **E2** Share icon on a project — share with named company members (D) and/or a public read-only project view (reuse the report `share_token` pattern).
- [ ] **E3** Manage project: rename / **duplicate** / archive / delete, all with confirm + undo (A5).
- [ ] **E4** Project detail page (N4) — the hub: map thumbnail, saved-location shortlist, latest forecast summary, reports, connections, activity, members with access.

### Phase F — Demand intelligence  ·  status: not started  ·  can start after C

- [ ] **F1** `signal_history` table — snapshot key signal values per pincode on the enrichment cadence (start capturing now even if UI comes later).
- [ ] **F2** **Existing demand trend** — category/geo demand index from GSC keyword volume + UPI txn value + footfall-proxy trend over `signal_history`.
- [ ] **F3** **Demand creation opportunity** — where modelled purchasing power outruns current commercial supply (underserved gap).
- [ ] **F4** **Demand gen gap** — connected GA4/GSC actual demand vs modelled reachable demand for the catchment.
- [ ] **F5** **Demand forecast** — demand-side projection alongside `_forecast_model` (trend × seasonality × the project's levers).
- [ ] **F6** A "Demand" section on the Project detail page with these four.

### Phase G — Signal synthesis & enrichment engine  ·  status: not started  ·  can start after C

- [ ] **G1** Signal → output coverage matrix (doc + a live internal dashboard): every one of the ~30 signals mapped to which customer output consumes it, both directions. Flag dead weight.
- [ ] **G2** Wire the dormant connected data in: GSC keywords + GA4 geography → demand (F) + suitability + forecast calibration.
- [ ] **G3** Bidirectional loop — anonymised re-synthesis of customer store performance + connected analytics into the shared enrichment set (gated by G-consent + H4 k-anonymity).
- [ ] **G4** Scheduled "self-intelligence" jobs — periodic capture-model re-fit, demand-index refresh, per-customer nudge generation ("your best-performing signal shifted", "a new hotspot appeared near your catchment").
- [ ] **G5** Consent framework for data re-use — explicit opt-in per company, revocable, with a plain-language explanation of what is shared and how it is anonymised.

### Phase H — Security & data protection  ·  status: not started  ·  runs alongside C–G

- [ ] **H1** Column-level encryption for sensitive customer fields (revenue / rent / capex / address) via the `_token_crypto` pattern; key management + rotation plan.
- [ ] **H2** Automated cross-account isolation test suite — every endpoint × every table, a second user must never read/write the first user's rows (or another company's).
- [ ] **H3** Data-access audit log (shared with D4).
- [ ] **H4** Anonymisation / k-anonymity guarantees + a re-identification risk review for the G3 pipeline.
- [ ] **H5** Rate limiting on all mutating endpoints, secrets rotation, dependency-vuln scan, a pen-test checklist; close `TODO.md` § "Security hardening".

---

## 4. Session log

| Date | Session did | Phase items touched |
|---|---|---|
| 2026-09-11 | Full audit; wrote this doc + published tracking artifact. | — |
| 2026-09-11 | Drafted `docs/PRICING_MODEL.md` — the pricing/credits model depends on Phase C (companies) + a billing-v2; see that doc §11 for P1–P9 sequencing. | C (context) |
| 2026-09-11 | Shipped A1: `DataList`/`DataRow`/`StatChip`/`MicroBar`/`Sparkline` primitives, retired `.project-list`/`.list` CSS, migrated all 9 list call sites. Verified with `tsc --noEmit` + `vite build` (both clean) and a static Playwright screenshot of the new markup against `styles.css` (no local backend/auth session available to click-test the live pages). Next: A2 (null/empty/loading/error `<AsyncBoundary>` system). | A |
| 2026-09-11 | Shipped A2: `AsyncBoundary` + `RowSkeleton`/`CardSkeleton`, wired into 10 pages' top-level list/project gates (see A2 note for the full list and what was deliberately left out). Verified with `tsc --noEmit` + `vite build` (clean) and a static Playwright screenshot of the skeleton/error states. Next: A3 (global project context / persistent switcher) — the highest-leverage remaining item since N1/N2/C5 all trace back to the lack of one. | A |
| 2026-09-11 | Shipped A3: `WorkspaceProvider` + `ProjectSwitcher` in a new persistent top bar, deep-link sync, migrated Forecast/Connections/CustomerData off their own per-page selects onto shared context. Verified with `tsc --noEmit` + `vite build` (clean) and a static Playwright screenshot of the top bar. Company-level context deferred to Phase C (nothing to switch between yet). Next: A4 (breadcrumb / "continue your journey" / Dashboard resume rail) or A7 (nav restructure) — both smaller, more isolated items than A3 was. | A |
| 2026-09-11 | Shipped A4: `ReturnToLink`/`ReturnBanner` wired into the exact N2 cases (Forecast→Store Data, Wizard→Connections/Store Data); found Dashboard's existing `map-hero` already satisfied the "Resume rail" bullet, just repointed its project fetch at `useWorkspace()`; deliberately skipped the breadcrumb-bar sub-item as redundant with A3's topbar. Phase A now 4/7. Verified with `tsc --noEmit` + `vite build` (clean) and a static screenshot of the return banner. Next: A5 (confirm/undo on destructive actions + remaining `.catch()` gaps) or A7 (nav restructure) — A6 (rebuild the forecast site-list widget) is the meatiest of what's left. | A |
| 2026-09-11 | Shipped A5: `usePendingDelete`/`UndoToastStack` on Projects/SavedLocations/CustomerData's two delete flows; script-verified `.catch()` sweep across every page found and fixed every bare `try{}finally{}` plus a real bug (CustomerData's upload-status poller could silently loop-fail forever on one transient error); wired the last two missing `EmptyState.dependency` spots (Connections, Forecast); reviewed and deliberately kept existing button vocabulary (domain-differentiated on purpose, not actually inconsistent) and ApiKeys/Connections' native `confirm()` (security semantics, not a fit for optimistic undo). Phase A now 5/7 — only A6 (rebuild the forecast site-list widget with sparklines) and A7 (nav restructure) remain. Verified with `tsc --noEmit` + `vite build` (clean) and a static screenshot of the undo-toast stack. | A |
| 2026-09-11 | Shipped A6: rebuilt "Which locations yield well" with icon chips (PPI/CapEx/payback + a fixed-size caveat slot, so row height no longer depends on which optional fields a site happens to have — the actual fix for E3, not a CSS trick), a `<MicroBar>` revenue-magnitude bar instead of a literal sparkline (no per-site time series exists in the API; fabricating a trend would have broken the honesty-labelling convention elsewhere in this codebase), and a collapsed-by-default "Show N more — need a bigger budget" group for over-budget sites. Phase A now 6/7, only A7 (nav restructure) left. Verified with `tsc --noEmit` + `vite build` (clean) and a static screenshot of the rebuilt widget. | A |
| 2026-09-11 | Shipped A7 — **Phase A now complete, 7/7**: sidenav regrouped into Workspace/Data/Account sections; Credits genuinely merged into Billing (page deleted, its UI folded in as a "Credits" section, `/credits` now redirects) rather than just visually grouped; reviewed the "Store Data" naming complaint and found the user-facing name was already consistent, the mismatch was only in internal route/API naming with no UX impact, so left it alone rather than churn URLs/backend for a cosmetic win. Verified with `tsc --noEmit` + `vite build` (clean) and static screenshots of the grouped sidenav and merged Billing page. Next: pick up Phase B (visual production polish — per-project map thumbnails, micro-viz everywhere, screen-by-screen redesign to the Forecast bar, new Project detail page) or start Phase C (Company→Project hierarchy, which the pricing model in `docs/PRICING_MODEL.md` is blocked on). | A |

# Dashboard module — end-to-end audit & multi-session roadmap

Owner: Raghotham · Started 2026-09-11 · Status: **audit complete, execution not started**

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

### Phase A — Dashboard UX foundation  ·  status: not started

- [ ] **A1** Shared primitives: `<DataCard>`, `<DataList>` + `<DataRow>` (fixed min-height, consistent slots: title / meta chips / actions / expand), `<StatChip>`, `<MicroBar>`, `<Sparkline>` (reuse `chartTheme.ts`). Retire `.project-list` vs `.list` split.
- [ ] **A2** Null/empty/loading/error system: `<AsyncBoundary>` wrapper — skeleton on load (kills the jump, E2), in-place "no matches" that **keeps** card chrome + filters (E1), consistent error toast (C4). Every forecast chart + every list adopts it.
- [ ] **A3** Global project context: `<WorkspaceProvider>` (active company later, active project now) + a persistent switcher in a new top bar. Every page reads context instead of its own `<select>` (N1). Deep-link `?project_id=` still wins and writes back to context.
- [ ] **A4** "Continue your journey": breadcrumb bar (N3); when a screen sends you elsewhere to unblock a task (forecast→store-data, wizard→connections) pass a `return_to` and show a "← back to your forecast" affordance (N2); a "Resume" rail on the Dashboard driven by real recent context.
- [ ] **A5** NN/g pass: confirm dialog + 5-second undo on every destructive action (C2, E5), one button vocabulary, wire unused `EmptyState.dependency` everywhere it applies, add `.catch()` everywhere.
- [ ] **A6** Rebuild "Which locations yield well" (the screenshot): title + PPI/CapEx/payback as **icon chips**, a mini reachable-revenue **sparkline** per row, `within_budget` as a clear status lozenge, fixed row height, "needs bigger budget" grouped and collapsed by default.
- [ ] **A7** Nav restructure: group into sections (Workspace / Data / Account), fold Credits into Billing (N5), rename "Store Data"→consistent.

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

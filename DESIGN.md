# PaisaMap design rules

One product, two surfaces — the public map (`index.html`) and the `/workspace`
React app share the same "Economic Hub" design tokens (`workspace/src/styles.css`
`:root`, mirrored inline in `index.html`). Don't fork a second visual language for
new screens; extend the shared tokens/classes instead.

Tokens: `--paper/-2/-3` (backgrounds), `--ink`/`--ink-soft` (text), `--rupee`/
`--rupee-deep` (brand green, primary actions), `--amber` (focus/warning), `--flame`
(destructive/negative), `--border`, `--radius`/`--radius-lg`, `--shadow-1`,
`--sans` (Hanken Grotesk), `--mono` (Anybody, used for labels/pills/eyebrows).

Shared components live in `workspace/src/components/`. Reuse `.btn` / `.btn.secondary`,
`.card`, `.stat-tile`, `.list`/`.project-list`, `.pill` — never hand-roll one-off
button or card styling per page.

## No empty screens

Every list/data view must handle its empty state with the shared `<EmptyState>`
component (`workspace/src/components/EmptyState.tsx`), not a bare "nothing here"
sentence. An empty state always has:

1. **An illustration or icon** — see below.
2. **A one-line title** in plain language ("Create your first project", not "No data").
3. **A one-line description** of what this view is for.
4. **A primary action** that is the get-started step *for that specific context* —
   not a generic link back to the dashboard. Examples already wired in:
   - Saved Locations (empty) → "Open the map" (locations are only created from
     the map popup, so the action sends the user there).
   - Projects (empty) → "Get started" focuses the always-visible create form.
   - Dashboard recent activity (empty) → "Open the map" + "Create a project"
     (either path unblocks the same dashboard).
5. **Nest inside the surrounding `.card` with `bare`**, so it doesn't double-box
   (see Dashboard's usage) — only free-standing empty states (a whole page with
   nothing in it, e.g. Saved Locations, Projects) get the bordered card treatment.

### Dependency gating

If the primary action can't actually be taken yet because something upstream is
missing (e.g. Reports needs a project to attach to before generation is possible),
don't show a dead-end message or a disabled button with no explanation. Use the
`dependency` prop to state what's missing in one line, and point the action at
resolving *that* dependency, not at the blocked feature. See `Reports.tsx`: zero
projects → "You don't have a project yet — create one to unlock report
generation" with a CTA straight to `/projects`; projects exist but zero reports →
plain "coming in a later phase" note, since generation itself isn't built yet and
there's nothing to gate.

### Empty-state illustrations

Use [unDraw](https://undraw.co/) for empty-state art — it's free, single-accent-color
SVGs that recolor cleanly to `--rupee` (#216A0B) or `--paper-2`. Workflow:

1. Pick an illustration matching the state's *action*, not just its noun (search
   "add", "empty", "in progress", "adding files", "location", "hiring" etc. — not
   generic "empty box" clipart for everything).
2. On undraw.co, set the accent color to `#216A0B` (or `#DFAE3A` for a
   softer/secondary state) before downloading the SVG.
3. Save it under `workspace/src/assets/illustrations/<name>.svg`.
4. Import it and pass as `illustration={...}` to `<EmptyState>` — it takes
   precedence over `icon`. Keep the `icon` emoji as a fallback prop only, not the
   long-term look; swap it for a real illustration when one is added for that
   screen (none are checked in yet — every current empty state still uses the
   emoji fallback).

## Search

Any list page (not single stat views) gets an inline `<SearchInput>`
(`workspace/src/components/SearchInput.tsx`) once the list is non-empty — client-side
filtering on the fields the user actually scans by (name/pincode for locations,
name/business type for projects). Don't add search chrome to an empty list; the
empty state's CTA is the only thing that should show. If a filtered search itself
returns nothing, show a lightweight `<EmptyState icon="🔍">` ("No matches") without
actions — don't repeat the get-started CTA there, the user already has items.

## Applying this to new screens

When adding a new workspace page: build the empty state and its context-appropriate
CTA *before* the populated-list view — it's the state most users will actually see
first. Ask "what single action gets this screen from empty to non-empty, and does
that action depend on anything else existing first?" before writing the JSX.

// Shared chart theme for the Forecast / Compare dashboards.
//
// Two colour languages, per the design decision:
//  - MAGNITUDE encodings (a bubble coloured by PPI, a lever bar, a factor-delta
//    cell) use the MAP INDEX ramps — the same diverging ColorBrewer scales the
//    map legend uses, so the workspace reads as one system.
//  - IDENTITY encodings (one polygon per location on the comparison radar, one
//    series per location in a Compare chart) use LOC_COLORS, a small categorical
//    set — diverging ramps can't tell five overlaid polygons apart.

export const INK = "var(--ink)";
export const SOFT = "var(--ink-soft)";
export const GRID = "var(--border)";
export const RUPEE = "var(--rupee)";
export const RUPEE_DEEP = "var(--rupee-deep)";
export const AMBER = "#DFAE3A";
export const FLAME = "var(--flame)";

// line weights — everything thin, one notch heavier for the primary series
export const STROKE = 1;
export const STROKE_PRIMARY = 1.5;
export const DOT = 2;
export const DOT_PRIMARY = 3;

// ── map index ramps (mirror of index.html PALETTES, low → high) ──────────────
export const RAMPS: Record<string, string[]> = {
  ppi: ["#D73027", "#FC8D59", "#FEE08B", "#D9EF8B", "#91CF60", "#1A9850"], // RdYlGn
  income: ["#762A83", "#AF8DC3", "#E7D4E8", "#D9F0D3", "#7FBF7B", "#1B7837"], // PRGn
  spend: ["#8C510A", "#D8B365", "#F6E8C3", "#C7EAE5", "#5AB4AC", "#01665E"], // BrBG
  suitability: ["#D53E4F", "#FC8D59", "#FEE08B", "#E6F598", "#99D594", "#3288BD"], // Spectral
  footfall: ["#C51B7D", "#E9A3C9", "#FDE0EF", "#E6F5D0", "#A1D76A", "#4D9221"], // PiYG
};

/** Map a 0–100 value onto a ramp (6 buckets). */
export function ramp(name: keyof typeof RAMPS | string, v0to100: number | null | undefined): string {
  const r = RAMPS[name] ?? RAMPS.ppi;
  if (v0to100 == null || Number.isNaN(v0to100)) return SOFT;
  const i = Math.max(0, Math.min(r.length - 1, Math.floor((v0to100 / 100) * r.length)));
  return r[i];
}

/** Signed-delta colour: green above baseline, flame below, soft at ~0. */
export function deltaColor(d: number | null | undefined): string {
  if (d == null) return SOFT;
  if (d >= 3) return "#1A9850";
  if (d <= -3) return "#BA1A1A";
  return SOFT;
}

// ── categorical — location identity, capped at 5 (matches MAX_COMPARE) ───────
export const LOC_COLORS = ["#216A0B", "#2a78d6", "#eb6834", "#8A5A00", "#7d3ac1"];

// tier fills for the hotspot chart when colour-by = tier
export const TIER_FILL: Record<string, string> = {
  core: RUPEE_DEEP,
  edge: "#8A5A00",
  expansion: SOFT,
};

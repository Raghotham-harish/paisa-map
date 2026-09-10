// Shared forecast dashboard charts + panels. Full-width, large in-SVG type,
// 1 px lines (1.5 px for the primary series), map-index colour ramps for
// magnitude and a small categorical set for location identity — see chartTheme.ts.
// Used by both the live Forecast page and ForecastPreview (sample data).

import { useMemo, useState } from "react";
import {
  AMBER, DOT, DOT_PRIMARY, deltaColor, GRID, INK, LOC_COLORS, ramp, RAMPS,
  RUPEE, RUPEE_DEEP, SOFT, STROKE, STROKE_PRIMARY, TIER_FILL,
} from "./chartTheme";

export function money(n: number | null | undefined, compact = false): string {
  if (n == null) return "—";
  if (compact) {
    if (Math.abs(n) >= 1e7) return `₹${(n / 1e7).toFixed(2)} Cr`;
    if (Math.abs(n) >= 1e5) return `₹${(n / 1e5).toFixed(1)} L`;
    if (Math.abs(n) >= 1e3) return `₹${Math.round(n / 1e3)} k`;
  }
  return `₹${Math.round(n).toLocaleString("en-IN")}`;
}

const pctStr = (n: number | null | undefined, dp = 1) => (n == null ? "—" : `${n > 0 ? "+" : ""}${n.toFixed(dp)}%`);

/* ── KPI strip ──────────────────────────────────────────────────────────── */
export type Kpi = { label: string; value: string; hint?: string; delta?: number | null; deltaLabel?: string };
export function KpiStrip({ items }: { items: Kpi[] }) {
  return (
    <div className="fc-kpis">
      {items.map((k) => (
        <div className="fc-kpi" key={k.label}>
          <div className="fc-kpi-label">{k.label}</div>
          <div className="fc-kpi-value">{k.value}</div>
          {k.delta != null && Math.abs(k.delta) >= 0.05 && (
            <div className="fc-kpi-delta" style={{ color: deltaColor(k.delta) }}>
              {k.delta > 0 ? "▲" : "▼"} {k.deltaLabel ?? `${Math.abs(k.delta).toFixed(0)}%`} vs last run
            </div>
          )}
          {k.hint && <div className="fc-kpi-hint">{k.hint}</div>}
        </div>
      ))}
    </div>
  );
}

/* ── investment → reachable revenue curve ──────────────────────────────── */
type CurvePt = { investment: number; monthly_revenue: number; stores?: number; at_budget?: boolean };
export function ReachCurve({
  points, budget, dimFrom, smallestViable, prev,
}: {
  points: CurvePt[];
  budget?: number | null;
  dimFrom?: number | null;
  smallestViable?: { investment: number; monthly_revenue: number } | null;
  prev?: { points: CurvePt[]; budget: number } | null;
}) {
  if (points.length < 2) return null;
  const W = 900, H = 340, L = 74, R = 30, T = 26, B = 52;
  const allPts = prev ? [...points, ...prev.points] : points;
  const maxX = Math.max(...allPts.map((p) => p.investment)) || 1;
  const maxY = Math.max(...allPts.map((p) => p.monthly_revenue)) || 1;
  const x = (v: number) => L + (v / maxX) * (W - L - R);
  const y = (v: number) => H - B - (v / maxY) * (H - B - T);
  const path = (ps: CurvePt[]) => ps.map((p) => `${x(p.investment)},${y(p.monthly_revenue)}`).join(" ");
  const area = `${x(0)},${y(0)} ${path(points)} ${x(points[points.length - 1].investment)},${y(0)}`;
  const bx = budget != null && budget <= maxX ? x(budget) : null;
  const atBudget = points.find((p) => p.at_budget) ?? null;
  const firstStore = points.find((p) => (p.stores ?? 0) >= 1) ?? null;

  const marker = (px: number, py: number, label: string, color: string, above = true, anchor: "middle" | "end" | "start" = "middle") => (
    <g>
      <circle cx={px} cy={py} r={3.5} fill={color} stroke="#fff" strokeWidth={1} />
      <text x={anchor === "end" ? px - 7 : anchor === "start" ? px + 7 : px} y={above ? py - 9 : py + 16}
        fontSize={11.5} fontWeight={700} textAnchor={anchor} fill={color}>
        {label}
      </text>
    </g>
  );

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: "block", maxWidth: "100%", overflow: "visible" }}>
      {dimFrom != null && dimFrom <= maxX && (
        <rect x={x(dimFrom)} y={T} width={W - R - x(dimFrom)} height={H - B - T} fill={AMBER} opacity={0.09} />
      )}
      {[0, 0.5, 1].map((t) => (
        <g key={t}>
          <line x1={L} x2={W - R} y1={y(maxY * t)} y2={y(maxY * t)} stroke={GRID} strokeWidth={STROKE} />
          <text x={L - 8} y={y(maxY * t) + 4} fontSize={12} textAnchor="end" fill={SOFT}>{money(maxY * t, true)}</text>
        </g>
      ))}
      <polygon points={area} fill={RUPEE} opacity={0.09} />
      {prev && (
        <polyline points={path(prev.points)} fill="none" stroke={SOFT} strokeWidth={STROKE}
          strokeDasharray="4 3" opacity={0.8} />
      )}
      <polyline points={path(points)} fill="none" stroke={RUPEE_DEEP} strokeWidth={STROKE_PRIMARY} strokeLinejoin="round" />
      {points.map((p, i) => (
        <circle key={i} cx={x(p.investment)} cy={y(p.monthly_revenue)} r={DOT} fill={RUPEE_DEEP} />
      ))}

      {bx != null && (
        <>
          <line x1={bx} x2={bx} y1={T} y2={H - B} stroke={INK} strokeWidth={STROKE} strokeDasharray="5 4" />
          <text x={bx} y={H - B + 16} fontSize={12} textAnchor="middle" fill={INK} fontWeight={700}>your budget</text>
          <text x={bx} y={H - B + 30} fontSize={11} textAnchor="middle" fill={SOFT}>{money(budget!, true)}</text>
        </>
      )}
      {atBudget && marker(x(atBudget.investment), y(atBudget.monthly_revenue),
        `${money(atBudget.monthly_revenue, true)}/mo`, RUPEE_DEEP, true, "end")}
      {firstStore && !atBudget && marker(x(firstStore.investment), y(firstStore.monthly_revenue), "1st store", RUPEE_DEEP, false)}
      {smallestViable && smallestViable.investment <= maxX && !atBudget && (
        marker(x(smallestViable.investment), y(smallestViable.monthly_revenue), "smallest viable", AMBER)
      )}
      {dimFrom != null && dimFrom <= maxX && (
        <text x={x(dimFrom) + 6} y={T + 14} fontSize={11.5} fill="#8A5A00" fontWeight={700}>diminishing returns →</text>
      )}

      {/* axis ends */}
      <text x={L} y={H - 8} fontSize={11.5} fill={SOFT}>₹0 invested</text>
      <text x={W - R} y={H - 8} fontSize={11.5} textAnchor="end" fill={SOFT}>{money(maxX, true)} invested</text>
      <text x={L - 8} y={y(0) + 4} fontSize={12} textAnchor="end" fill={SOFT}>₹0</text>
    </svg>
  );
}

/* ── "what's moving this curve" — signed driver rows ───────────────────── */
export type CurveDriver = {
  key: string; label: string; value: number; unit: string;
  delta_pct: number | null; direction: "up" | "down" | "flat"; basis: string;
};
function fmtDriver(d: CurveDriver): string {
  if (d.unit === "currency") return money(d.value, true);
  if (d.unit === "percent") return `${d.value}%`;
  if (d.unit === "count") return Math.round(d.value).toLocaleString("en-IN");
  return String(d.value);
}
export function CurveDrivers({ drivers }: { drivers: CurveDriver[] }) {
  return (
    <div className="fc-drivers">
      <div className="fc-drivers-head">What's moving this curve</div>
      {drivers.map((d) => (
        <div className="fc-driver" key={d.key} title={d.basis}>
          <span className="fc-driver-label">{d.label}</span>
          <span className="fc-driver-val mono">{fmtDriver(d)}</span>
          <span className="fc-driver-delta" style={{ color: d.direction === "flat" ? SOFT : deltaColor(d.direction === "up" ? 9 : -9) }}>
            {d.direction === "flat" ? "assumed" : `${d.direction === "up" ? "▲" : "▼"} ${d.delta_pct != null ? pctStr(d.delta_pct, 0) : ""}`}
          </span>
        </div>
      ))}
    </div>
  );
}

/* ── does each signal move revenue? — indicator rows ──────────────────── */
export type SignalRow = {
  label: string; lever_fit: number | null; site_avg_percentile: number | null;
  verdict: string; direction: "positive" | "negative" | null; sample_size: number;
};
export function SignalRevenuePanel({ rows }: { rows: SignalRow[] }) {
  if (!rows.length) return <p className="wiz-hint">No signals selected on this project.</p>;
  const verdictTone = (v: string) => (v.startsWith("moves") ? "delta-pos" : v.startsWith("weak") ? "rejected" : "shortlist");
  return (
    <div className="fc-sigpanel">
      {rows.map((r) => {
        const shown = r.lever_fit ?? r.site_avg_percentile ?? 0;
        return (
          <div className="fc-sigrow" key={r.label}>
            <span className="fc-sig-name">{r.label}</span>
            <span className="fc-sig-track">
              <span style={{ width: `${Math.max(3, Math.min(100, shown))}%`, background: ramp("suitability", shown) }} />
            </span>
            <span className="fc-sig-num mono">{Math.round(shown)}</span>
            <span className={`pill ${verdictTone(r.verdict)}`}>{r.verdict}</span>
            <span className="fc-sig-meta">
              {r.lever_fit != null
                ? `${r.direction === "negative" ? "−" : "+"} correlation, n=${r.sample_size}`
                : `site avg percentile · ${pctStr((r.site_avg_percentile ?? 50) - 50, 0)} vs median`}
            </span>
          </div>
        );
      })}
    </div>
  );
}

/* ── hotspots — market size × opportunity, filterable ─────────────────── */
export type HotspotSite = {
  name: string; reach_gross: number; monthly_revenue: number; households?: number | null;
  capex?: number | null; ppi_percentile: number; income_percentile?: number | null;
  footfall_percentile?: number | null; within_budget?: boolean; state?: string | null; tier: string;
};
const COLOR_BY = [
  { k: "ppi", label: "PPI percentile", ramp: "ppi" as const, get: (s: HotspotSite) => s.ppi_percentile },
  { k: "income", label: "Income percentile", ramp: "income" as const, get: (s: HotspotSite) => s.income_percentile },
  { k: "footfall", label: "Footfall proxy", ramp: "footfall" as const, get: (s: HotspotSite) => s.footfall_percentile },
  { k: "tier", label: "Market tier", ramp: null, get: (s: HotspotSite) => s.tier },
];
const X_BY = [
  { k: "reach", label: "Reachable spend", get: (s: HotspotSite) => s.reach_gross, fmt: (v: number) => money(v, true) },
  { k: "households", label: "Households", get: (s: HotspotSite) => s.households ?? 0, fmt: (v: number) => Math.round(v).toLocaleString("en-IN") },
  { k: "footfall", label: "Footfall proxy", get: (s: HotspotSite) => s.footfall_percentile ?? 0, fmt: (v: number) => String(Math.round(v)) },
];
export function HotspotBubbles({ points }: { points: HotspotSite[] }) {
  const [colorBy, setColorBy] = useState("ppi");
  const [xBy, setXBy] = useState("reach");
  const [scope, setScope] = useState("all");
  const states = useMemo(() => Array.from(new Set(points.map((p) => p.state).filter(Boolean))) as string[], [points]);

  const cfgC = COLOR_BY.find((c) => c.k === colorBy)!;
  const cfgX = X_BY.find((c) => c.k === xBy)!;
  const shown = points.filter((p) =>
    scope === "all" ? true : scope === "budget" ? p.within_budget : p.state === scope);

  const W = 900, H = 360, L = 70, R = 36, T = 26, Bp = 52;
  if (shown.length < 2) return <p className="wiz-hint">Not enough sites match this filter.</p>;
  const xs = shown.map((p) => cfgX.get(p)), ys = shown.map((p) => p.monthly_revenue);
  const xLo = Math.min(...xs), xHi = Math.max(...xs), yLo = Math.min(...ys), yHi = Math.max(...ys);
  const xPad = (xHi - xLo || xHi || 1) * 0.3, yPad = (yHi - yLo || yHi || 1) * 0.3;
  const x0 = Math.max(0, xLo - xPad), x1 = xHi + xPad, y0 = Math.max(0, yLo - yPad), y1 = yHi + yPad;
  const px = (v: number) => L + ((v - x0) / (x1 - x0 || 1)) * (W - L - R);
  const py = (v: number) => H - Bp - ((v - y0) / (y1 - y0 || 1)) * (H - Bp - T);
  const caps = shown.map((p) => p.capex).filter((c): c is number => c != null);
  const maxCap = caps.length ? Math.max(...caps) : null;
  const rad = (c: number | null | undefined) => (maxCap && c ? 10 + (c / maxCap) * 24 : 14);
  const bubbleColor = (s: HotspotSite) =>
    cfgC.ramp ? ramp(cfgC.ramp, cfgC.get(s) as number) : (TIER_FILL[String(cfgC.get(s))] ?? SOFT);
  // only label the ~10 highest-revenue bubbles — a 20-point scatter is unreadable with every label
  const labelSet = new Set([...shown].sort((a, b) => b.monthly_revenue - a.monthly_revenue).slice(0, 10).map((s) => s.name));

  return (
    <>
      <div className="fc-filters">
        <label>Colour by
          <select value={colorBy} onChange={(e) => setColorBy(e.target.value)}>
            {COLOR_BY.map((c) => <option key={c.k} value={c.k}>{c.label}</option>)}
          </select>
        </label>
        <label>X axis
          <select value={xBy} onChange={(e) => setXBy(e.target.value)}>
            {X_BY.map((c) => <option key={c.k} value={c.k}>{c.label}</option>)}
          </select>
        </label>
        <label>Show
          <select value={scope} onChange={(e) => setScope(e.target.value)}>
            <option value="all">All ranked</option>
            <option value="budget">Within budget</option>
            {states.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: "block", maxWidth: "100%", overflow: "visible" }}>
        {[0, 0.5, 1].map((t) => (
          <g key={`y${t}`}>
            <line x1={L} x2={W - R} y1={py(y0 + (y1 - y0) * t)} y2={py(y0 + (y1 - y0) * t)} stroke={GRID} strokeWidth={STROKE} strokeDasharray="3 5" />
            <text x={L - 8} y={py(y0 + (y1 - y0) * t) + 4} fontSize={11.5} textAnchor="end" fill={SOFT}>{money(y0 + (y1 - y0) * t, true)}</text>
          </g>
        ))}
        {[0, 0.5, 1].map((t) => (
          <text key={`x${t}`} x={px(x0 + (x1 - x0) * t)} y={H - Bp + 16} fontSize={11.5} textAnchor="middle" fill={SOFT}>
            {cfgX.fmt(x0 + (x1 - x0) * t)}
          </text>
        ))}
        <line x1={L} x2={W - R} y1={H - Bp} y2={H - Bp} stroke={GRID} strokeWidth={STROKE} />
        <line x1={L} x2={L} y1={T} y2={H - Bp} stroke={GRID} strokeWidth={STROKE} />
        {shown.map((s, i) => {
          const c = bubbleColor(s);
          return (
            <g key={s.name + i}>
              <circle cx={px(cfgX.get(s))} cy={py(s.monthly_revenue)} r={rad(s.capex)} fill={c} opacity={0.42} stroke={c} strokeWidth={STROKE_PRIMARY}>
                <title>{`${s.name}\n${cfgX.label} ${cfgX.fmt(cfgX.get(s))} · revenue ${money(s.monthly_revenue, true)}/mo · CapEx ${money(s.capex, true)}`}</title>
              </circle>
              {labelSet.has(s.name) && (
                <text x={px(cfgX.get(s))} y={py(s.monthly_revenue) - rad(s.capex) - 3} fontSize={11} fontWeight={700} textAnchor="middle" fill={INK}>
                  {s.name.length > 13 ? s.name.slice(0, 12) + "…" : s.name}
                </text>
              )}
            </g>
          );
        })}
        <text x={L} y={T - 10} fontSize={11.5} fill={SOFT}>↑ modelled revenue / month</text>
        <text x={W - R} y={H - Bp + 34} fontSize={11.5} textAnchor="end" fill={SOFT}>{cfgX.label} / month →</text>
      </svg>
      <p className="fc-chart-note">
        Based on the top-ranked candidate pincodes near your project's target market (or your stores). Bubble size = estimated
        fit-out CapEx. Colour = {cfgC.label.toLowerCase()}.
      </p>
    </>
  );
}

/* ── budget-split lever radar — 6 spend categories ────────────────────── */
export function LeverSplitRadar({ levers }: { levers: { label: string; pct: number }[] }) {
  const n = levers.length;
  if (n < 3) return null;
  const W = 560, H = 400, cx = W / 2, cy = H / 2 - 6, Rad = 118;
  const maxPct = Math.max(...levers.map((l) => l.pct), 15) * 1.15;
  const angle = (i: number) => (Math.PI * 2 * i) / n - Math.PI / 2;
  const pt = (i: number, frac: number): [number, number] => {
    const a = angle(i);
    return [cx + Rad * frac * Math.cos(a), cy + Rad * frac * Math.sin(a)];
  };
  const dataPts = levers.map((l, i) => pt(i, Math.min(1, l.pct / maxPct)));
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: "block", maxWidth: 520, margin: "0 auto" }}>
      {[0.25, 0.5, 0.75, 1].map((f) => (
        <polygon key={f} points={levers.map((_, i) => pt(i, f).join(",")).join(" ")} fill="none" stroke={GRID} strokeWidth={STROKE} />
      ))}
      {levers.map((_, i) => {
        const [ex, ey] = pt(i, 1);
        return <line key={i} x1={cx} y1={cy} x2={ex} y2={ey} stroke={GRID} strokeWidth={STROKE} />;
      })}
      <polygon points={dataPts.map((p) => p.join(",")).join(" ")} fill={RUPEE} fillOpacity={0.26} stroke={RUPEE_DEEP} strokeWidth={STROKE_PRIMARY} strokeLinejoin="round" />
      {dataPts.map(([dx, dy], i) => <circle key={i} cx={dx} cy={dy} r={DOT_PRIMARY} fill={RUPEE_DEEP} />)}
      {levers.map((l, i) => {
        const c = Math.cos(angle(i));
        const anchor = Math.abs(c) < 0.25 ? "middle" : c > 0 ? "start" : "end";
        const [lx, ly] = pt(i, 1.22);
        return (
          <g key={l.label}>
            <text x={lx} y={ly - 3} fontSize={11.5} fontWeight={600} textAnchor={anchor} fill={INK}>{l.label}</text>
            <text x={lx} y={ly + 13} fontSize={13} fontWeight={700} textAnchor={anchor} fill={RUPEE_DEEP}>{Math.round(l.pct)}%</text>
          </g>
        );
      })}
    </svg>
  );
}

/* ── per-location operating-factor table ──────────────────────────────── */
export type FactorRow = { key: string; label: string; score: number | null; delta_vs_median: number | null; delta_vs_your_stores: number | null; basis: string };
export type FactorSite = { name: string; factors: FactorRow[] | null };
export function LocationFactorTable({ sites }: { sites: FactorSite[] }) {
  const withF = sites.filter((s) => s.factors && s.factors.length).slice(0, 5);
  if (!withF.length) return null;
  const axes = withF[0].factors!.map((f) => ({ key: f.key, label: f.label, basis: f.basis }));
  return (
    <div className="fc-ftable-wrap">
      <table className="fc-ftable">
        <thead>
          <tr>
            <th>Operating factor</th>
            {withF.map((s) => <th key={s.name}>{s.name}</th>)}
          </tr>
        </thead>
        <tbody>
          {axes.map((ax) => (
            <tr key={ax.key}>
              <td title={ax.basis} className="fc-ftable-axis">{ax.label}</td>
              {withF.map((s) => {
                const f = s.factors!.find((x) => x.key === ax.key)!;
                const d = f.delta_vs_your_stores ?? f.delta_vs_median;
                return (
                  <td key={s.name}>
                    <span className="fc-ftable-score mono">{f.score == null ? "—" : Math.round(f.score)}</span>
                    {d != null && (
                      <span className="fc-ftable-delta" style={{ color: deltaColor(d) }}>
                        {d > 0 ? "+" : ""}{Math.round(d)}
                      </span>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="fc-chart-note">
        0–100 national percentiles. The small number is the gap vs.{" "}
        {withF[0].factors!.some((f) => f.delta_vs_your_stores != null) ? "your existing stores" : "the national median"}.
        All are composite proxies over real signals — hover a row for the basis.
      </p>
    </div>
  );
}

/* ── comparison radar — up to 5 locations across the factor axes ──────── */
export function LocationCompareRadar({ sites }: { sites: FactorSite[] }) {
  const withF = sites.filter((s) => s.factors && s.factors.length).slice(0, 5);
  if (withF.length < 2) return null;
  const axes = withF[0].factors!.map((f) => f.label);
  const n = axes.length;
  const W = 580, H = 420, cx = W / 2, cy = H / 2 - 4, Rad = 120;
  const angle = (i: number) => (Math.PI * 2 * i) / n - Math.PI / 2;
  const pt = (i: number, frac: number): [number, number] =>
    [cx + Rad * frac * Math.cos(angle(i)), cy + Rad * frac * Math.sin(angle(i))];
  return (
    <>
      <div className="fc-chart-legend">
        {withF.map((s, i) => (
          <span className="fc-chart-legend-item" key={s.name}><i style={{ background: LOC_COLORS[i] }} />{s.name}</span>
        ))}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: "block", maxWidth: 540, margin: "0 auto" }}>
        {[0.25, 0.5, 0.75, 1].map((f) => (
          <polygon key={f} points={axes.map((_, i) => pt(i, f).join(",")).join(" ")} fill="none" stroke={GRID} strokeWidth={STROKE} />
        ))}
        {axes.map((_, i) => {
          const [ex, ey] = pt(i, 1);
          return <line key={i} x1={cx} y1={cy} x2={ex} y2={ey} stroke={GRID} strokeWidth={STROKE} />;
        })}
        {withF.map((s, si) => {
          const pts = s.factors!.map((f, i) => pt(i, Math.max(0, Math.min(100, f.score ?? 0)) / 100));
          return (
            <g key={s.name}>
              <polygon points={pts.map((p) => p.join(",")).join(" ")} fill={LOC_COLORS[si]} fillOpacity={0.08}
                stroke={LOC_COLORS[si]} strokeWidth={STROKE_PRIMARY} strokeLinejoin="round" />
              {pts.map(([dx, dy], i) => <circle key={i} cx={dx} cy={dy} r={DOT} fill={LOC_COLORS[si]} />)}
            </g>
          );
        })}
        {axes.map((label, i) => {
          const c = Math.cos(angle(i));
          const anchor = Math.abs(c) < 0.25 ? "middle" : c > 0 ? "start" : "end";
          const [lx, ly] = pt(i, 1.18);
          const short = label.length > 22 ? label.slice(0, 21) + "…" : label;
          return <text key={label} x={lx} y={ly} fontSize={10.5} fontWeight={600} textAnchor={anchor} fill={INK}>{short}</text>;
        })}
      </svg>
    </>
  );
}

/* ── SWOT as one table: rows = locations, cols = S/W/O/T ──────────────── */
type SwotFactor = { label: string; score?: number | null; basis: string };
export type SwotSite = {
  name: string;
  swot: { strengths: SwotFactor[]; weaknesses: SwotFactor[]; opportunities: SwotFactor[]; threats: SwotFactor[] } | null;
};
const SWOT_COLS: { key: "strengths" | "weaknesses" | "opportunities" | "threats"; title: string; tone: string }[] = [
  { key: "strengths", title: "Strengths", tone: "delta-pos" },
  { key: "weaknesses", title: "Weaknesses", tone: "rejected" },
  { key: "opportunities", title: "Opportunities", tone: "shortlist" },
  { key: "threats", title: "Threats", tone: "reviewing" },
];
export function LocationSwotTable({ sites }: { sites: SwotSite[] }) {
  const withS = sites.filter((s) => s.swot).slice(0, 5);
  if (!withS.length) return null;
  return (
    <div className="fc-swot-table-wrap">
      <table className="fc-swot-table">
        <thead>
          <tr><th>Location</th>{SWOT_COLS.map((c) => <th key={c.key}>{c.title}</th>)}</tr>
        </thead>
        <tbody>
          {withS.map((s) => (
            <tr key={s.name}>
              <td className="fc-swot-loc">{s.name}</td>
              {SWOT_COLS.map((c) => {
                const items = (s.swot as any)[c.key] as SwotFactor[];
                return (
                  <td key={c.key}>
                    {items.length === 0 ? <span className="fc-swot-none">—</span> : (
                      <ul>
                        {items.map((it) => (
                          <li key={it.label} title={it.basis}>
                            {it.score != null && <b className={`mono fc-swot-n ${c.tone}`}>{Math.round(it.score)}</b>}
                            <span>{it.label}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ── recommended investment split bars ───────────────────────────────── */
export function SplitBars({ rows }: { rows: { name: string; investment: number; share: number | null; note: string }[] }) {
  return (
    <div className="fc-split">
      {rows.map((s) => (
        <div className="fc-split-row" key={s.name}>
          <span className="fc-split-name">{s.name}</span>
          <span className="fc-split-track"><span style={{ width: `${Math.max(4, s.share ?? 0)}%` }} /></span>
          <span className="mono fc-split-val">{money(s.investment, true)}</span>
          <span className="fc-split-note">{s.note}</span>
        </div>
      ))}
    </div>
  );
}

/* ── confidence meter ────────────────────────────────────────────────── */
export function ConfidenceMeter({
  confidence, bandPct, method, r2, candidates,
}: {
  confidence: string; bandPct: number; method: string; r2: number | null; candidates: number;
}) {
  const LEVELS = ["benchmark", "low", "medium", "high"];
  const idx = Math.max(0, LEVELS.indexOf(confidence));
  const methodLabel = method === "regression" ? "Fitted regression on your stores"
    : method === "pooled_median" ? "Your stores' median capture rate"
    : "Documented benchmark capture rate — no store data yet";
  return (
    <div className="fc-conf">
      <div className="fc-conf-track">
        {LEVELS.map((l, i) => (
          <span key={l} className={`fc-conf-seg ${i <= idx ? "on" : ""}`} title={l} />
        ))}
      </div>
      <div className="fc-conf-label">{confidence} confidence · ±{bandPct}% band</div>
      <ul className="fc-rail-bullets">
        <li>{methodLabel}{r2 != null ? ` · R² ${r2}` : ""}</li>
        <li>{candidates.toLocaleString("en-IN")} candidate pincodes weighed</li>
        <li>Reach is modelled household spend, not booked sales</li>
      </ul>
    </div>
  );
}

/* ── closing summary — outcome vs cost ───────────────────────────────── */
export type SummaryStat = { label: string; value: string; sub?: string; tone?: "pos" | "neutral" | "cost" };
export function ForecastSummaryCard({ headline, stats, costs, footnote }: {
  headline: string; stats: SummaryStat[]; costs: SummaryStat[]; footnote?: string;
}) {
  return (
    <div className="card fc-summary">
      <div className="fc-summary-head">
        <svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke={RUPEE_DEEP} strokeWidth="1.6">
          <path d="M3 17V9M8 17V4M13 17v-6M18 17V7" strokeLinecap="round" />
        </svg>
        <h3>{headline}</h3>
      </div>
      <div className="fc-summary-grid">
        {stats.map((s) => (
          <div className={`fc-summary-stat tone-${s.tone ?? "neutral"}`} key={s.label}>
            <div className="fc-summary-stat-label">{s.label}</div>
            <div className="fc-summary-stat-value">{s.value}</div>
            {s.sub && <div className="fc-summary-stat-sub">{s.sub}</div>}
          </div>
        ))}
      </div>
      <div className="fc-summary-costs">
        <span className="fc-summary-costs-head">What it costs</span>
        {costs.map((c) => (
          <span className="fc-summary-cost" key={c.label}>
            <b className="mono">{c.value}</b> {c.label}
          </span>
        ))}
      </div>
      {footnote && <p className="fc-chart-note">{footnote}</p>}
    </div>
  );
}

export { RAMPS };

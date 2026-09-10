// Shared, full-width forecast charts. Sized so their text renders large enough
// to read: modest viewBox, generous in-SVG font sizes, scaled up to the card.
// Used by both the real Forecast page and ForecastPreview (sample data).

export function money(n: number | null | undefined, compact = false): string {
  if (n == null) return "—";
  if (compact) {
    if (Math.abs(n) >= 1e7) return `₹${(n / 1e7).toFixed(2)} Cr`;
    if (Math.abs(n) >= 1e5) return `₹${(n / 1e5).toFixed(1)} L`;
    if (Math.abs(n) >= 1e3) return `₹${Math.round(n / 1e3)} k`;
  }
  return `₹${Math.round(n).toLocaleString("en-IN")}`;
}

const INK = "var(--ink)";
const SOFT = "var(--ink-soft)";
const GRID = "var(--border)";
const RUPEE = "var(--rupee)";
const RUPEE_DEEP = "var(--rupee-deep)";
const AMBER = "#DFAE3A";
// dataviz categorical — for the candidate-site overlays (identity, not magnitude)
export const SITE_COLORS = ["#2a78d6", "#eb6834", "#4a3aa7", "#1baf7a"];
const TIER_FILL: Record<string, string> = { core: RUPEE_DEEP, edge: "#8A5A00", expansion: SOFT };

/* ── KPI strip ──────────────────────────────────────────────────────────── */
export function KpiStrip({ items }: { items: { label: string; value: string; hint?: string }[] }) {
  return (
    <div className="fc-kpis">
      {items.map((k) => (
        <div className="fc-kpi" key={k.label}>
          <div className="fc-kpi-label">{k.label}</div>
          <div className="fc-kpi-value">{k.value}</div>
          {k.hint && <div className="fc-kpi-hint">{k.hint}</div>}
        </div>
      ))}
    </div>
  );
}

/* ── investment → reachable revenue curve ──────────────────────────────── */
export function ReachCurve({
  points,
  budget,
  dimFrom,
}: {
  points: { investment: number; monthly_revenue: number }[];
  budget?: number | null;
  dimFrom?: number | null;
}) {
  if (points.length < 2) return null;
  const W = 900, H = 320, L = 64, R = 24, T = 24, B = 44;
  const maxX = points[points.length - 1].investment || 1;
  const maxY = Math.max(...points.map((p) => p.monthly_revenue)) || 1;
  const x = (v: number) => L + (v / maxX) * (W - L - R);
  const y = (v: number) => H - B - (v / maxY) * (H - B - T);
  const line = points.map((p) => `${x(p.investment)},${y(p.monthly_revenue)}`).join(" ");
  const area = `${x(0)},${y(0)} ${line} ${x(maxX)},${y(0)}`;
  const bx = budget != null && budget <= maxX ? x(budget) : null;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: "block", maxWidth: "100%" }}>
      {dimFrom != null && dimFrom <= maxX && (
        <rect x={x(dimFrom)} y={T} width={W - R - x(dimFrom)} height={H - B - T} fill={AMBER} opacity={0.1} />
      )}
      {[0, 0.25, 0.5, 0.75, 1].map((t) => (
        <g key={t}>
          <line x1={L} x2={W - R} y1={y(maxY * t)} y2={y(maxY * t)} stroke={GRID} strokeWidth={1} />
          <text x={L - 8} y={y(maxY * t) + 4} fontSize={13} textAnchor="end" fill={SOFT}>{money(maxY * t, true)}</text>
        </g>
      ))}
      <polygon points={area} fill={RUPEE} opacity={0.1} />
      <polyline points={line} fill="none" stroke={RUPEE_DEEP} strokeWidth={3} />
      {points.map((p, i) => (
        <circle key={i} cx={x(p.investment)} cy={y(p.monthly_revenue)} r={3.5} fill={RUPEE_DEEP} />
      ))}
      {bx != null && (
        <>
          <line x1={bx} x2={bx} y1={T} y2={H - B} stroke={INK} strokeWidth={1.5} strokeDasharray="5 4" />
          <text x={bx} y={H - B + 18} fontSize={13} textAnchor="middle" fill={INK} fontWeight={600}>your budget</text>
        </>
      )}
      {dimFrom != null && dimFrom <= maxX && (
        <text x={x(dimFrom) + 8} y={H - B - 10} fontSize={13} fill="#8A5A00" fontWeight={600}>diminishing returns →</text>
      )}
      <text x={L} y={H - 12} fontSize={13} fill={SOFT}>₹0 invested</text>
      <text x={W - R} y={H - 12} fontSize={13} textAnchor="end" fill={SOFT}>{money(maxX, true)}</text>
    </svg>
  );
}

/* ── lever fit — profile lines across signals (your stores + up to 3 sites) ─ */
export function LeverLines({
  levers,
  candidates = [],
}: {
  levers: { label: string; fit: number | null }[];
  candidates?: { label: string; values: (number | null)[] }[];
}) {
  const n = levers.length;
  if (n < 2) return null;
  const series = [
    { label: "Your stores", color: RUPEE_DEEP, w: 3, primary: true, values: levers.map((l) => l.fit) },
    ...candidates.map((c, ci) => ({
      label: c.label,
      color: SITE_COLORS[ci % SITE_COLORS.length],
      w: 2,
      primary: false,
      values: levers.map((_, li) => c.values[li] ?? null),
    })),
  ];
  const W = 900, H = 340, L = 46, R = 26, T = 22, B = 62;
  const x = (i: number) => L + (n <= 1 ? 0.5 : i / (n - 1)) * (W - L - R);
  const y = (v: number) => H - B - (Math.max(0, Math.min(100, v)) / 100) * (H - B - T);
  const short = (s: string) => (s.length > 24 ? s.slice(0, 23) + "…" : s);
  const anchorFor = (i: number) => (i === 0 ? "start" : i === n - 1 ? "end" : "middle");

  return (
    <>
      <div className="fc-chart-legend">
        {series.map((s) => (
          <span className="fc-chart-legend-item" key={s.label}>
            <i style={{ background: s.color }} />{s.label}
          </span>
        ))}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: "block", maxWidth: "100%", overflow: "visible" }}>
        {[0, 25, 50, 75, 100].map((g) => (
          <g key={g}>
            <line x1={L} x2={W - R} y1={y(g)} y2={y(g)} stroke={GRID} strokeWidth={1} />
            <text x={L - 8} y={y(g) + 4} fontSize={12} textAnchor="end" fill={SOFT}>{g}</text>
          </g>
        ))}
        {levers.map((lv, i) => (
          <g key={lv.label}>
            <line x1={x(i)} x2={x(i)} y1={T} y2={H - B} stroke={GRID} strokeWidth={1} strokeDasharray="2 4" />
            <text x={x(i)} y={H - B + 20} fontSize={12} textAnchor={anchorFor(i)} fill={INK}>{short(lv.label)}</text>
          </g>
        ))}
        {series.map((s) => {
          const pts = s.values.map((v, i) => (v == null ? null : [x(i), y(v)] as [number, number]));
          // split into contiguous segments so a missing value breaks the line
          const segs: [number, number][][] = [];
          let cur: [number, number][] = [];
          for (const p of pts) { if (p) cur.push(p); else if (cur.length) { segs.push(cur); cur = []; } }
          if (cur.length) segs.push(cur);
          return (
            <g key={s.label}>
              {segs.map((seg, si) => (
                <polyline key={si} points={seg.map((p) => p.join(",")).join(" ")} fill="none"
                  stroke={s.color} strokeWidth={s.w} strokeLinejoin="round" />
              ))}
              {s.values.map((v, i) => v == null ? null : (
                <circle key={i} cx={x(i)} cy={y(v)} r={s.primary ? 4.5 : 3.5} fill={s.color}>
                  <title>{`${s.label} · ${levers[i].label}: ${Math.round(v)}`}</title>
                </circle>
              ))}
              {s.primary && s.values.map((v, i) => v == null ? null : (
                <text key={i} x={x(i)} y={y(v) - 9} fontSize={12} fontWeight={700} textAnchor="middle" fill={s.color}>
                  {Math.round(v)}
                </text>
              ))}
            </g>
          );
        })}
      </svg>
      <p className="fc-chart-note">
        Each line is a 0–100 profile across your signals: your stores’ historical correlation with revenue, then each
        recommended site’s own percentile. A low “your stores” point = a signal that doesn’t predict your sales.
      </p>
    </>
  );
}

/* ── budget-split lever radar — 6 spend categories, a real polar chart ─── */
export function LeverSplitRadar({ levers }: { levers: { label: string; pct: number }[] }) {
  const n = levers.length;
  if (n < 3) return null;
  const W = 440, H = 400, cx = W / 2, cy = H / 2 - 6, R = 130;
  const maxPct = Math.max(...levers.map((l) => l.pct), 15) * 1.15;
  const angle = (i: number) => (Math.PI * 2 * i) / n - Math.PI / 2;
  const pt = (i: number, frac: number): [number, number] => {
    const a = angle(i);
    const r = R * frac;
    return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
  };
  const dataPts = levers.map((l, i) => pt(i, Math.min(1, l.pct / maxPct)));

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: "block", maxWidth: 460, margin: "0 auto", overflow: "visible" }}>
      {[0.25, 0.5, 0.75, 1].map((f) => (
        <polygon key={f} points={levers.map((_, i) => pt(i, f).join(",")).join(" ")}
          fill="none" stroke={GRID} strokeWidth={1} />
      ))}
      {levers.map((_, i) => {
        const [x, y] = pt(i, 1);
        return <line key={i} x1={cx} y1={cy} x2={x} y2={y} stroke={GRID} strokeWidth={1} />;
      })}
      <polygon points={dataPts.map((p) => p.join(",")).join(" ")} fill={RUPEE} fillOpacity={0.28}
        stroke={RUPEE_DEEP} strokeWidth={2.5} strokeLinejoin="round" />
      {dataPts.map(([x, y], i) => <circle key={i} cx={x} cy={y} r={4} fill={RUPEE_DEEP} />)}
      {levers.map((l, i) => {
        const c = Math.cos(angle(i));
        const anchor = Math.abs(c) < 0.25 ? "middle" : c > 0 ? "start" : "end";
        const [lx, ly] = pt(i, 1.3);
        return (
          <g key={l.label}>
            <text x={lx} y={ly - 3} fontSize={12} fontWeight={600} textAnchor={anchor} fill={INK}>{l.label}</text>
            <text x={lx} y={ly + 13} fontSize={13.5} fontWeight={700} textAnchor={anchor} fill={RUPEE_DEEP}>
              {Math.round(l.pct)}%
            </text>
          </g>
        );
      })}
    </svg>
  );
}

/* ── location SWOT — 4-quadrant grid of composite proxy factors ────────── */
export function LocationSwot({
  swot,
}: {
  swot: {
    strengths: { key: string; label: string; score: number | null; basis: string }[];
    weaknesses: { key: string; label: string; score: number | null; basis: string }[];
    opportunities: { key: string; label: string; basis: string }[];
    threats: { key: string; label: string; basis: string }[];
  };
}) {
  const quads: { title: string; tone: string; items: { label: string; basis: string; score?: number | null }[] }[] = [
    { title: "Strengths", tone: "delta-pos", items: swot.strengths },
    { title: "Weaknesses", tone: "rejected", items: swot.weaknesses },
    { title: "Opportunities", tone: "shortlist", items: swot.opportunities },
    { title: "Threats", tone: "reviewing", items: swot.threats },
  ];
  return (
    <div className="fc-swot-grid">
      {quads.map((q) => (
        <div className="fc-swot-quad" key={q.title}>
          <div className="fc-swot-quad-title">{q.title}</div>
          {q.items.length === 0 ? (
            <p className="wiz-hint" style={{ margin: 0 }}>None flagged.</p>
          ) : (
            <ul className="fc-swot-list">
              {q.items.map((it) => (
                <li key={it.label} title={it.basis}>
                  <span className={`pill ${q.tone}`}>{it.label}</span>
                  {it.score != null && <span className="fc-swot-score">{Math.round(it.score)}</span>}
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </div>
  );
}

/* ── hotspots — market size × opportunity, bubble = CapEx ──────────────── */
export function HotspotBubbles({
  points,
}: {
  points: { name: string; x: number; y: number; capex?: number | null; tier: string }[];
}) {
  if (points.length < 2) return null;
  const W = 900, H = 340, L = 56, R = 32, T = 30, B = 44;
  // Zoom to the data range (with headroom) so a few clustered sites spread out
  // instead of huddling in one corner of a 0-based axis.
  const xs = points.map((p) => p.x), ys = points.map((p) => p.y);
  const xLo = Math.min(...xs), xHi = Math.max(...xs), yLo = Math.min(...ys), yHi = Math.max(...ys);
  const xPad = (xHi - xLo || xHi || 1) * 0.35, yPad = (yHi - yLo || yHi || 1) * 0.35;
  const x0 = Math.max(0, xLo - xPad), x1 = xHi + xPad;
  const y0 = Math.max(0, yLo - yPad), y1 = yHi + yPad;
  const px = (v: number) => L + ((v - x0) / (x1 - x0 || 1)) * (W - L - R);
  const py = (v: number) => H - B - ((v - y0) / (y1 - y0 || 1)) * (H - B - T);
  const caps = points.map((p) => p.capex).filter((c): c is number => c != null);
  const maxCap = caps.length ? Math.max(...caps) : null;
  const rad = (c: number | null | undefined) => (maxCap && c ? 12 + (c / maxCap) * 26 : 16);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: "block", maxWidth: "100%", overflow: "visible" }}>
      {[0.25, 0.5, 0.75].map((t) => (
        <line key={t} x1={L} x2={W - R} y1={py(y0 + (y1 - y0) * t)} y2={py(y0 + (y1 - y0) * t)}
          stroke={GRID} strokeWidth={1} strokeDasharray="3 5" />
      ))}
      <line x1={L} x2={W - R} y1={H - B} y2={H - B} stroke={GRID} strokeWidth={1.5} />
      <line x1={L} x2={L} y1={T} y2={H - B} stroke={GRID} strokeWidth={1.5} />
      {points.map((s, i) => {
        const c = TIER_FILL[s.tier] ?? SOFT;
        return (
          <g key={s.name + i}>
            <circle cx={px(s.x)} cy={py(s.y)} r={rad(s.capex)} fill={c} opacity={0.38} stroke={c} strokeWidth={1.6}>
              <title>{`${s.name}\nmarket size ${money(s.x, true)}/mo · opportunity ${money(s.y, true)}/mo`}</title>
            </circle>
            <text x={px(s.x)} y={py(s.y) + 4} fontSize={12} fontWeight={700} textAnchor="middle" fill={c}>
              {s.name.length > 14 ? s.name.slice(0, 13) + "…" : s.name}
            </text>
          </g>
        );
      })}
      <text x={L} y={H - 12} fontSize={13} fill={SOFT}>market size / month →</text>
      <text x={L - 4} y={T - 8} fontSize={13} fill={SOFT}>↑ opportunity / month</text>
    </svg>
  );
}

/* ── recommended investment split bars ─────────────────────────────────── */
export function SplitBars({
  rows,
}: {
  rows: { name: string; investment: number; share: number | null; note: string }[];
}) {
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

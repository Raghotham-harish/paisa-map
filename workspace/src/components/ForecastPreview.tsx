import { Link } from "react-router-dom";
import { illustrations } from "../lib/illustrations";

/**
 * Rich empty state for the Forecast page. The real radar / reach-curve /
 * hotspot widgets only render once a project has ≥3 geocoded stores and a paid
 * forecast has run — until then users saw a one-line "not enough data" message
 * and had no idea what they were working toward. This renders the same three
 * views populated with clearly-labelled SAMPLE numbers so the destination is
 * visible, with the upload CTA front and centre.
 */

const SAMPLE_LEVERS = [
  { label: "Avg income /mo", fit: 82 },
  { label: "UPI txn value", fit: 54 },
  { label: "Bank branches /lakh", fit: 71 },
  { label: "Property rate /sqft", fit: 33 },
  { label: "Footfall index", fit: 63 },
];

const SAMPLE_CURVE = [
  { x: 0, y: 0 },
  { x: 0.5, y: 3.1 },
  { x: 1.0, y: 5.4 },
  { x: 1.5, y: 7.0 },
  { x: 2.0, y: 8.0 },
  { x: 2.6, y: 8.6 },
  { x: 3.2, y: 8.9 },
];
const SAMPLE_DIM_FROM = 2.0; // ₹ Cr where diminishing returns start

const SAMPLE_SITES = [
  { name: "Indiranagar", size: 0.9, opp: 0.85, capex: 1.0, tier: "core" },
  { name: "Whitefield", size: 0.75, opp: 0.6, capex: 0.7, tier: "edge" },
  { name: "HSR Layout", size: 0.62, opp: 0.72, capex: 0.55, tier: "core" },
  { name: "Yelahanka", size: 0.4, opp: 0.35, capex: 0.4, tier: "expansion" },
  { name: "Kanakapura Rd", size: 0.3, opp: 0.48, capex: 0.35, tier: "edge" },
];
const TIER_FILL: Record<string, string> = {
  core: "var(--rupee-deep)",
  edge: "#8A5A00",
  expansion: "var(--ink-soft)",
};

function SampleRadar() {
  const n = SAMPLE_LEVERS.length;
  const C = 130;
  const R = 96;
  const ang = (i: number) => -Math.PI / 2 + (i / n) * 2 * Math.PI;
  const pt = (i: number, r: number) => [C + Math.cos(ang(i)) * r, C + Math.sin(ang(i)) * r];
  const poly = SAMPLE_LEVERS.map((l, i) => pt(i, (l.fit / 100) * R).join(",")).join(" ");
  return (
    <svg viewBox={`0 0 ${C * 2} ${C * 2}`} width="100%" style={{ maxWidth: 300 }}>
      {[0.25, 0.5, 0.75, 1].map((t) => (
        <polygon
          key={t}
          points={SAMPLE_LEVERS.map((_, i) => pt(i, R * t).join(",")).join(" ")}
          fill="none"
          stroke="var(--border)"
          strokeWidth={1}
        />
      ))}
      {SAMPLE_LEVERS.map((_, i) => {
        const [ex, ey] = pt(i, R);
        return <line key={i} x1={C} y1={C} x2={ex} y2={ey} stroke="var(--border)" strokeWidth={1} />;
      })}
      <polygon points={poly} fill="var(--rupee)" opacity={0.25} stroke="var(--rupee-deep)" strokeWidth={2} />
      {SAMPLE_LEVERS.map((l, i) => {
        const [lx, ly] = pt(i, R + 16);
        return (
          <text key={i} x={lx} y={ly} fontSize={9.5} textAnchor="middle" fill="var(--ink-soft)">
            {l.label.length > 16 ? l.label.slice(0, 15) + "…" : l.label}
          </text>
        );
      })}
    </svg>
  );
}

function SampleCurve() {
  const W = 560;
  const H = 220;
  const PAD = 40;
  const maxX = SAMPLE_CURVE[SAMPLE_CURVE.length - 1].x;
  const maxY = SAMPLE_CURVE[SAMPLE_CURVE.length - 1].y;
  const x = (v: number) => PAD + (v / maxX) * (W - PAD - 12);
  const y = (v: number) => H - PAD - (v / maxY) * (H - PAD - 12);
  const line = SAMPLE_CURVE.map((p) => `${x(p.x)},${y(p.y)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ maxWidth: W, overflow: "visible" }}>
      <rect x={x(SAMPLE_DIM_FROM)} y={12} width={W - 12 - x(SAMPLE_DIM_FROM)} height={H - PAD - 12} fill="var(--amber)" opacity={0.12} />
      {[0, 0.25, 0.5, 0.75, 1].map((t) => (
        <line key={t} x1={PAD} x2={W - 12} y1={y(maxY * t)} y2={y(maxY * t)} stroke="var(--border)" strokeWidth={1} />
      ))}
      <polyline points={line} fill="none" stroke="var(--rupee)" strokeWidth={2.5} />
      {SAMPLE_CURVE.map((p, i) => (
        <circle key={i} cx={x(p.x)} cy={y(p.y)} r={2.6} fill="var(--rupee-deep)" />
      ))}
      <text x={x(SAMPLE_DIM_FROM) + 6} y={H - PAD - 6} fontSize={10} fill="#8A5A00">
        diminishing returns →
      </text>
      <text x={PAD} y={H - 8} fontSize={10} fill="var(--ink-soft)">₹0</text>
      <text x={W - 12} y={H - 8} fontSize={10} textAnchor="end" fill="var(--ink-soft)">₹3.2 Cr invested</text>
      <text x={PAD} y={20} fontSize={10} fill="var(--ink-soft)">₹8.9 L/mo peak</text>
    </svg>
  );
}

function SampleBubbles() {
  const W = 480;
  const H = 220;
  const PAD = 40;
  const x = (v: number) => PAD + v * (W - PAD - 14);
  const y = (v: number) => H - PAD - v * (H - PAD - 14);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ maxWidth: W, overflow: "visible" }}>
      <line x1={PAD} x2={W - 12} y1={H - PAD} y2={H - PAD} stroke="var(--border)" strokeWidth={1} />
      <line x1={PAD} x2={PAD} y1={22} y2={H - PAD} stroke="var(--border)" strokeWidth={1} />
      {SAMPLE_SITES.map((s) => (
        <circle
          key={s.name}
          cx={x(s.size)}
          cy={y(s.opp)}
          r={4 + s.capex * 10}
          fill={TIER_FILL[s.tier]}
          opacity={0.55}
          stroke={TIER_FILL[s.tier]}
          strokeWidth={1.2}
        >
          <title>{s.name}</title>
        </circle>
      ))}
      <text x={PAD} y={H - 8} fontSize={10} fill="var(--ink-soft)">market size →</text>
      <text x={PAD} y={14} fontSize={10} fill="var(--ink-soft)">↑ opportunity</text>
    </svg>
  );
}

export function ForecastPreview({ detail }: { detail?: string | null }) {
  return (
    <>
      <div className="card forecast-cta" style={{ marginBottom: 20 }}>
        <img className="forecast-cta-art" src={illustrations.folderFiles} alt="" aria-hidden="true" />
        <div className="forecast-cta-body">
          <div className="kicker">Forecast needs your store data</div>
          <p style={{ fontSize: 13.5, lineHeight: 1.6, margin: "6px 0 12px", maxWidth: 560 }}>
            {detail ||
              "Upload at least 3 of your existing stores (with monthly revenue). PaisaMap calibrates the model on how your own locations actually perform, then projects where the next stores should go. The charts below are illustrative samples."}
          </p>
          <Link className="btn" to="/customer-data">Upload store data</Link>
        </div>
      </div>

      <div className="preview-wrap">
        <span className="preview-badge">Sample</span>

        <div className="fc-grid">
          <div className="fc-main">
            <div className="fc-row2">
              <div className="card">
                <div className="kicker">Location fit by lever — top 3 candidates</div>
                <SampleRadar />
                <p className="wiz-hint">
                  Each spoke = how strongly that signal correlates with your stores' revenue. Weak spokes = a signal
                  that doesn't predict your sales.
                </p>
              </div>
              <div className="card">
                <div className="kicker">Hotspots — market size vs opportunity</div>
                <SampleBubbles />
                <p className="wiz-hint">
                  Bubble = est. CapEx · <span style={{ color: "var(--rupee-deep)" }}>●</span> core ·{" "}
                  <span style={{ color: "#8A5A00" }}>●</span> edge · <span style={{ color: "var(--ink-soft)" }}>○</span> expansion
                </p>
              </div>
            </div>

            <div className="card">
              <div className="fc-card-head">
                <span className="kicker">Investment → projected reachable revenue</span>
                <span className="wiz-hint">diminishing returns past the sweet spot</span>
              </div>
              <SampleCurve />
              <div className="callout-sweetspot">
                <b>Sweet spot ≈ ₹2.0 Cr.</b> Beyond this, each extra rupee buys materially less revenue.
              </div>
            </div>

            <div className="card">
              <div className="kicker">Recommended investment split</div>
              <div className="fc-split">
                {[
                  { name: "Whitefield", pct: 100, cr: "₹82 L", note: "+₹2.4 Cr reach" },
                  { name: "Indiranagar", pct: 78, cr: "₹64 L", note: "+₹1.9 Cr reach" },
                  { name: "HSR Layout", pct: 66, cr: "₹54 L", note: "+₹1.5 Cr reach" },
                ].map((s) => (
                  <div className="fc-split-row" key={s.name}>
                    <span className="fc-split-name">{s.name}</span>
                    <span className="fc-split-track"><span style={{ width: `${s.pct}%` }} /></span>
                    <span className="mono fc-split-val">{s.cr}</span>
                    <span className="fc-split-note">{s.note}</span>
                  </div>
                ))}
              </div>
              <div className="fc-nudge">
                <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="#7A5B12" strokeWidth="1.5" style={{ flex: "none", marginTop: 1 }}>
                  <path d="M8 1l2 4 4 .6-3 3 .7 4L8 14.6 4.3 16.6 5 12.6 2 9.6l4-.6z" />
                </svg>
                <div><b>Nudge:</b> move ₹15 L from Indiranagar to Whitefield → <b>+₹40 L/mo</b>, payback 14 → 13 mo.</div>
              </div>
            </div>
          </div>

          <div className="fc-rail">
            <div className="card">
              <div className="kicker">At ₹2.0 Cr</div>
              <div className="fc-rail-stats">
                <div><div className="fc-rail-lab">Added revenue / mo</div><div className="mono fc-rail-val">₹8.0 L</div></div>
                <div><div className="fc-rail-lab">Segment market capture</div><div className="mono fc-rail-val">4.2% → 6.8%</div></div>
                <div><div className="fc-rail-lab">New stores</div><div className="mono fc-rail-val">4</div></div>
                <div><div className="fc-rail-lab">Blended payback</div><div className="mono fc-rail-val">14 mo</div></div>
              </div>
            </div>
            <div className="card">
              <div className="kicker">Forecast confidence</div>
              <span className="pill reviewing" style={{ marginTop: 8, display: "inline-block" }}>medium</span>
              <ul className="fc-rail-bullets">
                <li>Fitted regression · R² 0.58</li>
                <li>±18% confidence band</li>
                <li>Reach is modelled household spend, not booked sales</li>
              </ul>
            </div>
            <div className="card">
              <div className="kicker">Levers you're under-using</div>
              <div className="fc-lever-list">
                {SAMPLE_LEVERS.map((l) => {
                  const word = l.fit < 40 ? "weak" : l.fit < 60 ? "partial" : "strong";
                  const tone = l.fit < 40 ? "rejected" : l.fit < 60 ? "reviewing" : "delta-pos";
                  return (
                    <div className="fc-lever" key={l.label}>
                      <span>{l.label}</span>
                      <span className={`pill ${tone}`}>{word}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

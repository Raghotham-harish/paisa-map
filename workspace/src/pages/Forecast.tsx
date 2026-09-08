import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError, Forecast as ForecastResult, ForecastResponse, PricingConfig, Project, api } from "../lib/api";
import { EmptyState } from "../components/EmptyState";
import { ForecastPreview } from "../components/ForecastPreview";

function money(n: number | null | undefined, compact = false) {
  if (n == null) return "—";
  if (compact) {
    if (Math.abs(n) >= 1e7) return `₹${(n / 1e7).toFixed(2)} Cr`;
    if (Math.abs(n) >= 1e5) return `₹${(n / 1e5).toFixed(1)} L`;
  }
  return `₹${Math.round(n).toLocaleString("en-IN")}`;
}

const CONF_CLASS: Record<string, string> = { high: "delta-pos", medium: "reviewing", low: "rejected" };

/* ── reach curve ─────────────────────────────────────────────────────────── */
function ReachCurve({ f }: { f: ForecastResult }) {
  const pts = f.reach_curve.points;
  if (pts.length < 2) return null;
  const W = 560, H = 240, PAD = 44;
  const maxX = pts[pts.length - 1].investment || 1;
  const maxY = pts[pts.length - 1].monthly_revenue || 1;
  const x = (v: number) => PAD + (v / maxX) * (W - PAD - 12);
  const y = (v: number) => H - PAD - (v / maxY) * (H - PAD - 12);
  const line = pts.map((p) => `${x(p.investment)},${y(p.monthly_revenue)}`).join(" ");
  const area = `${x(0)},${y(0)} ${line} ${x(pts[pts.length - 1].investment)},${y(0)}`;
  const dim = f.reach_curve.diminishing_returns_from;
  const budgetX = f.budget <= maxX ? x(f.budget) : null;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ maxWidth: W, overflow: "visible" }}>
      {dim != null && dim <= maxX && (
        <rect x={x(dim)} y={12} width={W - 12 - x(dim)} height={H - PAD - 12}
          fill="var(--amber)" opacity={0.12} />
      )}
      {[0, 0.25, 0.5, 0.75, 1].map((t) => (
        <line key={t} x1={PAD} x2={W - 12} y1={y(maxY * t)} y2={y(maxY * t)} stroke="var(--border)" strokeWidth={1} />
      ))}
      <polygon points={area} fill="var(--rupee)" opacity={0.1} />
      <polyline points={line} fill="none" stroke="var(--rupee)" strokeWidth={2.5} />
      {pts.map((p, i) => (
        <circle key={i} cx={x(p.investment)} cy={y(p.monthly_revenue)} r={2.6} fill="var(--rupee-deep)" />
      ))}
      {budgetX != null && (
        <>
          <line x1={budgetX} x2={budgetX} y1={12} y2={H - PAD} stroke="var(--ink)" strokeWidth={1.4} strokeDasharray="4 3" />
          <text x={budgetX} y={H - PAD + 14} fontSize={10} textAnchor="middle" fill="var(--ink-soft)">your budget</text>
        </>
      )}
      {dim != null && dim <= maxX && (
        <text x={x(dim) + 6} y={H - PAD - 6} fontSize={10} fill="#8A5A00">diminishing returns →</text>
      )}
      <text x={PAD} y={H - 8} fontSize={10} fill="var(--ink-soft)">₹0</text>
      <text x={W - 12} y={H - 8} fontSize={10} textAnchor="end" fill="var(--ink-soft)">{money(maxX, true)} invested</text>
      <text x={PAD} y={20} fontSize={10} fill="var(--ink-soft)">{money(maxY, true)}/mo peak</text>
    </svg>
  );
}

/* ── lever-fit radar — historical polygon (your stores) + up to 3 candidate
   overlays (each recommended site's own percentile on the same levers) ── */
const CANDIDATE_COLORS = ["var(--flame)", "var(--amber)", "#2A81CB"];

function Radar({
  levers,
  candidates = [],
}: {
  levers: ForecastResult["lever_fit_radar"];
  candidates?: { label: string; values: (number | null)[] }[];
}) {
  const n = levers.length;
  if (n < 3) {
    return (
      <ul className="list">
        {levers.map((l) => (
          <li key={l.signal}>
            <div><div className="primary">{l.label}</div>
              <div className="secondary">{l.sample_size} stores{l.in_top_drivers ? " · top revenue driver" : ""}</div></div>
            <span className={`pill ${l.lever_fit != null && l.lever_fit >= 40 ? "delta-pos" : "shortlist"}`}>
              {l.lever_fit != null ? `${l.lever_fit}/100 fit` : "no signal"}
            </span>
          </li>
        ))}
      </ul>
    );
  }
  const C = 130, R = 96;
  const ang = (i: number) => -Math.PI / 2 + (i / n) * 2 * Math.PI;
  const pt = (i: number, r: number) => [C + Math.cos(ang(i)) * r, C + Math.sin(ang(i)) * r];
  const polyFor = (values: (number | null)[]) =>
    values.map((v, i) => pt(i, (Math.max(0, Math.min(100, v ?? 0)) / 100) * R).join(",")).join(" ");
  const poly = polyFor(levers.map((l) => l.lever_fit));
  return (
    <>
      <svg viewBox={`0 0 ${C * 2} ${C * 2}`} width="100%" style={{ maxWidth: 300 }}>
        {[0.25, 0.5, 0.75, 1].map((t) => (
          <polygon key={t} points={levers.map((_, i) => pt(i, R * t).join(",")).join(" ")}
            fill="none" stroke="var(--border)" strokeWidth={1} />
        ))}
        {levers.map((_, i) => {
          const [ex, ey] = pt(i, R);
          return <line key={i} x1={C} y1={C} x2={ex} y2={ey} stroke="var(--border)" strokeWidth={1} />;
        })}
        <polygon points={poly} fill="var(--rupee)" opacity={0.25} stroke="var(--rupee-deep)" strokeWidth={2} />
        {candidates.map((cand, ci) => (
          <polygon key={ci} points={polyFor(cand.values)} fill="none"
            stroke={CANDIDATE_COLORS[ci % CANDIDATE_COLORS.length]} strokeWidth={1.6} strokeDasharray="4 2" />
        ))}
        {levers.map((l, i) => {
          const [lx, ly] = pt(i, R + 16);
          return (
            <text key={i} x={lx} y={ly} fontSize={9.5} textAnchor="middle" fill="var(--ink-soft)">
              {l.label.length > 16 ? l.label.slice(0, 15) + "…" : l.label}
            </text>
          );
        })}
      </svg>
      {candidates.length > 0 && (
        <div className="radar-legend">
          <span className="radar-legend-item"><i style={{ background: "var(--rupee-deep)" }} />Your stores</span>
          {candidates.map((cand, ci) => (
            <span className="radar-legend-item" key={ci}>
              <i style={{ background: CANDIDATE_COLORS[ci % CANDIDATE_COLORS.length] }} />
              {cand.label}
            </span>
          ))}
        </div>
      )}
    </>
  );
}

/* ── hotspot bubble chart — market size (x) vs opportunity (y), bubble =
   payback speed, colour = market tier derived from the same national PPI
   percentile the model already computes per site ── */
function tierOf(ppiPercentile: number): "core" | "edge" | "expansion" {
  if (ppiPercentile >= 200 / 3) return "core";
  if (ppiPercentile >= 100 / 3) return "edge";
  return "expansion";
}
const TIER_FILL: Record<string, string> = { core: "var(--rupee-deep)", edge: "#8A5A00", expansion: "var(--ink-soft)" };

function HotspotBubbles({ sites }: { sites: ForecastResult["recommended_portfolio"]["sites"] }) {
  const pts = sites.filter((s) => s.reach_gross > 0 && s.monthly_revenue > 0);
  if (pts.length < 2) return null;
  const W = 480, H = 220, PAD = 40;
  const maxX = Math.max(...pts.map((s) => s.reach_gross));
  const maxY = Math.max(...pts.map((s) => s.monthly_revenue));
  const x = (v: number) => PAD + (v / maxX) * (W - PAD - 14);
  const y = (v: number) => H - PAD - (v / maxY) * (H - PAD - 14);
  const capexes = pts.map((s) => s.capex).filter((c): c is number => c != null);
  const maxCapex = capexes.length ? Math.max(...capexes) : null;
  const radius = (capex: number | null) => (maxCapex && capex ? 4 + (capex / maxCapex) * 10 : 6);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ maxWidth: W, overflow: "visible" }}>
      <line x1={PAD} x2={W - 12} y1={H - PAD} y2={H - PAD} stroke="var(--border)" strokeWidth={1} />
      <line x1={PAD} x2={PAD} y1={22} y2={H - PAD} stroke="var(--border)" strokeWidth={1} />
      {pts.map((s) => (
        <circle key={s.pincode} cx={x(s.reach_gross)} cy={y(s.monthly_revenue)} r={radius(s.capex)}
          fill={TIER_FILL[tierOf(s.ppi_percentile)]} opacity={0.55} stroke={TIER_FILL[tierOf(s.ppi_percentile)]} strokeWidth={1.2}>
          <title>{`${s.name} (${s.pincode})\nMarket size ${money(s.reach_gross, true)}/mo\nOpportunity ${money(s.monthly_revenue, true)}/mo`}</title>
        </circle>
      ))}
      <text x={PAD} y={H - 8} fontSize={10} fill="var(--ink-soft)">market size →</text>
      <text x={PAD} y={14} fontSize={10} fill="var(--ink-soft)">↑ opportunity</text>
    </svg>
  );
}

export default function Forecast() {
  const [params, setParams] = useSearchParams();
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [projectId, setProjectId] = useState<number | null>(null);
  const [budget, setBudget] = useState("");
  const [pricing, setPricing] = useState<PricingConfig | null>(null);
  const [result, setResult] = useState<ForecastResponse | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getPricing().then(setPricing).catch(() => setPricing(null));
    api.listProjects().then((d) => {
      setProjects(d.projects);
      const fromUrl = Number(params.get("project_id"));
      const initial = d.projects.find((p) => p.id === fromUrl) ?? d.projects[0];
      if (initial) {
        setProjectId(initial.id);
        if (initial.total_investment != null) setBudget(String(initial.total_investment));
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const project = useMemo(() => projects?.find((p) => p.id === projectId) ?? null, [projects, projectId]);
  const cost = pricing?.credit_costs?.forecast;

  const onProject = (id: number) => {
    setProjectId(id);
    setResult(null);
    setError(null);
    const p = projects?.find((x) => x.id === id);
    setBudget(p?.total_investment != null ? String(p.total_investment) : "");
    setParams((prev) => { const n = new URLSearchParams(prev); n.set("project_id", String(id)); return n; }, { replace: true });
  };

  const run = async () => {
    if (projectId == null) return;
    setRunning(true);
    setError(null);
    try {
      setResult(await api.getForecast(projectId, Number(budget) || undefined));
    } catch (e) {
      if (e instanceof ApiError && e.body?.error === "insufficient_credits") {
        setError(`Not enough credits — this forecast costs ${e.body.required}, you have ${e.body.balance}.`);
      } else if (e instanceof ApiError && e.body?.detail) {
        setError(String(e.body.detail));
      } else {
        setError("Couldn't run the forecast — try again.");
      }
    } finally {
      setRunning(false);
    }
  };

  const f = result && result.sufficient_data ? result : null;
  const mc = f?.market_capture;

  return (
    <>
      <h1 className="page-title">Forecast</h1>
      <p className="page-sub">
        Investment → reachable-revenue, calibrated on your own stores. A modelled projection, not a guarantee —
        see the assumptions at the bottom.
      </p>

      {projects === null ? (
        <div className="loading">Loading…</div>
      ) : projects.length === 0 ? (
        <EmptyState icon="📈" title="Forecast needs a project"
          description="Create a project, upload your store data, then forecast where the next stores should go."
          primaryAction={{ label: "Create a project", to: "/projects/new" }} />
      ) : (
        <>
          <div className="card" style={{ marginBottom: 24 }}>
            <div style={{ display: "flex", gap: 12, alignItems: "flex-end", flexWrap: "wrap" }}>
              <label style={{ flex: "1 1 200px" }}>
                Project
                <select value={projectId ?? ""} onChange={(e) => onProject(Number(e.target.value))}>
                  {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
              </label>
              <label style={{ flex: "0 0 200px" }}>
                Budget (₹)
                <input type="number" min="1" placeholder="e.g. 20000000" value={budget}
                  onChange={(e) => setBudget(e.target.value)} />
              </label>
              <button className="btn" disabled={running || !projectId} onClick={run}>
                {running ? "Running…" : "Run forecast"}
              </button>
              {cost != null && <span className="wiz-hint" style={{ paddingBottom: 8 }}>{cost} credits</span>}
            </div>
            {project && (project.gross_margin_pct == null || project.revenue_period == null) && (
              <p className="wiz-hint" style={{ marginTop: 10 }}>
                Set this project's <Link to={`/projects/new?edit=${project.id}`}>gross margin & revenue period</Link> for
                a sharper payback estimate — defaults are assumed otherwise.
              </p>
            )}
            {error && <p style={{ color: "var(--flame)", fontSize: 13, marginTop: 10 }}>{error}</p>}
          </div>

          {!f && (
            <ForecastPreview detail={result && !result.sufficient_data ? result.detail : null} />
          )}

          {f && (
            <>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 18 }}>
                <span className={`pill ${CONF_CLASS[f.confidence]}`}>{f.confidence} confidence</span>
                <span className="pill shortlist">
                  capture: {f.capture_model.method === "regression" ? "fitted regression" : "your stores' median"}
                  {f.capture_model.r2 != null ? ` · R² ${f.capture_model.r2}` : ""}
                </span>
                <span className="pill shortlist">± {f.capture_model.confidence_band_pct}% band</span>
                <span className="pill shortlist">{f.candidates_considered.toLocaleString("en-IN")} candidate pincodes</span>
              </div>

              <div className="stat-row">
                <div className="stat-tile"><div className="label">Recommended stores</div>
                  <div className="value">{f.recommended_portfolio.stores}</div></div>
                <div className="stat-tile"><div className="label">Investment used</div>
                  <div className="value">{money(f.recommended_portfolio.investment, true)}</div></div>
                <div className="stat-tile"><div className="label">Added revenue /mo</div>
                  <div className="value">{money(f.recommended_portfolio.monthly_revenue, true)}</div></div>
                <div className="stat-tile"><div className="label">Gross profit /mo</div>
                  <div className="value">{money(f.recommended_portfolio.monthly_gross_profit, true)}</div></div>
                <div className="stat-tile"><div className="label">Payback</div>
                  <div className="value">{f.recommended_portfolio.payback_months != null ? `${f.recommended_portfolio.payback_months} mo` : "—"}</div></div>
              </div>

              <div className="card" style={{ marginBottom: 24 }}>
                <div className="kicker">Investment → added monthly revenue</div>
                <ReachCurve f={f} />
                <p className="wiz-hint" style={{ marginTop: 6 }}>{f.reach_curve.note}</p>
                {f.reach_curve.diminishing_returns_from != null && (
                  <div className="callout-sweetspot">
                    <b>Sweet spot: {money(f.reach_curve.diminishing_returns_from, true)}.</b>{" "}
                    Beyond this, each additional rupee buys materially less revenue than the sites already ahead of it.
                  </div>
                )}
              </div>

              {f.nudge && (
                <div className="card" style={{ marginBottom: 24 }}>
                  <div className="kicker">The nudge</div>
                  <p style={{ fontSize: 13.5, lineHeight: 1.6, margin: "8px 0 0" }}>
                    Move {money(f.nudge.from_capex, true)} out of <b>{f.nudge.from_name}</b> and into{" "}
                    <b>{f.nudge.to_name}</b> instead — {f.nudge.note}
                  </p>
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 10 }}>
                    <span className="pill delta-pos">+{money(f.nudge.monthly_revenue_delta, true)}/mo</span>
                    {f.nudge.old_payback_months != null && f.nudge.new_payback_months != null && (
                      <span className="pill shortlist">
                        payback {f.nudge.old_payback_months} → {f.nudge.new_payback_months} mo
                      </span>
                    )}
                  </div>
                </div>
              )}

              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(280px,1fr))", gap: 20, marginBottom: 24 }}>
                <div className="card">
                  <div className="kicker">Do your chosen signals move revenue?</div>
                  <Radar
                    levers={f.lever_fit_radar}
                    candidates={f.recommended_portfolio.sites
                      .filter((s) => s.lever_percentiles)
                      .slice(0, 3)
                      .map((s) => ({
                        label: `${s.name} (${s.pincode})`,
                        values: (s.lever_percentiles ?? []).map((lp) => lp.percentile),
                      }))}
                  />
                  <p className="wiz-hint">Correlation of each lever with your stores' revenue (solid) vs. the top 3 recommended sites' own percentile on each (dashed). Weak spokes = you're tracking a signal that doesn't predict your sales.</p>
                  {f.lever_fit_radar.some((l) => l.lever_fit != null && l.lever_fit < 40) && (
                    <div style={{ marginTop: 10 }}>
                      <div className="wiz-hint" style={{ fontWeight: 600 }}>Levers you're under-using</div>
                      <ul className="lp-drivers" style={{ display: "flex", flexWrap: "wrap", gap: 6, listStyle: "none", padding: 0, marginTop: 6 }}>
                        {f.lever_fit_radar
                          .filter((l) => l.lever_fit != null && l.lever_fit < 40)
                          .sort((a, b) => (a.lever_fit ?? 0) - (b.lever_fit ?? 0))
                          .map((l) => (
                            <li className="lp-driver" key={l.signal}>{l.label} · {l.lever_fit}/100</li>
                          ))}
                      </ul>
                    </div>
                  )}
                </div>

                <div className="card">
                  <div className="kicker">Recommended ₹ split</div>
                  {f.investment_split.length === 0 ? (
                    <p className="wiz-hint">No sites fit the budget.</p>
                  ) : (
                    <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 8 }}>
                      {f.investment_split.map((s) => (
                        <div key={s.state}>
                          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12.5 }}>
                            <span><b>{s.state}</b> · {s.stores} store{s.stores > 1 ? "s" : ""}</span>
                            <span>{money(s.investment, true)}{s.investment_share_pct != null ? ` (${s.investment_share_pct}%)` : ""}</span>
                          </div>
                          <div style={{ height: 8, background: "var(--paper-3)", borderRadius: 4, marginTop: 4 }}>
                            <div style={{ width: `${s.investment_share_pct ?? 0}%`, height: "100%", background: "var(--rupee)", borderRadius: 4 }} />
                          </div>
                          <div className="wiz-hint">+{money(s.monthly_revenue, true)}/mo</div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>

              {mc && (
                <div className="card" style={{ marginBottom: 24 }}>
                  <div className="kicker">Market capture</div>
                  <div style={{ display: "flex", gap: 24, flexWrap: "wrap", marginTop: 8, fontSize: 13 }}>
                    <div>Now: <b>{mc.current_capture_pct}%</b> of {money(mc.addressable_monthly_spend, true)}/mo addressable spend</div>
                    <div>After: <b style={{ color: "var(--rupee-deep)" }}>{mc.projected_capture_pct}%</b> ({money(mc.projected_monthly_revenue, true)}/mo)</div>
                  </div>
                  <p className="wiz-hint" style={{ marginTop: 6 }}>{mc.note}</p>
                </div>
              )}

              {f.recommended_portfolio.sites.length > 0 && (
                <div className="card" style={{ marginBottom: 24 }}>
                  <div className="kicker">Hotspots — market size vs. opportunity</div>
                  <HotspotBubbles sites={f.recommended_portfolio.sites} />
                  <p className="wiz-hint" style={{ marginTop: 6 }}>
                    Bubble size = estimated CapEx. <span style={{ color: "var(--rupee-deep)" }}>●</span> core ·{" "}
                    <span style={{ color: "#8A5A00" }}>●</span> edge · <span style={{ color: "var(--ink-soft)" }}>●</span> expansion market.
                  </p>
                </div>
              )}

              {f.recommended_portfolio.sites.length > 0 && (
                <div className="card">
                  <div className="kicker">Where the model would put stores</div>
                  <ul className="list" style={{ marginTop: 10 }}>
                    {f.recommended_portfolio.sites.map((s) => (
                      <li key={s.pincode}>
                        <div>
                          <div className="primary">{s.name} · {s.pincode}</div>
                          <div className="secondary">
                            {s.state} · PPI pct {s.ppi_percentile}
                            {s.capex != null ? ` · CapEx ${money(s.capex, true)}` : ""}
                            {s.cannibalisation_discount < 0.98 ? ` · overlap −${Math.round((1 - s.cannibalisation_discount) * 100)}%` : ""}
                          </div>
                        </div>
                        <div className="row-actions">
                          <span className="pill delta-pos">+{money(s.monthly_revenue, true)}/mo</span>
                          {s.payback_months != null && <span className="pill shortlist">{s.payback_months} mo payback</span>}
                        </div>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              <details style={{ marginTop: 20, fontSize: 12.5, color: "var(--ink-soft)" }}>
                <summary style={{ cursor: "pointer", fontWeight: 600 }}>Model assumptions</summary>
                <ul style={{ marginTop: 8, lineHeight: 1.6 }}>
                  {Object.entries(f.assumptions).map(([k, v]) => (
                    <li key={k}><b>{k.replace(/_/g, " ")}:</b> {String(v)}</li>
                  ))}
                  <li><b>capex basis:</b> {f.capex_basis ?? "unavailable — showing top sites unconstrained by budget"}</li>
                </ul>
              </details>
            </>
          )}
        </>
      )}
    </>
  );
}

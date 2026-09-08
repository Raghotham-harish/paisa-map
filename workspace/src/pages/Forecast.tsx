import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError, Forecast as ForecastResult, ForecastResponse, PricingConfig, Project, api } from "../lib/api";
import { EmptyState } from "../components/EmptyState";
import { ForecastPreview } from "../components/ForecastPreview";
import { illustrations } from "../lib/illustrations";
import { HotspotBubbles, KpiStrip, LeverLines, money, ReachCurve, SplitBars } from "../components/forecastCharts";

const CONF_CLASS: Record<string, string> = { high: "delta-pos", medium: "reviewing", low: "rejected" };

export default function Forecast() {
  const [params, setParams] = useSearchParams();
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [projectId, setProjectId] = useState<number | null>(null);
  const [budget, setBudget] = useState("");
  const [pricing, setPricing] = useState<PricingConfig | null>(null);
  const [result, setResult] = useState<ForecastResponse | null>(null);
  const [resultBudget, setResultBudget] = useState<number | null>(null);
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
    setResultBudget(null);
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
      const b = Number(budget) || undefined;
      setResult(await api.getForecast(projectId, b));
      setResultBudget(b ?? null);
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
        <EmptyState illustration={illustrations.dataTrends} title="Forecast needs a project"
          description="Create a project, upload your store data, then forecast where the next stores should go."
          primaryAction={{ label: "Create a project", to: "/projects/new" }} />
      ) : (
        <>
          <div className="card fc-controls" style={{ marginBottom: 24 }}>
            <div className="fc-controls-row">
              <label className="fc-control-project">
                Project
                <select value={projectId ?? ""} onChange={(e) => onProject(Number(e.target.value))}>
                  {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
              </label>
              <label className="fc-control-budget">
                Budget (₹)
                <input type="number" min="1" placeholder="e.g. 20000000" value={budget}
                  onChange={(e) => setBudget(e.target.value)} />
              </label>
              <button className="btn" disabled={running || !projectId} onClick={run}>
                {running ? "Running…" : result ? "Re-run forecast" : "Run forecast"}
              </button>
              {cost != null && <span className="wiz-hint fc-control-cost">{cost} credits</span>}
            </div>
            {result && (Number(budget) || null) !== resultBudget && (
              <p className="fc-stale">
                Showing the {money(resultBudget ?? 0, true)} forecast.{" "}
                <button type="button" className="fc-stale-link" onClick={run} disabled={running}>
                  Re-run for {budget ? money(Number(budget), true) : "the new budget"}
                </button>
              </p>
            )}
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
              <div className="fc-headline">
                <div>
                  <div className="fc-crumb"><b>{project?.name}</b> <span>/ Forecast</span></div>
                  <h1 className="fc-h1">{money(f.budget, true)} &rarr; where it works hardest</h1>
                </div>
                <div className="fc-headline-pills">
                  {project?.time_horizon_months != null && (
                    <span className="pill shortlist">{project.time_horizon_months}-mo horizon</span>
                  )}
                  <span className={`pill ${CONF_CLASS[f.confidence]}`}>{f.confidence} confidence</span>
                </div>
              </div>

              <KpiStrip
                items={[
                  { label: "Added revenue / mo", value: money(f.recommended_portfolio.monthly_revenue, true) },
                  ...(mc ? [{ label: "Segment market capture", value: `${mc.current_capture_pct}% → ${mc.projected_capture_pct}%` }] : []),
                  { label: "New stores", value: String(f.recommended_portfolio.stores) },
                  { label: "Blended payback", value: f.recommended_portfolio.payback_months != null ? `${f.recommended_portfolio.payback_months} mo` : "—" },
                  { label: "Candidates weighed", value: f.candidates_considered.toLocaleString("en-IN") },
                ]}
              />

              <div className="card fc-chart-card">
                <div className="fc-card-head">
                  <span className="kicker">Investment &rarr; projected reachable revenue</span>
                  <span className="wiz-hint">the dashed line is your budget</span>
                </div>
                <ReachCurve
                  points={f.reach_curve.points.map((p) => ({ investment: p.investment, monthly_revenue: p.monthly_revenue }))}
                  budget={f.budget}
                  dimFrom={f.reach_curve.diminishing_returns_from}
                />
                {f.reach_curve.diminishing_returns_from != null && (
                  <div className="callout-sweetspot">
                    <b>Sweet spot &asymp; {money(f.reach_curve.diminishing_returns_from, true)}.</b>{" "}
                    Beyond this, each extra rupee buys materially less than the sites already ahead of it.
                  </div>
                )}
              </div>

              <div className="card fc-chart-card">
                <div className="kicker">Does each signal actually move revenue?</div>
                <LeverLines
                  levers={f.lever_fit_radar.map((l) => ({ label: l.label, fit: l.lever_fit }))}
                  candidates={f.recommended_portfolio.sites
                    .filter((s) => s.lever_percentiles)
                    .slice(0, 3)
                    .map((s) => ({ label: s.name, values: (s.lever_percentiles ?? []).map((lp) => lp.percentile) }))}
                />
              </div>

              {f.recommended_portfolio.sites.length >= 2 && (
                <div className="card fc-chart-card">
                  <div className="fc-card-head">
                    <span className="kicker">Hotspots &mdash; market size vs opportunity</span>
                    <span className="wiz-hint">
                      bubble = est. CapEx &middot; <span style={{ color: "var(--rupee-deep)" }}>&#9679;</span> core &middot;{" "}
                      <span style={{ color: "#8A5A00" }}>&#9679;</span> edge &middot;{" "}
                      <span style={{ color: "var(--ink-soft)" }}>&#9679;</span> expansion
                    </span>
                  </div>
                  <HotspotBubbles
                    points={f.recommended_portfolio.sites.map((s) => ({
                      name: s.name,
                      x: s.reach_gross,
                      y: s.monthly_revenue,
                      capex: s.capex,
                      tier: s.ppi_percentile >= 200 / 3 ? "core" : s.ppi_percentile >= 100 / 3 ? "edge" : "expansion",
                    }))}
                  />
                </div>
              )}

              <div className="card fc-chart-card">
                <div className="kicker">Recommended investment split</div>
                {f.investment_split.length === 0 ? (
                  <p className="wiz-hint" style={{ marginTop: 8 }}>No sites fit the budget.</p>
                ) : (
                  <SplitBars
                    rows={f.investment_split.map((s) => ({
                      name: s.state,
                      investment: s.investment,
                      share: s.investment_share_pct,
                      note: `+${money(s.monthly_revenue, true)}/mo`,
                    }))}
                  />
                )}
                {f.nudge && (
                  <div className="fc-nudge">
                    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="#7A5B12" strokeWidth="1.5" style={{ flex: "none", marginTop: 1 }}>
                      <path d="M8 1l2 4 4 .6-3 3 .7 4L8 14.6 4.3 16.6 5 12.6 2 9.6l4-.6z" />
                    </svg>
                    <div>
                      <b>Nudge:</b> move {money(f.nudge.from_capex, true)} from <b>{f.nudge.from_name}</b> to{" "}
                      <b>{f.nudge.to_name}</b> &rarr; <b>+{money(f.nudge.monthly_revenue_delta, true)}/mo</b>
                      {f.nudge.old_payback_months != null && f.nudge.new_payback_months != null
                        ? `, payback ${f.nudge.old_payback_months} → ${f.nudge.new_payback_months} mo.`
                        : "."}{" "}
                      {f.nudge.note}
                    </div>
                  </div>
                )}
              </div>

              <div className="fc-two">
                <div className="card">
                  <div className="kicker">Forecast confidence</div>
                  <span className={`pill ${CONF_CLASS[f.confidence]}`} style={{ marginTop: 8, display: "inline-block" }}>
                    {f.confidence}
                  </span>
                  <ul className="fc-rail-bullets">
                    <li>
                      {f.capture_model.method === "regression"
                        ? `Fitted regression · R² ${f.capture_model.r2 ?? "—"}`
                        : "Calibrated on your stores' median capture"}
                    </li>
                    <li>&plusmn;{f.capture_model.confidence_band_pct}% confidence band</li>
                    <li>{f.candidates_considered.toLocaleString("en-IN")} candidate pincodes considered</li>
                    <li>Reach is modelled household spend, not booked sales</li>
                  </ul>
                </div>

                {f.lever_fit_radar.length > 0 && (
                  <div className="card">
                    <div className="kicker">Levers you're under-using</div>
                    <div className="fc-lever-list">
                      {[...f.lever_fit_radar]
                        .sort((a, b) => (a.lever_fit ?? 999) - (b.lever_fit ?? 999))
                        .map((l) => {
                          const fit = l.lever_fit;
                          const tone = fit == null ? "shortlist" : fit < 40 ? "rejected" : fit < 60 ? "reviewing" : "delta-pos";
                          const word = fit == null ? "no signal" : fit < 40 ? "weak" : fit < 60 ? "partial" : "strong";
                          return (
                            <div className="fc-lever" key={l.signal}>
                              <span>{l.label}</span>
                              <span className={`pill ${tone}`}>{word}</span>
                            </div>
                          );
                        })}
                    </div>
                  </div>
                )}
              </div>

              {f.recommended_portfolio.sites.length > 0 && (
                <div className="card">
                  <div className="kicker">Where the model would put stores</div>
                  <ul className="list" style={{ marginTop: 10 }}>
                    {f.recommended_portfolio.sites.map((s) => (
                      <li key={s.pincode}>
                        <div>
                          <div className="primary">{s.name} &middot; {s.pincode}</div>
                          <div className="secondary">
                            {s.state} &middot; PPI pct {s.ppi_percentile}
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

import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, Forecast as ForecastResult, ForecastResponse, ForecastSite, PricingConfig, api } from "../lib/api";
import { EmptyState } from "../components/EmptyState";
import { ForecastPreview } from "../components/ForecastPreview";
import { DataList, DataRow } from "../components/DataList";
import { AsyncBoundary } from "../components/AsyncBoundary";
import { StatChip } from "../components/StatChip";
import { Pill } from "../components/Pill";
import { MicroBar } from "../components/MicroViz";
import { RUPEE } from "../components/chartTheme";
import { illustrations } from "../lib/illustrations";
import { useWorkspace } from "../lib/workspace";
import {
  ConfidenceMeter, CurveDrivers, ForecastSummaryCard, HotspotBubbles, KpiStrip, LeverSplitRadar,
  LocationCompareRadar, LocationFactorTable, LocationSwotTable, money, ReachCurve, SignalRevenuePanel, SplitBars,
} from "../components/forecastCharts";

const CONF_CLASS: Record<string, string> = { high: "delta-pos", medium: "reviewing", low: "rejected", benchmark: "shortlist" };
const CALIBRATION_LABEL: Record<string, string> = {
  benchmark: "Benchmark (no store data yet)",
  pooled_median: "Calibrated on your stores' median",
  regression: "Fitted regression on your stores",
};

type PrevCurve = { budget: number; points: { investment: number; monthly_revenue: number }[]; monthly_revenue: number; stores: number };
const prevKey = (pid: number) => `pm_fc_prev_${pid}`;

const pctDelta = (now: number, was: number | undefined | null) =>
  was == null || was === 0 ? null : ((now - was) / Math.abs(was)) * 100;

export default function Forecast() {
  const { projects, activeProjectId: projectId, loadError: projectsError, reload: loadProjects } = useWorkspace();
  const [budget, setBudget] = useState("");
  const [pricing, setPricing] = useState<PricingConfig | null>(null);
  const [result, setResult] = useState<ForecastResponse | null>(null);
  const [resultBudget, setResultBudget] = useState<number | null>(null);
  const [prev, setPrev] = useState<PrevCurve | null>(null);
  const [running, setRunning] = useState(false);
  const [showMoreSites, setShowMoreSites] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getPricing().then(setPricing).catch(() => setPricing(null));
  }, []);

  const project = useMemo(() => projects?.find((p) => p.id === projectId) ?? null, [projects, projectId]);
  const cost = pricing?.credit_costs?.forecast;

  // Re-syncs whenever the shared active project changes — including on first
  // load, when `projectId` may already be set from localStorage before the
  // matching `project` object has arrived, hence depending on both.
  useEffect(() => {
    if (projectId == null) return;
    setResult(null);
    setResultBudget(null);
    setError(null);
    try {
      const raw = localStorage.getItem(prevKey(projectId));
      setPrev(raw ? JSON.parse(raw) : null);
    } catch { setPrev(null); }
    setBudget(project?.total_investment != null ? String(project.total_investment) : "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, project]);

  const run = async () => {
    if (projectId == null) return;
    setRunning(true);
    setError(null);
    // snapshot the current forecast as "previous" before overwriting
    if (result && result.sufficient_data && projectId != null) {
      const snap: PrevCurve = {
        budget: result.budget,
        points: result.reach_curve.points.map((p) => ({ investment: p.investment, monthly_revenue: p.monthly_revenue })),
        monthly_revenue: result.recommended_portfolio.monthly_revenue,
        stores: result.recommended_portfolio.stores,
      };
      setPrev(snap);
      try { localStorage.setItem(prevKey(projectId), JSON.stringify(snap)); } catch { /* ignore */ }
    }
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

  const f: ForecastResult | null = result && result.sufficient_data ? result : null;
  const mc = f?.market_capture;
  const rp = f?.recommended_portfolio;
  const sv = f?.reach_curve.smallest_viable ?? null;
  const fundsNothing = !!f && (rp?.stores ?? 0) === 0;

  // KPI values — always show something budget-responsive
  const addedRev = fundsNothing && sv ? sv.monthly_revenue : rp?.monthly_revenue ?? 0;
  const stores = fundsNothing && sv ? sv.stores : rp?.stores ?? 0;
  const payback = fundsNothing && sv ? sv.payback_months : rp?.payback_months;

  const swotSites = (rp?.sites ?? []).filter((s) => s.swot).slice(0, 5).map((s) => ({ name: s.name, swot: s.swot }));
  const factorSites = (rp?.sites ?? []).filter((s) => s.factors).slice(0, 5).map((s) => ({ name: s.name, factors: s.factors }));

  return (
    <>
      <h1 className="page-title">Forecast</h1>
      <p className="page-sub">
        Investment → reachable-revenue, driven by your project's signals, location and budget — sharpened once you
        upload store data to calibrate on your own performance. A modelled projection, not a guarantee — see the
        assumptions at the bottom.
      </p>

      <AsyncBoundary
        loading={projects === null && !projectsError}
        error={projectsError}
        onRetry={loadProjects}
        empty={projects?.length === 0}
        emptyState={
          <EmptyState illustration={illustrations.dataTrends} title="Forecast needs a project"
            description="Every project gets a benchmark forecast automatically — sharper once you upload your own store data."
            dependency="You don't have a project yet — create one to unlock forecasting."
            primaryAction={{ label: "Create a project", to: "/projects/new" }} />
        }
      >
        <>
          <div className="card fc-controls" style={{ marginBottom: 24 }}>
            <div className="fc-controls-row">
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

          {f && rp && (
            <>
              <div className="fc-headline">
                <div>
                  <div className="fc-crumb"><b>{project?.name}</b> <span>/ Forecast</span></div>
                  <h1 className="fc-h1">{money(f.budget, true)} &rarr; where it works hardest</h1>
                </div>
                <div className="fc-headline-pills">
                  {project?.time_horizon_months != null && (
                    <Pill tone="shortlist">{project.time_horizon_months}-mo horizon</Pill>
                  )}
                  <Pill tone={CONF_CLASS[f.confidence]}>{f.confidence} confidence</Pill>
                </div>
              </div>

              {f.calibration === "benchmark" && (
                <p className="wiz-hint" style={{ marginTop: -8, marginBottom: 16 }}>
                  <Pill tone={CONF_CLASS.benchmark} style={{ marginRight: 8 }}>{CALIBRATION_LABEL.benchmark}</Pill>
                  Capture rate = {(f.capture_model.median_capture_rate * 100).toFixed(3)}% of catchment spend, built from an
                  assumed {(f.reach_curve.drivers.find((d) => d.key === "wallet_share")?.value ?? 5)}% category wallet share ×{" "}
                  {(f.reach_curve.drivers.find((d) => d.key === "entrant_capture")?.value ?? 1)}% new-entrant capture —{" "}
                  <Link to="/customer-data">upload at least 3 stores</Link> to fit it on your real sales.
                </p>
              )}

              <KpiStrip
                items={[
                  {
                    label: "Added revenue / mo", value: money(addedRev, true),
                    hint: fundsNothing ? `smallest viable step: ${sv ? money(sv.investment, true) : "—"}` : undefined,
                    delta: fundsNothing ? null : pctDelta(rp.monthly_revenue, prev?.monthly_revenue),
                  },
                  ...(mc ? [{
                    label: "Segment market capture",
                    value: `${mc.current_capture_pct ?? 0}% → ${mc.projected_capture_pct ?? 0}%`,
                  }] : []),
                  {
                    label: "New stores this budget funds", value: String(stores),
                    hint: fundsNothing ? "budget below one site's fit-out cost" : undefined,
                    delta: fundsNothing ? null : (prev?.stores != null ? rp.stores - prev.stores : null),
                    deltaLabel: prev?.stores != null ? `${Math.abs(rp.stores - prev.stores)}` : undefined,
                  },
                  { label: "Blended payback", value: payback != null ? `${payback} mo` : "—" },
                  { label: "Candidates weighed", value: f.candidates_considered.toLocaleString("en-IN") },
                ]}
              />

              <div className="card fc-chart-card">
                <div className="fc-card-head">
                  <span className="kicker">Investment &rarr; projected reachable revenue</span>
                  <span className="wiz-hint">solid = this budget · dotted = your last run</span>
                </div>
                <div className="fc-curve-layout">
                  <div>
                    <ReachCurve
                      points={f.reach_curve.points.map((p) => ({ investment: p.investment, monthly_revenue: p.monthly_revenue, stores: p.stores, at_budget: p.at_budget }))}
                      budget={f.budget}
                      dimFrom={f.reach_curve.diminishing_returns_from}
                      smallestViable={sv}
                      prev={prev && prev.points.length >= 2 ? { points: prev.points, budget: prev.budget } : null}
                    />
                    {f.reach_curve.diminishing_returns_from != null && (
                      <div className="callout-sweetspot">
                        <b>Sweet spot &asymp; {money(f.reach_curve.diminishing_returns_from, true)}.</b>{" "}
                        Beyond this, each extra rupee buys materially less than the sites already ahead of it.
                      </div>
                    )}
                    <p className="fc-chart-note">{f.reach_curve.note}</p>
                  </div>
                  <CurveDrivers drivers={f.reach_curve.drivers} />
                </div>
              </div>

              <div className="card fc-chart-card">
                <div className="fc-card-head">
                  <span className="kicker">Does each signal actually move revenue?</span>
                  <span className="wiz-hint">
                    {f.calibration === "benchmark" ? "site percentiles — upload stores for a fitted verdict" : "correlation with your stores' revenue"}
                  </span>
                </div>
                <SignalRevenuePanel
                  rows={f.lever_fit_radar.map((l) => ({
                    label: l.label, lever_fit: l.lever_fit, site_avg_percentile: l.site_avg_percentile,
                    verdict: l.verdict, direction: l.direction, sample_size: l.sample_size,
                  }))}
                />
              </div>

              {rp.sites.length >= 2 && (
                <div className="card fc-chart-card">
                  <div className="fc-card-head">
                    <span className="kicker">Hotspots &mdash; market size vs modelled revenue</span>
                  </div>
                  <HotspotBubbles
                    points={rp.sites.map((s) => ({
                      name: s.name, reach_gross: s.reach_gross, monthly_revenue: s.monthly_revenue,
                      households: s.households, capex: s.capex, ppi_percentile: s.ppi_percentile,
                      income_percentile: s.income_percentile, footfall_percentile: s.footfall_percentile,
                      within_budget: s.within_budget, state: s.state,
                      tier: s.ppi_percentile >= 200 / 3 ? "core" : s.ppi_percentile >= 100 / 3 ? "edge" : "expansion",
                    }))}
                  />
                </div>
              )}

              <div className="fc-two-wide">
                <div className="card fc-chart-card">
                  <div className="fc-card-head">
                    <span className="kicker">Recommended budget split by lever</span>
                    <span className="wiz-hint">recalculates with your budget</span>
                  </div>
                  <LeverSplitRadar levers={f.lever_split.levers.map((l) => ({ label: l.label, pct: l.pct }))} />
                  <p className="fc-chart-note">{f.lever_split.basis}</p>
                </div>
                <div className="card fc-chart-card">
                  <div className="kicker">How the top sites score on operating factors</div>
                  <LocationFactorTable sites={factorSites} />
                </div>
              </div>

              {factorSites.length >= 2 && (
                <div className="card fc-chart-card">
                  <div className="fc-card-head">
                    <span className="kicker">Top sites compared &mdash; operating-factor profile</span>
                    <span className="wiz-hint">up to 5 locations · 0–100 percentiles</span>
                  </div>
                  <LocationCompareRadar sites={factorSites} />
                </div>
              )}

              <div className="card fc-chart-card">
                <div className="fc-card-head">
                  <span className="kicker">
                    {f.investment_split.length ? "Recommended investment split" : "Smallest viable step"}
                  </span>
                </div>
                {f.investment_split.length === 0 ? (
                  sv ? (
                    <div className="fc-viable">
                      <div>
                        <div className="fc-viable-name">{sv.name} &middot; {sv.state}</div>
                        <div className="fc-viable-note">No site fits {money(f.budget, true)} yet. The cheapest recommended site opens for:</div>
                      </div>
                      <div className="fc-viable-stats">
                        <span><b className="mono">{money(sv.investment, true)}</b> fit-out</span>
                        <span><b className="mono">+{money(sv.monthly_revenue, true)}</b>/mo revenue</span>
                        <span><b className="mono">{sv.payback_months ?? "—"} mo</b> payback</span>
                      </div>
                    </div>
                  ) : <p className="wiz-hint" style={{ marginTop: 8 }}>No priced sites near your target market.</p>
                ) : (
                  <SplitBars
                    rows={f.investment_split.map((s) => ({
                      name: s.state, investment: s.investment, share: s.investment_share_pct,
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
                  <ConfidenceMeter
                    confidence={f.confidence}
                    bandPct={f.capture_model.confidence_band_pct}
                    method={f.capture_model.method}
                    r2={f.capture_model.r2}
                    candidates={f.candidates_considered}
                  />
                </div>

                {f.lever_fit_radar.length > 0 && (
                  <div className="card">
                    <div className="kicker">Levers you're under-using</div>
                    <div className="fc-underused">
                      {[...f.lever_fit_radar]
                        .sort((a, b) => ((a.lever_fit ?? a.site_avg_percentile ?? 999) - (b.lever_fit ?? b.site_avg_percentile ?? 999)))
                        .map((l) => {
                          const v = l.lever_fit ?? l.site_avg_percentile ?? 0;
                          const tone = v < 40 ? "rejected" : v < 60 ? "reviewing" : "delta-pos";
                          const word = v < 40 ? "weak" : v < 60 ? "partial" : "strong";
                          return (
                            <div className="fc-underused-row" key={l.signal}>
                              <span className="fc-underused-name">{l.label}</span>
                              <span className="fc-underused-track">
                                <span style={{ width: `${Math.max(3, Math.min(100, v))}%` }} className={`t-${tone}`} />
                              </span>
                              <span className="mono fc-underused-n">{Math.round(v)}</span>
                              <Pill tone={tone}>{word}</Pill>
                            </div>
                          );
                        })}
                    </div>
                  </div>
                )}
              </div>

              {swotSites.length > 0 && (
                <div className="card fc-chart-card">
                  <div className="fc-card-head">
                    <span className="kicker">SWOT &mdash; top recommended sites</span>
                    <span className="wiz-hint">number = 0–100 factor percentile</span>
                  </div>
                  <LocationSwotTable sites={swotSites} />
                </div>
              )}

              {rp.sites.length > 0 && (() => {
                const withinBudget = rp.sites.filter((s) => s.within_budget);
                const overBudget = rp.sites.filter((s) => !s.within_budget);
                const maxRevenue = Math.max(...rp.sites.map((s) => s.monthly_revenue), 1);

                const renderSite = (s: ForecastSite) => {
                  const caveat = s.revenue_clamped
                    ? "Revenue capped — this site's modelled demand exceeds what the capture-rate model trusts without more store data."
                    : s.cannibalisation_discount < 0.98
                    ? `Catchment overlaps another recommended site — revenue discounted ${Math.round((1 - s.cannibalisation_discount) * 100)}%.`
                    : null;
                  return (
                    <DataRow
                      key={s.pincode}
                      title={`${s.name} · ${s.pincode}`}
                      subtitle={s.state}
                      chips={[
                        <StatChip key="ppi" icon="ti ti-chart-bar">PPI {s.ppi_percentile}</StatChip>,
                        <StatChip key="capex" icon="ti ti-currency-rupee">
                          {s.capex != null ? `CapEx ${money(s.capex, true)}` : "CapEx —"}
                        </StatChip>,
                        <StatChip key="payback" icon="ti ti-hourglass">
                          {s.payback_months != null ? `${s.payback_months} mo payback` : "payback —"}
                        </StatChip>,
                        <span
                          key="caveat"
                          className={`fc-site-caveat${caveat ? " on" : ""}`}
                          title={caveat ?? undefined}
                        >
                          <i className="ti ti-alert-triangle" aria-hidden="true" />
                        </span>,
                      ]}
                      trailing={
                        <>
                          <MicroBar value={s.monthly_revenue} max={maxRevenue} color={RUPEE} />
                          <Pill tone={s.within_budget ? "delta-pos" : "shortlist"}>
                            {s.within_budget ? `+${money(s.monthly_revenue, true)}/mo` : "needs bigger budget"}
                          </Pill>
                        </>
                      }
                    />
                  );
                };

                return (
                  <div className="card">
                    <div className="fc-card-head">
                      <span className="kicker">Which locations yield well</span>
                      <span className="wiz-hint">ranked best-first &mdash; bar shows reachable revenue relative to the top site</span>
                    </div>
                    {withinBudget.length > 0 && <DataList>{withinBudget.map(renderSite)}</DataList>}
                    {overBudget.length > 0 && (
                      <div style={{ marginTop: 10 }}>
                        <button
                          type="button"
                          className="fc-stale-link"
                          onClick={() => setShowMoreSites((v) => !v)}
                        >
                          {showMoreSites ? "Hide" : `Show ${overBudget.length} more`} — need a bigger budget
                        </button>
                        {showMoreSites && <DataList>{overBudget.map(renderSite)}</DataList>}
                      </div>
                    )}
                  </div>
                );
              })()}

              <ForecastSummaryCard
                headline={fundsNothing
                  ? `At ${money(f.budget, true)} — nothing opens yet`
                  : `At ${money(f.budget, true)} — ${rp.stores} new ${rp.stores === 1 ? "store" : "stores"}`}
                stats={[
                  { label: "Investment deployed", value: money(fundsNothing && sv ? sv.investment : rp.investment ?? f.budget, true), tone: "neutral", sub: fundsNothing ? "smallest viable" : undefined },
                  { label: "Added revenue / mo", value: money(addedRev, true), tone: "pos" },
                  { label: "Added gross profit / mo", value: money(fundsNothing && sv ? sv.monthly_gross_profit : rp.monthly_gross_profit, true), tone: "pos" },
                  { label: "Blended payback", value: payback != null ? `${payback} mo` : "—", tone: "neutral", sub: project?.time_horizon_months ? `${project.time_horizon_months}-mo horizon` : undefined },
                  { label: "Market capture", value: mc ? `${mc.current_capture_pct ?? 0}% → ${mc.projected_capture_pct ?? 0}%` : "—", tone: "neutral" },
                  { label: `Gross profit over ${f.time_horizon_months} mo`, value: money(rp.horizon_gross_profit, true), tone: "pos" },
                ]}
                costs={[
                  { label: "total fit-out CapEx", value: money(fundsNothing && sv ? sv.investment : rp.investment ?? 0, true) },
                  { label: "per store (avg)", value: money(rp.stores > 0 && rp.investment ? rp.investment / rp.stores : (sv?.investment ?? null), true) },
                  { label: "credits for this forecast", value: cost != null ? String(cost) : "—" },
                ]}
                footnote={`Revenue is modelled reachable household spend at a ${(f.capture_model.median_capture_rate * 100).toFixed(3)}% capture rate (${f.capture_model.method}), gross profit at ${f.gross_margin_pct}% margin. ${f.capex_basis ?? ""}`}
              />

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
      </AsyncBoundary>
    </>
  );
}

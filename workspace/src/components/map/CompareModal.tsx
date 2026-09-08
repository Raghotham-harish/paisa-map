import { useEffect, useState } from "react";
import { ApiError, DriverAnalysis, LocationScore, LocationStatus, PricingConfig, Project, SavedLocation, api } from "../../lib/api";
import { prettySignal } from "../../lib/signalLabel";

type Ranked = LocationScore & { rank: number };

function money(n: number | null | undefined) {
  if (n == null) return "—";
  if (Math.abs(n) >= 1e7) return `₹${(n / 1e7).toFixed(2)} Cr`;
  if (Math.abs(n) >= 1e5) return `₹${(n / 1e5).toFixed(2)} L`;
  if (Math.abs(n) >= 1e3) return `₹${(n / 1e3).toFixed(0)} k`;
  return `₹${Math.round(n).toLocaleString("en-IN")}`;
}

const STATUS_PILL: Record<LocationStatus, string> = {
  shortlist: "shortlist", reviewing: "reviewing", approved: "approved", rejected: "rejected",
};
const SUIT_PILL: Record<string, string> = {
  "Highly Suitable": "approved", Suitable: "reviewing", Marginal: "shortlist", "Not Suitable": "rejected",
};
const RISK_PILL: Record<string, string> = { Low: "approved", Medium: "reviewing", High: "rejected", Unknown: "shortlist" };

const opp = (l: Ranked) => l.opportunity?.opportunity_score ?? l.economic_score ?? null;

// One stable colour per location, used across every chart + the scorecards.
const LOC_COLORS = ["var(--rupee)", "var(--amber)", "#2A81CB", "var(--flame)", "#7A54B0", "#0E9594", "#C2650C", "#555"];

/* Grouped horizontal bars — who leads on each metric, at a glance + the number. */
function MetricBars({ rows }: { rows: Ranked[] }) {
  const metrics: { label: string; get: (l: Ranked) => number | null; fmt: (v: number) => string; outOf?: number }[] = [
    { label: "Opportunity", get: opp, fmt: (v) => `${Math.round(v)}`, outOf: 100 },
    { label: "Economic", get: (l) => l.economic_score, fmt: (v) => `${Math.round(v)}`, outOf: 100 },
    { label: "Income /mo", get: (l) => l.income, fmt: (v) => money(v) },
    { label: "Spend /mo", get: (l) => l.spend, fmt: (v) => money(v) },
  ];
  return (
    <div className="cmp-bars">
      {metrics.map((m) => {
        const vals = rows.map((l) => m.get(l));
        const max = m.outOf ?? Math.max(1, ...vals.map((v) => v ?? 0));
        const best = Math.max(...vals.map((v) => v ?? -Infinity));
        return (
          <div className="cmp-bar-row" key={m.label}>
            <span className="cmp-bar-metric">{m.label}</span>
            <div className="cmp-bar-set">
              {rows.map((l, i) => {
                const v = m.get(l);
                const w = v == null ? 0 : Math.max(2, (v / max) * 100);
                return (
                  <div className="cmp-bar-line" key={l.pincode}>
                    <span className="cmp-bar-track">
                      <span className={`cmp-bar-fill ${v === best ? "lead" : ""}`}
                        style={{ width: `${w}%`, background: LOC_COLORS[i % LOC_COLORS.length] }} />
                    </span>
                    <span className={`cmp-bar-val ${v === best ? "lead" : ""}`}>{v == null ? "—" : m.fmt(v)}</span>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// The exec summary is a full paragraph; the compare "Read" cell wants a glance.
function firstSentences(text: string, n: number) {
  if (!text) return "—";
  const parts = text.split(/(?<=\.)\s+/).filter(Boolean);
  const out = parts.slice(0, n).join(" ");
  // drop the boilerplate "Modelled estimate…" tail if it slipped in
  return out.replace(/\s*Modelled estimate.*$/i, "").trim() || parts[0];
}

export function CompareModal({
  pincodes,
  project,
  onClose,
  onSaved,
}: {
  pincodes: string[];
  project: Project | null;
  onClose: () => void;
  onSaved?: () => void;
}) {
  const [rows, setRows] = useState<Ranked[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [drivers, setDrivers] = useState<DriverAnalysis | null>(null);
  const [saved, setSaved] = useState<SavedLocation[]>([]);
  const [pricing, setPricing] = useState<PricingConfig | null>(null);
  const [bulkSaving, setBulkSaving] = useState(false);
  const [bulkSaveMsg, setBulkSaveMsg] = useState<string | null>(null);
  const [reportRunning, setReportRunning] = useState(false);
  const [reportError, setReportError] = useState<string | null>(null);
  const [reportId, setReportId] = useState<number | null>(null);

  useEffect(() => {
    api
      .compareLocations(pincodes, {
        avg_ticket: project?.avg_ticket ?? null,
        target_segment: project?.target_segment ?? null,
        business_type: project?.business_type ?? project?.industry ?? null,
      })
      .then((d) => setRows(d.locations))
      .catch(() => setError("Couldn't load the comparison."));
  }, [pincodes, project]);

  useEffect(() => {
    api.getPricing().then(setPricing).catch(() => setPricing(null));
  }, []);

  useEffect(() => {
    if (!project) { setDrivers(null); return; }
    api.getDriverAnalysis(project.id).then(setDrivers).catch(() => setDrivers(null));
  }, [project]);

  const loadSaved = () => {
    if (!project) { setSaved([]); return; }
    api.listLocations(project.id).then((d) => setSaved(d.locations)).catch(() => setSaved([]));
  };
  useEffect(loadSaved, [project]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const savedPincodes = new Set(saved.map((s) => s.pincode));
  const topDriverSignals = new Set((drivers?.sufficient_data ? drivers.drivers : []).map((d) => d.signal));

  const onBulkSave = async () => {
    if (!rows) return;
    setBulkSaving(true);
    setBulkSaveMsg(null);
    let created = 0;
    for (const l of rows) {
      if (savedPincodes.has(l.pincode)) continue;
      try {
        await api.createLocation({ pincode: l.pincode, name: l.name, lat: l.lat, lng: l.lng, project_id: project?.id });
        created++;
      } catch { /* keep going; summary reports what landed */ }
    }
    setBulkSaving(false);
    const skipped = rows.length - created;
    setBulkSaveMsg(
      created === 0
        ? "All already saved."
        : `Saved ${created}${skipped > 0 ? ` (${skipped} already saved)` : ""}.`,
    );
    loadSaved();
    onSaved?.();
  };

  const reportCost = pricing?.credit_costs?.report_generate;

  const onGenerateReport = async () => {
    if (!project) return;
    setReportRunning(true);
    setReportError(null);
    setReportId(null);
    try {
      await onBulkSave();
      const { report } = await api.generateReport(project.id, `Compare — ${new Date().toLocaleDateString()}`);
      setReportId(report.id);
    } catch (e) {
      setReportError(
        e instanceof ApiError && e.body?.error === "insufficient_credits"
          ? `Not enough credits — needs ${e.body.required}, you have ${e.body.balance}.`
          : e instanceof ApiError && e.body?.error === "no_locations"
            ? "Nothing to report on yet."
            : "Couldn't generate the report — try again.",
      );
    } finally {
      setReportRunning(false);
    }
  };

  const strongestDriver = (l: Ranked) => {
    const match = (l.top_signals ?? []).find((s) => topDriverSignals.has(s)) ?? (l.top_signals ?? [])[0];
    return match ? prettySignal(match) : "—";
  };

  const gridCols = rows ? `148px repeat(${rows.length}, minmax(0,1fr))` : "148px";
  const leader = rows && rows.length ? [...rows].sort((a, b) => (opp(b) ?? 0) - (opp(a) ?? 0))[0] : null;
  const richest = rows && rows.length ? [...rows].sort((a, b) => (b.income ?? 0) - (a.income ?? 0))[0] : null;

  type Row = { lab: string; cell: (l: Ranked) => React.ReactNode; head?: boolean };
  const tableRows: Row[] = [
    {
      lab: "Economic score", head: true,
      cell: (l) => <><span className="mono cmp-big">{l.economic_score ?? "—"}</span><span className="cmp-outof"> /100</span></>,
    },
    { lab: "Opportunity (weighted)", cell: (l) => <span className="mono">{opp(l) != null ? Math.round(opp(l)!) : "—"}</span> },
    {
      lab: "Suitability",
      cell: (l) => l.opportunity ? <span className={`pill ${SUIT_PILL[l.opportunity.suitability] ?? "shortlist"}`}>{l.opportunity.suitability}</span> : "—",
    },
    { lab: "Risk", cell: (l) => <span className={`pill ${RISK_PILL[l.risk.level] ?? "shortlist"}`}>{l.risk.level}</span> },
    { lab: "Monthly income / hh", cell: (l) => <span className="mono">{money(l.income)}</span> },
    { lab: "Monthly spend / hh", cell: (l) => <span className="mono">{money(l.spend)}</span> },
    { lab: "Strongest driver", cell: (l) => strongestDriver(l) },
    {
      lab: "vs nearby areas",
      cell: (l) => {
        const d = l.benchmark.neighbours?.diff_pct;
        if (d == null) return <span className="mono">—</span>;
        return <span className={`mono ${d >= 0 ? "cmp-pos" : "cmp-neg"}`}>{d > 0 ? "+" : ""}{d}%</span>;
      },
    },
    { lab: "Read", cell: (l) => <span className="cmp-read">{firstSentences(l.executive_summary, 2)}</span> },
  ];

  return (
    <div className="map-modal-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="card map-modal cmp-modal" role="dialog" aria-label="Compare locations">
        <div className="map-modal-head">
          <h2 style={{ margin: 0, fontSize: 17 }}>Compare {rows ? rows.length : pincodes.length} locations</h2>
          <button type="button" aria-label="Close" onClick={onClose}>
            <svg width="16" height="16" viewBox="0 0 16 16" stroke="currentColor" strokeWidth="1.7"><path d="M3 3l10 10M13 3L3 13" /></svg>
          </button>
        </div>

        {error && <p style={{ color: "var(--flame)", fontSize: 13, padding: "0 22px" }}>{error}</p>}
        {!rows && !error && <p className="loading" style={{ padding: "0 22px" }}>Loading…</p>}

        {rows && (
          <div className="cmp-layout">
            {/* LEFT */}
            <div className="cmp-left">
              <div className="card cmp-basket">
                <span className="kicker">Compare basket</span>
                <div className="cmp-basket-row">
                  <div className="cmp-basket-chips">
                    {rows.map((l) => (
                      <span className="cmp-chip" key={l.pincode}>{l.name}</span>
                    ))}
                  </div>
                  <button className="btn secondary" disabled={bulkSaving} onClick={onBulkSave}>
                    {bulkSaving ? "Saving…" : "Save all to project"}
                  </button>
                </div>
                {bulkSaveMsg && <span className="wiz-hint">{bulkSaveMsg}</span>}
              </div>

              {leader && (
                <div className="cmp-insight">
                  <b>{leader.name}</b> leads on opportunity ({Math.round(opp(leader) ?? 0)}/100)
                  {richest && richest.pincode !== leader.pincode
                    ? <> · <b>{richest.name}</b> has the highest income ({money(richest.income)}/mo)</>
                    : richest && <> and income ({money(leader.income)}/mo)</>}.
                </div>
              )}

              <div className="cmp-cards">
                {rows.map((l, i) => (
                  <div className="cmp-card" key={l.pincode} style={{ borderTopColor: LOC_COLORS[i % LOC_COLORS.length] }}>
                    <div className="cmp-card-head">
                      <span className={`cmp-rank ${l.rank === 1 ? "r1" : ""}`}>{l.rank}</span>
                      <span className="cmp-card-name">{l.name}</span>
                    </div>
                    <div className="cmp-card-score">
                      <span className="mono">{opp(l) != null ? Math.round(opp(l)!) : "—"}</span>
                      <small>/100 opp</small>
                    </div>
                    <div className="cmp-card-pills">
                      {l.opportunity && <span className={`pill ${SUIT_PILL[l.opportunity.suitability] ?? "shortlist"}`}>{l.opportunity.suitability}</span>}
                      <span className={`pill ${RISK_PILL[l.risk.level] ?? "shortlist"}`}>{l.risk.level}</span>
                    </div>
                    <div className="cmp-card-money">
                      <span>Income <b>{money(l.income)}</b></span>
                      <span>Spend <b>{money(l.spend)}</b></span>
                    </div>
                  </div>
                ))}
              </div>

              <div className="card" style={{ padding: "14px 16px" }}>
                <div className="cmp-section-label">How they stack up</div>
                <div className="cmp-legend">
                  {rows.map((l, i) => (
                    <span className="cmp-legend-item" key={l.pincode}>
                      <i style={{ background: LOC_COLORS[i % LOC_COLORS.length] }} />{l.name}
                    </span>
                  ))}
                </div>
                <MetricBars rows={rows} />
              </div>

              <div className="card cmp-grid-card">
                <div className="cmp-grid-head" style={{ gridTemplateColumns: gridCols }}>
                  <span className="kicker">Metric</span>
                  {rows.map((l) => (
                    <span className="cmp-col-head" key={l.pincode}>
                      <span className={`cmp-rank ${l.rank === 1 ? "r1" : ""}`}>{l.rank}</span>
                      <b>{l.name}</b>
                      <span className="cmp-col-pin">{l.pincode}</span>
                    </span>
                  ))}
                </div>
                {tableRows.map((r) => (
                  <div className={`cmp-grid-row ${r.head ? "head" : ""}`} key={r.lab} style={{ gridTemplateColumns: gridCols }}>
                    <span className="cmp-grid-lab">{r.lab}</span>
                    {rows.map((l) => (
                      <span className="cmp-grid-cell" key={l.pincode}>{r.cell(l)}</span>
                    ))}
                  </div>
                ))}
              </div>
            </div>

            {/* RIGHT — saved locations rail */}
            <div className="card cmp-rail">
              <div className="cmp-rail-head">
                <span className="kicker">Saved locations</span>
                <span className="cmp-rail-sub">{saved.length}{project ? ` · ${project.name}` : ""}</span>
              </div>
              <div className="cmp-rail-list">
                {saved.length === 0 && (
                  <p className="wiz-hint" style={{ padding: "14px 16px" }}>
                    Nothing saved yet. Use “Save all to project” to start a shortlist.
                  </p>
                )}
                {saved.map((l) => (
                  <div className="cmp-rail-item" key={l.id}>
                    <div className="cmp-rail-item-top">
                      <b>{l.name || l.pincode}</b>
                      <span className={`pill ${STATUS_PILL[l.status]}`}>{l.status}</span>
                    </div>
                    <div className="cmp-rail-item-meta">
                      {l.pincode}
                      {l.allocated_investment != null ? <> · <span className="mono">{money(l.allocated_investment)} allocated</span></> : null}
                    </div>
                    {l.notes && <div className="cmp-rail-item-note">{l.notes}</div>}
                  </div>
                ))}
              </div>
              <div className="cmp-rail-foot">
                {reportId != null ? (
                  <a className="btn" style={{ width: "100%", textAlign: "center" }} href={api.reportDownloadUrl(reportId)}>Download report PDF</a>
                ) : (
                  <button className="btn secondary" style={{ width: "100%" }} disabled={reportRunning || !project}
                    title={project ? "" : "Pick a project first"} onClick={onGenerateReport}>
                    {reportRunning ? "Generating…" : `Generate expansion report${reportCost != null ? ` — ${reportCost} credits` : ""}`}
                  </button>
                )}
                {reportError && <p style={{ color: "var(--flame)", fontSize: 12, margin: "8px 0 0" }}>{reportError}</p>}
              </div>
            </div>
          </div>
        )}

        <p className="cmp-disclaimer" style={{ padding: "10px 22px 0" }}>
          Modelled estimates from PaisaMap's PPI ensemble — not real transaction records.
        </p>
      </div>
    </div>
  );
}

import { useEffect, useState } from "react";
import { ApiError, DriverAnalysis, LocationScore, LocationStatus, PricingConfig, Project, SavedLocation, api } from "../../lib/api";

type Ranked = LocationScore & { rank: number };

// One stable colour per location, used across every chart in the modal.
const LOC_COLORS = ["var(--rupee)", "var(--amber)", "#2A81CB", "var(--flame)", "#7A54B0", "#0E9594", "#C2650C", "#555"];

function money(n: number | null | undefined, compact = false) {
  if (n == null) return "—";
  if (compact) {
    if (Math.abs(n) >= 1e7) return `₹${(n / 1e7).toFixed(2)}Cr`;
    if (Math.abs(n) >= 1e5) return `₹${(n / 1e5).toFixed(1)}L`;
    if (Math.abs(n) >= 1e3) return `₹${(n / 1e3).toFixed(0)}k`;
  }
  return `₹${Math.round(n).toLocaleString("en-IN")}`;
}

const STATUS_PILL: Record<LocationStatus, string> = {
  shortlist: "shortlist", reviewing: "reviewing", approved: "approved", rejected: "rejected",
};
const RISK_PILL: Record<string, string> = { Low: "delta-pos", Medium: "reviewing", High: "rejected", Unknown: "shortlist" };

const opp = (l: Ranked) => l.opportunity?.opportunity_score ?? l.economic_score ?? null;

/* ── metric comparison bars ─────────────────────────────────────────────────
   One row per metric; every location a labelled bar scaled to the row's max,
   the leader marked. Gives "who wins on what" at a glance + the real number. */
function MetricBars({ rows }: { rows: Ranked[] }) {
  const metrics: { label: string; get: (l: Ranked) => number | null; fmt: (v: number) => string; outOf?: number }[] = [
    { label: "Opportunity", get: opp, fmt: (v) => `${Math.round(v)}`, outOf: 100 },
    { label: "Economic score", get: (l) => l.economic_score, fmt: (v) => `${Math.round(v)}`, outOf: 100 },
    { label: "Income /mo", get: (l) => l.income, fmt: (v) => money(v, true) },
    { label: "Spend /mo", get: (l) => l.spend, fmt: (v) => money(v, true) },
  ];
  return (
    <>
    <div className="cmp-legend">
      {rows.map((l, i) => (
        <span className="cmp-legend-item" key={l.pincode}>
          <i style={{ background: LOC_COLORS[i % LOC_COLORS.length] }} />
          {l.name}
        </span>
      ))}
    </div>
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
                      <span
                        className={`cmp-bar-fill ${v === best ? "lead" : ""}`}
                        style={{ width: `${w}%`, background: LOC_COLORS[i % LOC_COLORS.length] }}
                      />
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
    </>
  );
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
  const [allocInput, setAllocInput] = useState<Record<number, string>>({});
  const [showTable, setShowTable] = useState(false);

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
    if (!project) {
      setDrivers(null);
      return;
    }
    api.getDriverAnalysis(project.id).then(setDrivers).catch(() => setDrivers(null));
  }, [project]);

  const loadSaved = () => {
    if (!project) {
      setSaved([]);
      return;
    }
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
      } catch {
        // one failure shouldn't stop the rest — the summary below reports what actually landed
      }
    }
    setBulkSaving(false);
    const skipped = rows.length - created;
    setBulkSaveMsg(
      created === 0
        ? "All of these are already saved."
        : `Saved ${created} location${created === 1 ? "" : "s"}${skipped > 0 ? ` (${skipped} already saved)` : ""}.`,
    );
    loadSaved();
    onSaved?.();
  };

  const onAllocate = async (loc: SavedLocation) => {
    const raw = allocInput[loc.id];
    const n = Number(raw);
    if (!raw || !isFinite(n) || n < 0) return;
    const { location } = await api.updateLocation(loc.id, { allocated_investment: n });
    setSaved((prev) => prev.map((s) => (s.id === location.id ? location : s)));
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
          ? `Not enough credits — this report costs ${e.body.required}, you have ${e.body.balance}.`
          : e instanceof ApiError && e.body?.error === "no_locations"
            ? "Nothing to report on yet."
            : "Couldn't generate the report — try again.",
      );
    } finally {
      setReportRunning(false);
    }
  };

  // Headline insight — who leads on the two metrics that matter most.
  const leader = rows && rows.length
    ? [...rows].sort((a, b) => (opp(b) ?? 0) - (opp(a) ?? 0))[0]
    : null;
  const richest = rows && rows.length
    ? [...rows].sort((a, b) => (b.income ?? 0) - (a.income ?? 0))[0]
    : null;

  const tableRows: { label: string; render: (l: Ranked) => React.ReactNode }[] = [
    { label: "Opportunity", render: (l) => (opp(l) != null ? Math.round(opp(l)!) : "—") },
    { label: "Economic score", render: (l) => l.economic_score ?? "—" },
    { label: "Suitability", render: (l) => l.opportunity?.suitability ?? "—" },
    { label: "Risk", render: (l) => l.risk.level },
    { label: "Income /mo", render: (l) => money(l.income) },
    { label: "Spend /mo", render: (l) => money(l.spend) },
    { label: "vs nearby", render: (l) => (l.benchmark.neighbours?.diff_pct != null ? `${l.benchmark.neighbours.diff_pct}%` : "—") },
    {
      label: "Strongest driver for you",
      render: (l) => {
        const match = (l.top_signals ?? []).find((s) => topDriverSignals.has(s));
        return match ? <span className="pill delta-pos">{match}</span> : <span style={{ color: "var(--ink-soft)" }}>—</span>;
      },
    },
    { label: "Top signals", render: (l) => (l.top_signals ?? []).slice(0, 3).join(", ") || "—" },
  ];

  return (
    <div className="map-modal-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="card map-modal" role="dialog" aria-label="Compare locations">
        <div className="map-modal-head">
          <h2 style={{ margin: 0, fontSize: 17 }}>Compare {rows ? rows.length : pincodes.length} locations</h2>
          <button type="button" aria-label="Close" onClick={onClose}>
            <svg width="16" height="16" viewBox="0 0 16 16" stroke="currentColor" strokeWidth="1.7"><path d="M3 3l10 10M13 3L3 13" /></svg>
          </button>
        </div>

        {error && <p style={{ color: "var(--flame)", fontSize: 13 }}>{error}</p>}
        {!rows && !error && <p className="loading">Loading…</p>}

        {rows && (
          <>
            {/* headline insight */}
            {leader && (
              <div className="cmp-insight">
                <b>{leader.name}</b> leads on opportunity ({Math.round(opp(leader) ?? 0)}/100)
                {richest && richest.pincode !== leader.pincode && (
                  <> · <b>{richest.name}</b> has the highest income ({money(richest.income, true)}/mo)</>
                )}
                {richest && richest.pincode === leader.pincode && <> and income ({money(leader.income, true)}/mo)</>}.
              </div>
            )}

            {/* scorecards */}
            <div className="cmp-cards">
              {rows.map((l, i) => (
                <div className="cmp-card" key={l.pincode} style={{ borderTopColor: LOC_COLORS[i % LOC_COLORS.length] }}>
                  <div className="cmp-card-head">
                    <span className={`cmp-rank ${l.rank === 1 ? "r1" : ""}`}>{l.rank}</span>
                    <div style={{ minWidth: 0 }}>
                      <div className="cmp-card-name">{l.name}</div>
                      <div className="cmp-pin">{l.pincode}</div>
                    </div>
                  </div>
                  <div className="cmp-card-score">
                    <span className="mono">{opp(l) != null ? Math.round(opp(l)!) : "—"}</span>
                    <small>/100 opportunity</small>
                  </div>
                  <div className="cmp-card-pills">
                    {l.opportunity && <span className="pill approved">{l.opportunity.suitability}</span>}
                    <span className={`pill ${RISK_PILL[l.risk.level] ?? "shortlist"}`}>{l.risk.level} risk</span>
                  </div>
                  <div className="cmp-card-money">
                    <span>Income <b>{money(l.income, true)}</b></span>
                    <span>Spend <b>{money(l.spend, true)}</b></span>
                    <span>
                      vs nearby{" "}
                      <b className={(l.benchmark.neighbours?.diff_pct ?? 0) >= 0 ? "pos" : "neg"}>
                        {l.benchmark.neighbours?.diff_pct != null ? `${l.benchmark.neighbours.diff_pct > 0 ? "+" : ""}${l.benchmark.neighbours.diff_pct}%` : "—"}
                      </b>
                    </span>
                  </div>
                </div>
              ))}
            </div>

            {/* metric bars */}
            <div className="cmp-section-label">How they stack up</div>
            <MetricBars rows={rows} />

            {/* detail table (collapsed) */}
            <button className="cmp-table-toggle" type="button" onClick={() => setShowTable((s) => !s)}>
              {showTable ? "Hide" : "Show"} full detail table
            </button>
            {showTable && (
              <div className="cmp-scroll">
                <table className="cmp-table">
                  <thead>
                    <tr>
                      <th />
                      {rows.map((l) => (
                        <th key={l.pincode}>
                          <span className={`cmp-rank ${l.rank === 1 ? "r1" : ""}`}>{l.rank}</span>
                          {l.name}
                          <span className="cmp-pin">{l.pincode}</span>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {tableRows.map((rd) => (
                      <tr key={rd.label}>
                        <th>{rd.label}</th>
                        {rows.map((l) => (
                          <td key={l.pincode}>{rd.render(l)}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <div className="cmp-actions">
              <div>
                <button className="btn secondary" disabled={bulkSaving} onClick={onBulkSave}>
                  {bulkSaving ? "Saving…" : "Save all to project"}
                </button>
                {bulkSaveMsg && <span className="wiz-hint" style={{ marginLeft: 10 }}>{bulkSaveMsg}</span>}
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                {reportId != null ? (
                  <a className="btn" href={api.reportDownloadUrl(reportId)}>Download report PDF</a>
                ) : (
                  <button className="btn" disabled={reportRunning || !project} title={project ? "" : "Pick a project first"} onClick={onGenerateReport}>
                    {reportRunning ? "Generating…" : `Generate report${reportCost != null ? ` — ${reportCost} credits` : ""}`}
                  </button>
                )}
              </div>
            </div>
            {reportError && <p style={{ color: "var(--flame)", fontSize: 12.5, marginTop: 6 }}>{reportError}</p>}

            {project && saved.length > 0 && (
              <div className="cmp-shortlist">
                <div className="kicker" style={{ marginBottom: 8 }}>Saved-locations shortlist — {project.name}</div>
                <ul className="list">
                  {saved.map((l) => (
                    <li key={l.id}>
                      <div>
                        <div className="primary">{l.name || l.pincode}</div>
                        <div className="secondary">
                          {l.pincode}
                          {l.notes ? ` · ${l.notes}` : ""}
                          {l.allocated_investment != null ? ` · ${money(l.allocated_investment)} allocated` : ""}
                        </div>
                      </div>
                      <div className="row-actions">
                        <span className={`pill ${STATUS_PILL[l.status]}`}>{l.status}</span>
                        <input
                          type="number"
                          placeholder="₹ allocate"
                          style={{ width: 100, fontSize: 12 }}
                          value={allocInput[l.id] ?? ""}
                          onChange={(e) => setAllocInput((prev) => ({ ...prev, [l.id]: e.target.value }))}
                          onBlur={() => onAllocate(l)}
                        />
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <p style={{ fontSize: 11.5, color: "var(--ink-soft)", margin: "14px 0 0" }}>
              Modelled estimates from PaisaMap's PPI ensemble — not real transaction records.
            </p>
          </>
        )}
      </div>
    </div>
  );
}

import { useEffect, useState } from "react";
import { ApiError, DriverAnalysis, LocationScore, LocationStatus, PricingConfig, Project, SavedLocation, api } from "../../lib/api";

type Ranked = LocationScore & { rank: number };

function money(n: number | null | undefined) {
  return n == null ? "—" : `₹${Math.round(n).toLocaleString("en-IN")}`;
}

const STATUS_PILL: Record<LocationStatus, string> = {
  shortlist: "shortlist", reviewing: "reviewing", approved: "approved", rejected: "rejected",
};

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

  // Project-scoped drivers, for "strongest driver for you" below — same
  // fetch LocationPanel makes, reused here rather than re-deriving.
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
      // A report is generated over a project's SAVED locations, not the
      // compare selection directly — make sure everything being compared is
      // actually saved first, same action as the button above.
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

  const rowDef: { label: string; render: (l: Ranked) => React.ReactNode }[] = [
    { label: "Economic score", render: (l) => <span className="cmp-score">{l.economic_score ?? "—"}<small> /100</small></span> },
    { label: "Opportunity", render: (l) => l.opportunity?.opportunity_score ?? l.economic_score ?? "—" },
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
    { label: "Read", render: (l) => <span className="cmp-summary">{l.executive_summary}</span> },
  ];

  return (
    <div className="map-modal-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="card map-modal" role="dialog" aria-label="Compare locations">
        <div className="map-modal-head">
          <h2 style={{ margin: 0, fontSize: 17 }}>Compare locations</h2>
          <button type="button" aria-label="Close" onClick={onClose}>
            <svg width="16" height="16" viewBox="0 0 16 16" stroke="currentColor" strokeWidth="1.7"><path d="M3 3l10 10M13 3L3 13" /></svg>
          </button>
        </div>
        <p style={{ fontSize: 12, color: "var(--ink-soft)", margin: "0 0 12px" }}>
          Modelled estimates from PaisaMap's PPI ensemble — not real transaction records.
        </p>
        {error && <p style={{ color: "var(--flame)", fontSize: 13 }}>{error}</p>}
        {!rows && !error && <p className="loading">Loading…</p>}
        {rows && (
          <>
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
                  {rowDef.map((rd) => (
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
          </>
        )}
      </div>
    </div>
  );
}

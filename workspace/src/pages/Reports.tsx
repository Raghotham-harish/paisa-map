import { useEffect, useState } from "react";
import { ApiError, api, LocationScore, Project, Report } from "../lib/api";
import { EmptyState } from "../components/EmptyState";

const RISK_CLASS: Record<string, string> = {
  Low: "delta-pos",
  Medium: "reviewing",
  High: "rejected",
  Unknown: "shortlist",
};

const STATUS_CLASS: Record<Report["status"], string> = {
  ready: "delta-pos",
  processing: "reviewing",
  pending: "reviewing",
  failed: "rejected",
};

function summarize(locations?: LocationScore[]): string | null {
  if (!locations || locations.length === 0) return null;
  const scores = locations.map((l) => l.opportunity?.opportunity_score ?? l.economic_score ?? 0);
  const avg = Math.round(scores.reduce((a, b) => a + b, 0) / scores.length);
  const elevated = locations.filter((l) => l.risk.level === "High" || l.risk.level === "Medium").length;
  return (
    `${locations.length} location${locations.length > 1 ? "s" : ""} · avg opportunity ${avg}/100` +
    (elevated ? ` · ${elevated} elevated risk` : "")
  );
}

export default function Reports() {
  const [reports, setReports] = useState<Report[] | null>(null);
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [generating, setGenerating] = useState<number | null>(null);
  const [errors, setErrors] = useState<Record<number, string>>({});
  const [sharing, setSharing] = useState<number | null>(null);
  const [copied, setCopied] = useState<number | null>(null);

  const loadReports = () => api.listReports().then((data) => setReports(data.reports));

  useEffect(() => {
    loadReports();
    api.listProjects().then((data) => setProjects(data.projects));
  }, []);

  const onGenerate = async (project: Project) => {
    setGenerating(project.id);
    setErrors((prev) => ({ ...prev, [project.id]: "" }));
    try {
      await api.generateReport(project.id);
      await loadReports();
    } catch (e) {
      const detail =
        e instanceof ApiError && (e.body?.detail || e.body?.error)
          ? String(e.body.detail || e.body.error)
          : "Couldn't generate that report — try again.";
      setErrors((prev) => ({ ...prev, [project.id]: detail }));
    } finally {
      setGenerating(null);
    }
  };

  const onShare = async (report: Report) => {
    setSharing(report.id);
    try {
      const { report: updated } = await api.shareReport(report.id);
      setReports((prev) => prev && prev.map((r) => (r.id === updated.id ? updated : r)));
    } finally {
      setSharing(null);
    }
  };

  const onUnshare = async (report: Report) => {
    setSharing(report.id);
    try {
      const { report: updated } = await api.unshareReport(report.id);
      setReports((prev) => prev && prev.map((r) => (r.id === updated.id ? updated : r)));
    } finally {
      setSharing(null);
    }
  };

  const onCopyLink = async (report: Report) => {
    if (!report.share_token) return;
    try {
      await navigator.clipboard.writeText(api.sharedReportUrl(report.share_token));
      setCopied(report.id);
      setTimeout(() => setCopied((prev) => (prev === report.id ? null : prev)), 2000);
    } catch {
      // Clipboard permission denied — the link is still visible below to copy by hand.
    }
  };

  return (
    <>
      <h1 className="page-title">Reports</h1>
      <p className="page-sub">Generate a location intelligence PDF for any project's saved locations.</p>

      {reports === null || projects === null ? (
        <div className="loading">Loading…</div>
      ) : projects.length === 0 ? (
        <EmptyState
          icon="📄"
          title="Reports need a project first"
          description="Reports are generated for a project's saved locations — economic score, opportunity, suitability, and risk for each one, as a downloadable PDF."
          dependency="You don't have a project yet — create one to unlock report generation."
          primaryAction={{ label: "Create a project", to: "/projects" }}
        />
      ) : (
        <>
          <div className="card" style={{ marginBottom: 24 }}>
            <p
              style={{
                margin: "0 0 14px", fontSize: 12.5, color: "var(--ink-soft)", fontFamily: "var(--mono)",
                letterSpacing: ".06em", textTransform: "uppercase",
              }}
            >
              Generate for a project
            </p>
            <ul className="project-list">
              {projects.map((p) => (
                <li key={p.id} style={{ flexDirection: "row", alignItems: "center" }}>
                  <div>
                    <div className="name">{p.name}</div>
                    {(p.business_type || p.target_segment || p.avg_ticket) && (
                      <div className="biz-meta">
                        {p.business_type && <span><b>{p.business_type}</b></span>}
                        {p.target_segment && <span>{p.target_segment}</span>}
                        {p.avg_ticket != null && <span>avg ticket ₹{p.avg_ticket}</span>}
                      </div>
                    )}
                    {errors[p.id] && <div style={{ color: "var(--flame)", fontSize: 12, marginTop: 4 }}>{errors[p.id]}</div>}
                  </div>
                  <button className="btn secondary" disabled={generating === p.id} onClick={() => onGenerate(p)}>
                    {generating === p.id ? "Generating…" : "Generate report"}
                  </button>
                </li>
              ))}
            </ul>
          </div>

          {reports.length === 0 ? (
            <EmptyState
              icon="📄"
              title="No reports yet"
              description="Generate one for a project above — it'll show up here with a download link."
              bare
            />
          ) : (
            <ul className="list">
              {reports.map((r) => {
                const summary = summarize(r.params?.locations);
                return (
                  <li key={r.id} style={{ flexDirection: "column", alignItems: "stretch", gap: 6 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
                      <div>
                        <div className="primary">{r.title}</div>
                        {summary && <div className="secondary">{summary}</div>}
                      </div>
                      <div className="row-actions">
                        <span className={`pill ${STATUS_CLASS[r.status]}`}>{r.status}</span>
                        <span className="meta">{new Date(r.created_at).toLocaleDateString()}</span>
                        {r.status === "ready" && (
                          <>
                            <a className="btn secondary" href={api.reportDownloadUrl(r.id)}>
                              Download PDF
                            </a>
                            {r.share_token ? (
                              <button className="btn secondary" disabled={sharing === r.id} onClick={() => onUnshare(r)}>
                                Stop sharing
                              </button>
                            ) : (
                              <button className="btn secondary" disabled={sharing === r.id} onClick={() => onShare(r)}>
                                {sharing === r.id ? "Sharing…" : "Share"}
                              </button>
                            )}
                          </>
                        )}
                      </div>
                    </div>
                    {r.share_token && (
                      <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5 }}>
                        <span style={{ color: "var(--ink-soft)" }}>No account needed to view:</span>
                        <code style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 320 }}>
                          {api.sharedReportUrl(r.share_token)}
                        </code>
                        <button className="btn secondary" style={{ padding: "2px 8px" }} onClick={() => onCopyLink(r)}>
                          {copied === r.id ? "Copied!" : "Copy link"}
                        </button>
                      </div>
                    )}
                    {r.params?.locations && r.params.locations.length > 0 && (
                      <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                        {r.params.locations.map((loc) => (
                          <span
                            key={loc.pincode}
                            className={`pill ${RISK_CLASS[loc.risk.level] || "shortlist"}`}
                            title={loc.risk.note}
                          >
                            {loc.name} · {loc.opportunity?.suitability ?? `${loc.economic_score}/100`}
                          </span>
                        ))}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </>
      )}
    </>
  );
}

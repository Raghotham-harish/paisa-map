import { ReactNode, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, api, LocationScore, Project, Report } from "../lib/api";
import { EmptyState } from "../components/EmptyState";
import { illustrations } from "../lib/illustrations";
import { useAuth } from "../lib/auth";
import { openCheckout } from "../lib/razorpay";
import { DataList, DataRow } from "../components/DataList";
import { AsyncBoundary } from "../components/AsyncBoundary";
import { StatChip } from "../components/StatChip";
import { Pill } from "../components/Pill";

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

interface InsufficientCredits {
  balance: number;
  required: number;
  reportPurchasePricePaise: number;
}

export default function Reports() {
  const { user } = useAuth();
  const [reports, setReports] = useState<Report[] | null>(null);
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [generating, setGenerating] = useState<number | null>(null);
  const [errors, setErrors] = useState<Record<number, string>>({});
  const [insufficientFor, setInsufficientFor] = useState<Record<number, InsufficientCredits>>({});
  const [buying, setBuying] = useState<number | null>(null);
  const [sharing, setSharing] = useState<number | null>(null);
  const [shareError, setShareError] = useState<string | null>(null);
  const [copied, setCopied] = useState<number | null>(null);

  const loadReports = () => api.listReports().then((data) => setReports(data.reports));

  const loadAll = () => {
    setLoadError(null);
    Promise.all([loadReports(), api.listProjects().then((data) => setProjects(data.projects))])
      .catch(() => setLoadError("Couldn't load your reports — try again."));
  };

  useEffect(loadAll, []);

  const onGenerate = async (project: Project) => {
    setGenerating(project.id);
    setErrors((prev) => ({ ...prev, [project.id]: "" }));
    setInsufficientFor((prev) => ({ ...prev, [project.id]: undefined as any }));
    try {
      await api.generateReport(project.id);
      await loadReports();
    } catch (e) {
      if (e instanceof ApiError && e.body?.error === "insufficient_credits") {
        setInsufficientFor((prev) => ({
          ...prev,
          [project.id]: {
            balance: e.body.balance, required: e.body.required,
            reportPurchasePricePaise: e.body.report_purchase_price_paise,
          },
        }));
      } else {
        const detail =
          e instanceof ApiError && (e.body?.detail || e.body?.error)
            ? String(e.body.detail || e.body.error)
            : "Couldn't generate that report — try again.";
        setErrors((prev) => ({ ...prev, [project.id]: detail }));
      }
    } finally {
      setGenerating(null);
    }
  };

  const onBuyThisReport = async (project: Project) => {
    setBuying(project.id);
    try {
      const { razorpay_order_id, razorpay_key_id, amount_paise, order } = await api.createReportOrder(project.id);
      await openCheckout({
        key: razorpay_key_id,
        amount: amount_paise,
        currency: "INR",
        order_id: razorpay_order_id,
        description: `Report — ${project.name}`,
        prefillEmail: user?.email,
        onSuccess: async (resp) => {
          await api.verifyPayment(resp);
          await api.generateReport(project.id, undefined, order.id);
          await loadReports();
          setInsufficientFor((prev) => ({ ...prev, [project.id]: undefined as any }));
        },
        onDismiss: () => setBuying(null),
      });
    } catch {
      setErrors((prev) => ({ ...prev, [project.id]: "Couldn't complete that purchase — try again." }));
    } finally {
      setBuying(null);
    }
  };

  const onShare = async (report: Report) => {
    setSharing(report.id);
    setShareError(null);
    try {
      const { report: updated } = await api.shareReport(report.id);
      setReports((prev) => prev && prev.map((r) => (r.id === updated.id ? updated : r)));
    } catch {
      setShareError("Couldn't share that report — try again.");
    } finally {
      setSharing(null);
    }
  };

  const onUnshare = async (report: Report) => {
    setSharing(report.id);
    setShareError(null);
    try {
      const { report: updated } = await api.unshareReport(report.id);
      setReports((prev) => prev && prev.map((r) => (r.id === updated.id ? updated : r)));
    } catch {
      setShareError("Couldn't stop sharing that report — try again.");
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

      <AsyncBoundary
        loading={(reports === null || projects === null) && !loadError}
        error={loadError}
        onRetry={loadAll}
        empty={projects?.length === 0}
        emptyState={
          <EmptyState
            illustration={illustrations.dataTrends}
            title="Reports need a project first"
            description="Reports are generated for a project's saved locations — economic score, opportunity, suitability, and risk for each one, as a downloadable PDF."
            dependency="You don't have a project yet — create one to unlock report generation."
            primaryAction={{ label: "Create a project", to: "/projects" }}
          />
        }
      >
        <>
          <div className="card" style={{ marginBottom: 24 }}>
            <p className="kicker" style={{ marginBottom: 14 }}>Generate for a project</p>
            <DataList>
              {(projects ?? []).map((p) => {
                const chips: ReactNode[] = [
                  <StatChip key="locs" icon="ti ti-map-pin">
                    {p.location_count ?? 0} location{(p.location_count ?? 0) === 1 ? "" : "s"}
                  </StatChip>,
                ];
                if (p.business_type) chips.push(<b key="bt">{p.business_type}</b>);
                if (p.target_segment) chips.push(<span key="ts">{p.target_segment}</span>);
                if (p.avg_ticket != null) chips.push(<span key="at">avg ticket ₹{p.avg_ticket}</span>);
                return (
                  <DataRow
                    key={p.id}
                    title={p.name}
                    chips={chips}
                    subtitle={
                      (errors[p.id] || insufficientFor[p.id]) ? (
                        <>
                          {errors[p.id] && <div style={{ color: "var(--flame)" }}>{errors[p.id]}</div>}
                          {insufficientFor[p.id] && (
                            <div>
                              You have {insufficientFor[p.id].balance} credits, need {insufficientFor[p.id].required}.{" "}
                              <Link to="/billing">Buy credits</Link> or{" "}
                              <button
                                className="btn secondary"
                                style={{ padding: "2px 8px", fontSize: 12 }}
                                disabled={buying === p.id}
                                onClick={() => onBuyThisReport(p)}
                              >
                                {buying === p.id
                                  ? "Opening…"
                                  : `Buy this report — ₹${(insufficientFor[p.id].reportPurchasePricePaise / 100).toLocaleString("en-IN")}`}
                              </button>
                            </div>
                          )}
                        </>
                      ) : undefined
                    }
                    trailing={
                      <button className="btn secondary" disabled={generating === p.id} onClick={() => onGenerate(p)}>
                        {generating === p.id ? "Generating…" : "Generate report"}
                      </button>
                    }
                  />
                );
              })}
            </DataList>
          </div>

          {shareError && <p style={{ color: "var(--flame)", fontSize: 13, marginBottom: 14 }}>{shareError}</p>}

          <div className="card">
            <p className="kicker" style={{ marginBottom: 14 }}>Your reports</p>
            {(reports ?? []).length === 0 ? (
            <EmptyState
              icon="📄"
              title="No reports yet"
              description="Generate one for a project above — it'll show up here with a download link."
              bare
            />
          ) : (
            <DataList>
              {(reports ?? []).map((r) => {
                const summary = summarize(r.params?.locations);
                return (
                  <DataRow
                    key={r.id}
                    title={r.title}
                    subtitle={summary}
                    trailing={
                      <>
                        <Pill tone={STATUS_CLASS[r.status]}>{r.status}</Pill>
                        <StatChip icon="ti ti-calendar">{new Date(r.created_at).toLocaleDateString()}</StatChip>
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
                      </>
                    }
                    footer={
                      <>
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
                        {r.params?.digital_baseline && (
                          <div style={{ fontSize: 12.5, color: "var(--ink-soft)" }}>
                            Connected GA4: {r.params.digital_baseline.ecommerce.transactions.toLocaleString("en-IN")} transactions ·
                            {" "}₹{r.params.digital_baseline.ecommerce.purchase_revenue.toLocaleString("en-IN")} revenue (last 28 days)
                          </div>
                        )}
                        {r.params?.locations && r.params.locations.length > 0 && (
                          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                            {r.params.locations.map((loc) => (
                              <Pill
                                key={loc.pincode}
                                tone={RISK_CLASS[loc.risk.level] || "shortlist"}
                                title={
                                  loc.digital_signal
                                    ? `${loc.risk.note} — GA4: ${loc.digital_signal.sessions} sessions, ${loc.digital_signal.conversions} conversions`
                                    : loc.risk.note
                                }
                              >
                                {loc.name} · {loc.opportunity?.suitability ?? `${loc.economic_score}/100`}
                                {loc.digital_signal ? " · GA4" : ""}
                              </Pill>
                            ))}
                          </div>
                        )}
                      </>
                    }
                  />
                );
              })}
            </DataList>
          )}
          </div>
        </>
      </AsyncBoundary>
    </>
  );
}

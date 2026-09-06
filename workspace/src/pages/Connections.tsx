import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  ApiError, api, ConnectionProperty, ConnectionProvider, GA4ReportRow, GSCReportRow,
  OAuthConnection, Project,
} from "../lib/api";
import { EmptyState } from "../components/EmptyState";

const PROVIDERS: { id: ConnectionProvider; label: string; description: string }[] = [
  {
    id: "google_analytics",
    label: "Google Analytics (GA4)",
    description: "Traffic, conversions, and session geography for a connected property.",
  },
  {
    id: "search_console",
    label: "Google Search Console",
    description: "Real search queries — what people search before visiting or buying.",
  },
];

function isGA4Row(row: GA4ReportRow | GSCReportRow): row is GA4ReportRow {
  return "city" in row;
}

export default function Connections() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [projectId, setProjectId] = useState<number | null>(null);
  const [consentAt, setConsentAt] = useState<string | null>(null);
  const [connections, setConnections] = useState<OAuthConnection[]>([]);
  const [consentChecked, setConsentChecked] = useState(false);
  const [accepting, setAccepting] = useState(false);
  const [banner, setBanner] = useState<{ kind: "success" | "error"; text: string } | null>(null);
  const [propertiesByProvider, setPropertiesByProvider] = useState<Partial<Record<ConnectionProvider, ConnectionProperty[]>>>({});
  const [reportByProvider, setReportByProvider] = useState<Partial<Record<ConnectionProvider, (GA4ReportRow | GSCReportRow)[]>>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listProjects().then((data) => {
      setProjects(data.projects);
      const fromUrl = Number(searchParams.get("project_id"));
      if (fromUrl && data.projects.some((p) => p.id === fromUrl)) {
        setProjectId(fromUrl);
      } else if (data.projects.length > 0) {
        setProjectId(data.projects[0].id);
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const connected = searchParams.get("connected");
    const err = searchParams.get("error");
    if (connected) {
      setBanner({ kind: "success", text: `Connected ${connected.replace("_", " ")}.` });
    } else if (err) {
      setBanner({ kind: "error", text: `Couldn't connect — ${err.replace(/_/g, " ")}.` });
    }
    if (connected || err) setSearchParams({}, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const load = (pid: number) => {
    api.getConnections(pid).then((data) => {
      setConsentAt(data.analytics_consent_at);
      setConnections(data.connections);
    });
  };

  useEffect(() => {
    if (projectId != null) load(projectId);
  }, [projectId]);

  const onAcceptConsent = async () => {
    if (projectId == null) return;
    setAccepting(true);
    try {
      await api.acceptConnectionsConsent(projectId);
      load(projectId);
    } finally {
      setAccepting(false);
    }
  };

  const onConnect = async (provider: ConnectionProvider) => {
    if (projectId == null) return;
    setError(null);
    try {
      const { authorize_url } = await api.getConnectionAuthorizeUrl(projectId, provider);
      window.location.href = authorize_url;
    } catch (e) {
      setError(e instanceof ApiError && e.body?.detail ? String(e.body.detail) : "Couldn't start the connection.");
    }
  };

  const onLoadProperties = async (provider: ConnectionProvider) => {
    if (projectId == null) return;
    setBusy(`properties:${provider}`);
    setError(null);
    try {
      const { properties } = await api.listConnectionProperties(projectId, provider);
      setPropertiesByProvider((prev) => ({ ...prev, [provider]: properties }));
    } catch (e) {
      setError(e instanceof ApiError && e.body?.detail ? String(e.body.detail) : "Couldn't fetch properties.");
    } finally {
      setBusy(null);
    }
  };

  const onSelectProperty = async (provider: ConnectionProvider, ref: string) => {
    if (projectId == null || !ref) return;
    setBusy(`select:${provider}`);
    try {
      await api.selectConnectionProperty(projectId, provider, ref);
      load(projectId);
    } finally {
      setBusy(null);
    }
  };

  const onViewReport = async (provider: ConnectionProvider) => {
    if (projectId == null) return;
    setBusy(`report:${provider}`);
    setError(null);
    try {
      const { rows } = await api.getConnectionReport(projectId, provider);
      setReportByProvider((prev) => ({ ...prev, [provider]: rows }));
    } catch (e) {
      setError(e instanceof ApiError && e.body?.detail ? String(e.body.detail) : "Couldn't fetch the report.");
    } finally {
      setBusy(null);
    }
  };

  const onDisconnect = async (provider: ConnectionProvider) => {
    if (projectId == null) return;
    if (!confirm("Disconnect and permanently delete the stored access tokens for this connection?")) return;
    setBusy(`disconnect:${provider}`);
    try {
      await api.disconnectConnection(projectId, provider);
      setReportByProvider((prev) => ({ ...prev, [provider]: undefined }));
      setPropertiesByProvider((prev) => ({ ...prev, [provider]: undefined }));
      load(projectId);
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <h1 className="page-title">Connections</h1>
      <p className="page-sub">
        Link a project's own Google Analytics and Search Console data so PaisaMap can blend your real traffic and
        search-intent data with its own pincode-level signals.
      </p>

      {banner && (
        <p style={{
          color: banner.kind === "success" ? "var(--rupee-deep)" : "var(--flame)",
          fontSize: 13, marginBottom: 18,
        }}>
          {banner.text}
        </p>
      )}
      {error && <p style={{ color: "var(--flame)", fontSize: 13, marginBottom: 18 }}>{error}</p>}

      {projects === null ? (
        <div className="loading">Loading…</div>
      ) : projects.length === 0 ? (
        <EmptyState
          icon="🔗"
          title="Connections need a project first"
          description="Create a project, then connect its Google Analytics or Search Console account here."
          primaryAction={{ label: "Create a project", to: "/projects" }}
        />
      ) : (
        <>
          <div className="card" style={{ marginBottom: 24 }}>
            <label>
              Project
              <select value={projectId ?? ""} onChange={(e) => setProjectId(Number(e.target.value))}>
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            </label>
          </div>

          {!consentAt ? (
            <div className="card">
              <h3 style={{ marginTop: 0 }}>Data-processing consent</h3>
              <p style={{ fontSize: 13.5, color: "var(--ink-soft)", lineHeight: 1.6 }}>
                PaisaMap acts as a data processor for whatever Google account you connect below — your business
                remains the data owner. Connected data is used only to build the reports and signals you request
                inside this project, stored encrypted, and permanently deleted the moment you disconnect (not just
                revoked). Read the full{" "}
                <a href="/privacy" target="_blank" rel="noopener noreferrer">Privacy Policy</a> and{" "}
                <a href="/terms" target="_blank" rel="noopener noreferrer">Terms of Service</a>.
              </p>
              <label style={{ display: "flex", alignItems: "flex-start", gap: 8, fontSize: 13.5 }}>
                <input
                  type="checkbox"
                  checked={consentChecked}
                  onChange={(e) => setConsentChecked(e.target.checked)}
                  style={{ marginTop: 3 }}
                />
                I agree to the data-processing terms above on behalf of this project.
              </label>
              <button
                className="btn"
                style={{ marginTop: 14 }}
                disabled={!consentChecked || accepting}
                onClick={onAcceptConsent}
              >
                {accepting ? "Saving…" : "Accept & continue"}
              </button>
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
              {PROVIDERS.map(({ id, label, description }) => {
                const conn = connections.find((c) => c.provider === id);
                const properties = propertiesByProvider[id];
                const report = reportByProvider[id];
                return (
                  <div className="card" key={id}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                      <div>
                        <h3 style={{ margin: "0 0 4px" }}>{label}</h3>
                        <p style={{ fontSize: 13, color: "var(--ink-soft)", margin: 0 }}>{description}</p>
                      </div>
                      {conn ? (
                        <span className={`pill ${conn.status === "connected" ? "approved" : "rejected"}`}>
                          {conn.status === "connected" ? "Connected" : "Needs reconnect"}
                        </span>
                      ) : (
                        <span className="pill shortlist">Not connected</span>
                      )}
                    </div>

                    {!conn && (
                      <button className="btn" style={{ marginTop: 14 }} onClick={() => onConnect(id)}>
                        Connect
                      </button>
                    )}

                    {conn && (
                      <div style={{ marginTop: 14 }}>
                        {conn.external_account_email && (
                          <p style={{ fontSize: 12.5, color: "var(--ink-soft)" }}>
                            Connected as <b>{conn.external_account_email}</b>
                          </p>
                        )}
                        {conn.status === "error" && conn.last_error && (
                          <p style={{ fontSize: 12.5, color: "var(--flame)" }}>{conn.last_error}</p>
                        )}

                        {conn.status === "error" ? (
                          <button className="btn" onClick={() => onConnect(id)}>Reconnect</button>
                        ) : !conn.external_ref ? (
                          <div>
                            {properties === undefined ? (
                              <button
                                className="btn secondary"
                                disabled={busy === `properties:${id}`}
                                onClick={() => onLoadProperties(id)}
                              >
                                {busy === `properties:${id}` ? "Loading…" : "Choose a property"}
                              </button>
                            ) : properties.length === 0 ? (
                              <p style={{ fontSize: 12.5, color: "var(--ink-soft)" }}>
                                No {id === "google_analytics" ? "GA4 properties" : "Search Console sites"} found on
                                this Google account.
                              </p>
                            ) : (
                              <select
                                defaultValue=""
                                disabled={busy === `select:${id}`}
                                onChange={(e) => onSelectProperty(id, e.target.value)}
                              >
                                <option value="" disabled>Select…</option>
                                {properties.map((p) => (
                                  <option key={p.property_id || p.site_url} value={p.property_id || p.site_url}>
                                    {p.display_name || p.site_url}
                                    {p.account_name ? ` (${p.account_name})` : ""}
                                  </option>
                                ))}
                              </select>
                            )}
                          </div>
                        ) : (
                          <div>
                            <p style={{ fontSize: 12.5, color: "var(--ink-soft)" }}>
                              Using <b>{conn.external_ref}</b>
                            </p>
                            <div style={{ display: "flex", gap: 10 }}>
                              <button
                                className="btn secondary"
                                disabled={busy === `report:${id}`}
                                onClick={() => onViewReport(id)}
                              >
                                {busy === `report:${id}` ? "Loading…" : "View report (last 28 days)"}
                              </button>
                            </div>
                            {report && (
                              <div style={{ overflowX: "auto", marginTop: 12 }}>
                                <table style={{ borderCollapse: "collapse", fontSize: 12.5, width: "100%" }}>
                                  <thead>
                                    <tr>
                                      {id === "google_analytics" ? (
                                        <>
                                          <th style={thStyle}>City</th>
                                          <th style={thStyle}>Sessions</th>
                                          <th style={thStyle}>Users</th>
                                          <th style={thStyle}>Conversions</th>
                                          <th style={thStyle}>Pageviews</th>
                                        </>
                                      ) : (
                                        <>
                                          <th style={thStyle}>Query</th>
                                          <th style={thStyle}>Clicks</th>
                                          <th style={thStyle}>Impressions</th>
                                          <th style={thStyle}>CTR</th>
                                          <th style={thStyle}>Position</th>
                                        </>
                                      )}
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {report.length === 0 ? (
                                      <tr><td style={tdStyle} colSpan={5}>No data in this window yet.</td></tr>
                                    ) : (
                                      report.map((row, i) =>
                                        isGA4Row(row) ? (
                                          <tr key={i}>
                                            <td style={tdStyle}>{row.city}</td>
                                            <td style={tdStyle}>{row.sessions}</td>
                                            <td style={tdStyle}>{row.users}</td>
                                            <td style={tdStyle}>{row.conversions}</td>
                                            <td style={tdStyle}>{row.pageviews}</td>
                                          </tr>
                                        ) : (
                                          <tr key={i}>
                                            <td style={tdStyle}>{row.query}</td>
                                            <td style={tdStyle}>{row.clicks ?? "—"}</td>
                                            <td style={tdStyle}>{row.impressions ?? "—"}</td>
                                            <td style={tdStyle}>
                                              {row.ctr != null ? `${(row.ctr * 100).toFixed(1)}%` : "—"}
                                            </td>
                                            <td style={tdStyle}>{row.position?.toFixed(1) ?? "—"}</td>
                                          </tr>
                                        )
                                      )
                                    )}
                                  </tbody>
                                </table>
                              </div>
                            )}
                          </div>
                        )}

                        <button
                          className="btn secondary"
                          style={{ marginTop: 14 }}
                          disabled={busy === `disconnect:${id}`}
                          onClick={() => onDisconnect(id)}
                        >
                          {busy === `disconnect:${id}` ? "Disconnecting…" : "Disconnect"}
                        </button>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </>
      )}
    </>
  );
}

const thStyle: React.CSSProperties = {
  textAlign: "left", padding: "4px 8px", borderBottom: "1px solid var(--line)", color: "var(--ink-soft)",
};
const tdStyle: React.CSSProperties = { padding: "4px 8px", borderBottom: "1px solid var(--line)" };

import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ApiError, OAuthConnection, PROVIDER_LABEL, ProjectFields, Report, SavedLocation, api,
} from "../lib/api";
import { useWorkspace } from "../lib/workspace";
import { EmptyState } from "../components/EmptyState";
import { illustrations } from "../lib/illustrations";
import { MiniMap } from "../components/MiniMap";
import { DataList, DataRow } from "../components/DataList";
import { Pill } from "../components/Pill";
import { StatChip } from "../components/StatChip";
import { MicroBar } from "../components/MicroViz";
import { ramp } from "../components/chartTheme";
import { money } from "../components/forecastCharts";
import { BusinessFields } from "./Projects";

const CONN_STATUS_CLASS: Record<string, string> = { connected: "approved", error: "rejected" };
const REPORT_STATUS_CLASS: Record<Report["status"], string> = {
  ready: "delta-pos", processing: "reviewing", pending: "reviewing", failed: "rejected",
};

const prevKey = (pid: number) => `pm_fc_prev_${pid}`;
type PrevCurve = { budget: number; points: { investment: number; monthly_revenue: number }[]; monthly_revenue: number; stores: number };

/**
 * Project detail (N4/E4) — the hub a project row now opens into instead of
 * only expanding inline. Pulls together everything a project already has:
 * map thumbnail (B1), saved-location shortlist, latest forecast, reports,
 * connections. Two things E4 lists are deliberately NOT here yet: "activity"
 * (the backend only ever logs `project_create`/`location_save` — see
 * `_auth_db.log_activity` call sites — so a project-scoped feed would be
 * near-always-empty; not worth building on top of an unfinished log) and
 * "members with access" (Phase D — orgs/roles don't exist yet).
 */
export default function ProjectDetail() {
  const { id } = useParams<{ id: string }>();
  const projectId = Number(id);
  const navigate = useNavigate();
  const { projects, loadError, reload, setActiveProjectId } = useWorkspace();
  const project = projects?.find((p) => p.id === projectId) ?? null;

  const [locations, setLocations] = useState<SavedLocation[] | null>(null);
  const [scores, setScores] = useState<Record<string, number | null>>({});
  const [reports, setReports] = useState<Report[] | null>(null);
  const [connections, setConnections] = useState<OAuthConnection[] | null>(null);
  const [prevCurve, setPrevCurve] = useState<PrevCurve | null>(null);
  const [editing, setEditing] = useState(false);
  const [editFields, setEditFields] = useState<ProjectFields>({});
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!Number.isFinite(projectId)) return;
    setActiveProjectId(projectId);

    api.listLocations(projectId).then((data) => {
      setLocations(data.locations);
      // Bounded to this one project's own (typically handful of) locations —
      // same per-pincode scoring pattern SavedLocations/Dashboard already use.
      data.locations.slice(0, 5).forEach((l) => {
        api.getLocationScore(l.pincode)
          .then((s) => setScores((prev) => ({ ...prev, [l.pincode]: s.economic_score })))
          .catch(() => setScores((prev) => ({ ...prev, [l.pincode]: null })));
      });
    }).catch(() => setLocations([]));

    api.listReports().then((data) => setReports(data.reports.filter((r) => r.project_id === projectId))).catch(() => setReports([]));
    api.getConnections(projectId).then((data) => setConnections(data.connections)).catch(() => setConnections([]));

    // Forecast has no server-persisted "last result" — Forecast.tsx caches
    // the curve it just ran to localStorage under this same key. Reading it
    // back here is real, previously-computed data, not a fabricated number.
    try {
      const raw = localStorage.getItem(prevKey(projectId));
      setPrevCurve(raw ? JSON.parse(raw) : null);
    } catch {
      setPrevCurve(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  if (projects === null && !loadError) return <p className="loading">Loading…</p>;
  if (!project) {
    return (
      <EmptyState
        illustration={illustrations.noData}
        title="Project not found"
        description="It may have been deleted, or the link is out of date."
        primaryAction={{ label: "Back to Projects", to: "/projects" }}
      />
    );
  }

  const startEdit = () => {
    setEditFields({
      name: project.name, description: project.description || "",
      business_type: project.business_type || "", target_segment: project.target_segment || "",
      avg_ticket: project.avg_ticket ?? "", website_url: project.website_url || "",
    });
    setEditing(true);
  };

  const onSaveEdit = async () => {
    try {
      await api.updateProject(project.id, editFields);
      setEditing(false);
      reload();
    } catch {
      setError("Couldn't save changes — try again.");
    }
  };

  const onDeleteProject = async () => {
    if (!window.confirm(`Delete "${project.name}"? This removes its saved locations, reports, and connections too — it can't be undone.`)) return;
    try {
      await api.deleteProject(project.id);
      navigate("/projects");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't delete this project — try again.");
    }
  };

  const heroPoints = (locations ?? []).map((l) => ({ lat: l.lat, lng: l.lng, score: scores[l.pincode] }));

  return (
    <>
      <p className="wiz-hint" style={{ marginBottom: 6 }}>
        <Link to="/projects">← Back to Projects</Link>
      </p>

      {editing ? (
        <div className="card" style={{ marginBottom: 22 }}>
          <input
            type="text"
            placeholder="Project name"
            value={editFields.name || ""}
            onChange={(e) => setEditFields({ ...editFields, name: e.target.value })}
            style={{ marginBottom: 10, fontWeight: 700, fontSize: 15 }}
          />
          <input
            type="text"
            placeholder="Description"
            value={editFields.description || ""}
            onChange={(e) => setEditFields({ ...editFields, description: e.target.value })}
            style={{ marginBottom: 10 }}
          />
          <BusinessFields fields={editFields} onChange={setEditFields} />
          <div style={{ display: "flex", gap: 10, marginTop: 14 }}>
            <button className="btn" onClick={onSaveEdit}>Save</button>
            <button className="btn secondary" onClick={() => setEditing(false)}>Cancel</button>
          </div>
        </div>
      ) : (
        <div className="card map-hero" style={{ marginBottom: 22 }}>
          <div className="map-hero-copy">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
              <p className="map-hero-kicker">Project</p>
              <div style={{ display: "flex", gap: 12 }}>
                <button className="icon-link" style={{ background: "none", border: "none", padding: 0, font: "inherit", cursor: "pointer" }} onClick={startEdit}>
                  <i className="ti ti-pencil" /> Edit
                </button>
                <button className="icon-link" style={{ background: "none", border: "none", padding: 0, font: "inherit", cursor: "pointer" }} onClick={onDeleteProject}>
                  <i className="ti ti-trash" /> Delete
                </button>
              </div>
            </div>
            <div className="map-hero-title">{project.name}</div>
            <p className="map-hero-sub">{project.description || "No description yet."}</p>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 14 }}>
              <StatChip icon="ti ti-map-pin">{locations?.length ?? 0} locations</StatChip>
              <StatChip icon="ti ti-file-text">{reports?.length ?? 0} reports</StatChip>
              {project.business_type && <StatChip icon="ti ti-building-store">{project.business_type}</StatChip>}
              {project.website_url && (
                <a href={project.website_url} target="_blank" rel="noopener noreferrer" style={{ fontSize: 12.5 }}>
                  {project.website_url.replace(/^https?:\/\//, "")}
                </a>
              )}
            </div>
            <div className="map-hero-actions">
              <Link className="btn" to={`/map?project_id=${project.id}`}>Open on map →</Link>
              <Link className="btn secondary" to={`/forecast?project_id=${project.id}`}>Forecast</Link>
            </div>
          </div>
          <div className="map-hero-thumb">
            <MiniMap width={360} height={200} points={heroPoints} />
          </div>
        </div>
      )}
      {error && <p style={{ color: "var(--flame)", fontSize: 13, marginBottom: 18 }}>{error}</p>}

      <div className="card" style={{ marginBottom: 20 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 12 }}>
          <p className="kicker" style={{ margin: 0 }}>Saved locations</p>
          <Link to="/locations" style={{ fontSize: 12.5 }}>View all →</Link>
        </div>
        {locations === null ? (
          <p className="loading">Loading…</p>
        ) : locations.length === 0 ? (
          <EmptyState icon="📍" title="No saved locations yet" description="Save a pincode from the map to start this project's shortlist." bare />
        ) : (
          <DataList>
            {locations.slice(0, 5).map((loc) => (
              <DataRow
                key={loc.id}
                title={loc.name || loc.pincode}
                subtitle={
                  loc.pincode in scores && scores[loc.pincode] !== null ? (
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                      <MicroBar value={scores[loc.pincode]!} color={ramp("ppi", scores[loc.pincode])} />
                      <span style={{ fontFamily: "var(--mono)", color: "var(--rupee-deep)" }}>{scores[loc.pincode]}/100</span>
                    </span>
                  ) : loc.pincode
                }
                trailing={<Pill tone={loc.status}>{loc.status}</Pill>}
              />
            ))}
          </DataList>
        )}
      </div>

      <div className="card" style={{ marginBottom: 20 }}>
        <p className="kicker" style={{ marginBottom: 12 }}>Latest forecast</p>
        {prevCurve ? (
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <StatChip icon="ti ti-currency-rupee">{money(prevCurve.budget, true)} invested</StatChip>
            <StatChip icon="ti ti-trending-up">{money(prevCurve.monthly_revenue, true)}/mo reachable</StatChip>
            <StatChip icon="ti ti-building-store">{prevCurve.stores} sites</StatChip>
            <Link to={`/forecast?project_id=${project.id}`} style={{ fontSize: 12.5, marginLeft: 4 }}>Open forecast →</Link>
          </div>
        ) : (
          <EmptyState
            icon="📈"
            title="No forecast run yet"
            description="See where a budget works hardest for this project — a benchmark estimate works even without store data."
            primaryAction={{ label: "Run a forecast", to: `/forecast?project_id=${project.id}` }}
            bare
          />
        )}
      </div>

      <div className="card" style={{ marginBottom: 20 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 12 }}>
          <p className="kicker" style={{ margin: 0 }}>Reports</p>
          <Link to="/reports" style={{ fontSize: 12.5 }}>View all →</Link>
        </div>
        {reports === null ? (
          <p className="loading">Loading…</p>
        ) : reports.length === 0 ? (
          <EmptyState icon="🧾" title="No reports yet" description="Generate a PDF report for this project from a comparison or the map." bare />
        ) : (
          <DataList>
            {reports.slice(0, 3).map((r) => (
              <DataRow
                key={r.id}
                title={r.title}
                trailing={
                  <>
                    <Pill tone={REPORT_STATUS_CLASS[r.status]}>{r.status}</Pill>
                    <StatChip icon="ti ti-calendar">{new Date(r.created_at).toLocaleDateString()}</StatChip>
                  </>
                }
              />
            ))}
          </DataList>
        )}
      </div>

      <div className="card">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 12 }}>
          <p className="kicker" style={{ margin: 0 }}>Connections</p>
          <Link to="/connections" style={{ fontSize: 12.5 }}>Manage →</Link>
        </div>
        {connections === null ? (
          <p className="loading">Loading…</p>
        ) : (
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {(["google_analytics", "search_console"] as const).map((provider) => {
              const conn = connections.find((c) => c.provider === provider);
              return (
                <Pill key={provider} tone={conn ? CONN_STATUS_CLASS[conn.status] : "shortlist"}>
                  {PROVIDER_LABEL[provider]}: {conn ? (conn.status === "connected" ? "Connected" : "Needs reconnect") : "Not connected"}
                </Pill>
              );
            })}
          </div>
        )}
      </div>
    </>
  );
}

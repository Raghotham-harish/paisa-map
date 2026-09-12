import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ApiError, OAuthConnection, OrgMember, PROVIDER_LABEL, Project, ProjectFields, Report, SavedLocation, api,
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
import { UndoToastStack } from "../components/UndoToast";
import { usePendingDelete } from "../lib/undo";
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
 * connections, and (E4, now that Phase D shipped real org roles) members
 * with access. One thing E4 lists is still deliberately NOT here: "activity"
 * — the backend only ever logs `project_create`/`location_save` (see
 * `_auth_db.log_activity` call sites), so a project-scoped feed would be
 * near-always-empty; not worth building on top of an unfinished log.
 */
export default function ProjectDetail() {
  const { id } = useParams<{ id: string }>();
  const projectId = Number(id);
  const navigate = useNavigate();
  const { projects, reload, setActiveProjectId } = useWorkspace();
  // E3: WorkspaceProvider's own `projects` list excludes archived ones by
  // default, so an archived project would 404 its own detail page if this
  // relied on that list alone. `project` starts from the shared list (no
  // extra request, no flicker, for the common non-archived case) but a
  // direct fetch below is the authoritative source and covers archived too.
  const [project, setProject] = useState<Project | null>(null);
  const [projectTried, setProjectTried] = useState(false);
  const contextProject = projects?.find((p) => p.id === projectId) ?? null;

  const reloadProject = () => {
    api.getProject(projectId).then((data) => setProject(data.project)).catch(() => setProject(null));
  };

  useEffect(() => {
    if (!Number.isFinite(projectId)) return;
    setProject(contextProject);
    setProjectTried(false);
    api.getProject(projectId)
      .then((data) => setProject(data.project))
      .catch(() => setProject(null))
      .finally(() => setProjectTried(true));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const [locations, setLocations] = useState<SavedLocation[] | null>(null);
  const [scores, setScores] = useState<Record<string, number | null>>({});
  const [reports, setReports] = useState<Report[] | null>(null);
  const [connections, setConnections] = useState<OAuthConnection[] | null>(null);
  const [members, setMembers] = useState<OrgMember[] | null>(null);
  const [prevCurve, setPrevCurve] = useState<PrevCurve | null>(null);
  const [editing, setEditing] = useState(false);
  const [editFields, setEditFields] = useState<ProjectFields>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const [shareBusy, setShareBusy] = useState(false);
  const [shareCopied, setShareCopied] = useState(false);
  const { pending, remove, undo } = usePendingDelete();

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

  // Separate effect: needs project.org_id, which only exists once `projects`
  // (workspace context) has actually loaded — not available on first render.
  useEffect(() => {
    if (project?.org_id == null) { setMembers(null); return; }
    api.listOrgMembers(project.org_id).then((data) => setMembers(data.members)).catch(() => setMembers([]));
  }, [project?.org_id]);

  if (!projectTried) return <p className="loading">Loading…</p>;
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
      reloadProject();
      reload();
    } catch {
      setError("Couldn't save changes — try again.");
    }
  };

  // E3: matches Projects.tsx's own onDelete exactly — no window.confirm, the
  // 5s undo window (A5's pattern) replaces it. Deleting from the detail page
  // can't just make a row disappear like the list does, so instead the whole
  // page stays put showing the undo toast; the actual API call (and the
  // navigate-away, since there's nothing left to show once it really
  // commits) only happens once the window lapses without an Undo click.
  const onDeleteProject = () => {
    remove(project.id, `“${project.name}” will be deleted`, async () => {
      try {
        await api.deleteProject(project.id);
        navigate("/projects");
      } catch (e) {
        setError(e instanceof ApiError ? e.message : "Couldn't delete this project — try again.");
      }
    });
  };

  const onArchiveToggle = async () => {
    setBusy(true);
    setError(null);
    try {
      await (project.archived_at ? api.unarchiveProject(project.id) : api.archiveProject(project.id));
      reloadProject();
      reload();
    } catch {
      setError(`Couldn't ${project.archived_at ? "restore" : "archive"} this project — try again.`);
    } finally {
      setBusy(false);
    }
  };

  const onDuplicate = async () => {
    setBusy(true);
    setError(null);
    try {
      const { project: copy } = await api.duplicateProject(project.id);
      navigate(`/projects/${copy.id}`);
    } catch {
      setError("Couldn't duplicate this project — try again.");
      setBusy(false);
    }
  };

  const onToggleShare = async () => {
    setShareBusy(true);
    setError(null);
    try {
      if (project.share_token) await api.unshareProject(project.id);
      else await api.shareProject(project.id);
      setShareCopied(false);
      reloadProject();
    } catch {
      setError("Couldn't update sharing for this project — try again.");
    } finally {
      setShareBusy(false);
    }
  };

  const shareUrl = project.share_token
    ? `${window.location.origin}/workspace/projects/shared/${project.share_token}`
    : null;

  const onCopyShareLink = async () => {
    if (!shareUrl) return;
    try {
      await navigator.clipboard.writeText(shareUrl);
      setShareCopied(true);
    } catch {
      setError("Couldn't copy the link — select and copy it manually.");
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
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", flexWrap: "wrap", gap: 8 }}>
              <p className="map-hero-kicker">
                Project {project.archived_at && <Pill tone="shortlist">Archived</Pill>}
              </p>
              <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
                <button className="icon-link" style={{ background: "none", border: "none", padding: 0, font: "inherit", cursor: "pointer" }} onClick={startEdit}>
                  <i className="ti ti-pencil" /> Edit
                </button>
                <button className="icon-link" style={{ background: "none", border: "none", padding: 0, font: "inherit", cursor: "pointer" }} disabled={busy} onClick={onDuplicate}>
                  <i className="ti ti-copy" /> Duplicate
                </button>
                <button className="icon-link" style={{ background: "none", border: "none", padding: 0, font: "inherit", cursor: "pointer" }} disabled={busy} onClick={onArchiveToggle}>
                  <i className={project.archived_at ? "ti ti-archive-off" : "ti ti-archive"} /> {project.archived_at ? "Restore" : "Archive"}
                </button>
                {(project.role === "owner" || project.role === "admin") && (
                  <button className="icon-link" style={{ background: "none", border: "none", padding: 0, font: "inherit", cursor: "pointer" }} onClick={() => setShareOpen((v) => !v)}>
                    <i className="ti ti-share" /> Share
                  </button>
                )}
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
            {shareOpen && (
              <div className="card" style={{ background: "var(--paper-3)", marginBottom: 14, padding: 12 }}>
                {project.share_token ? (
                  <>
                    <p style={{ fontSize: 12.5, marginBottom: 8 }}>
                      Anyone with this link can view a read-only summary — name, description, saved locations, and
                      finished report titles. No downloads, no financial figures.
                    </p>
                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                      <input type="text" readOnly value={shareUrl ?? ""} style={{ flex: 1, minWidth: 200, fontSize: 12 }} onFocus={(e) => e.target.select()} />
                      <button className="btn secondary" onClick={onCopyShareLink}>{shareCopied ? "Copied!" : "Copy link"}</button>
                      <button className="btn secondary" disabled={shareBusy} onClick={onToggleShare}>Stop sharing</button>
                    </div>
                  </>
                ) : (
                  <>
                    <p style={{ fontSize: 12.5, marginBottom: 8 }}>
                      Create a public read-only link — no PaisaMap account needed to view it. Company members already
                      see this project without one; use this for anyone outside the company.
                    </p>
                    <button className="btn" disabled={shareBusy} onClick={onToggleShare}>Create public link</button>
                  </>
                )}
              </div>
            )}
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

      <div className="card" style={{ marginBottom: 20 }}>
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

      {/* E4 — every org member already has access to every one of the
          company's projects (Phase D2's read policy), so this is purely
          informational: who can see this, not a per-project ACL to edit.
          Invite a new person via Company Settings, not here. */}
      <div className="card">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 12 }}>
          <p className="kicker" style={{ margin: 0 }}>Members with access</p>
          <Link to="/company" style={{ fontSize: 12.5 }}>Manage company →</Link>
        </div>
        {members === null ? (
          <p className="loading">Loading…</p>
        ) : members.length === 0 ? (
          <p style={{ color: "var(--ink-soft)", fontSize: 13 }}>Just you — no other company members yet.</p>
        ) : (
          <DataList>
            {members.map((m) => (
              <DataRow key={m.user_id} title={m.name || m.email} subtitle={m.name ? m.email : undefined} trailing={<StatChip icon="ti ti-user-circle">{m.role}</StatChip>} />
            ))}
          </DataList>
        )}
      </div>

      <UndoToastStack toasts={pending ? [{ key: "project", label: pending.label, onUndo: undo }] : []} />
    </>
  );
}

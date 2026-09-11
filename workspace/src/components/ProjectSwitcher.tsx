import { Link } from "react-router-dom";
import { useWorkspace } from "../lib/workspace";

/** Persistent project switcher for the top bar — the one place every page now reads its active project from. */
export function ProjectSwitcher() {
  const { projects, activeProjectId, setActiveProjectId, activeProject, loadError } = useWorkspace();

  if (loadError) return null; // the page body's own AsyncBoundary already surfaces this with a retry
  if (projects === null) return <span className="topbar-switcher-loading skeleton-line" aria-hidden="true" />;
  if (projects.length === 0) {
    return (
      <Link className="topbar-new-project" to="/projects/new">
        <i className="ti ti-plus" aria-hidden="true" /> New project
      </Link>
    );
  }

  return (
    <div className="topbar-switcher">
      <span className="topbar-switcher-label">Project</span>
      <select value={activeProjectId ?? ""} onChange={(e) => setActiveProjectId(Number(e.target.value))}>
        {projects.map((p) => (
          <option key={p.id} value={p.id}>{p.name}</option>
        ))}
      </select>
      {activeProject && (
        <span className="topbar-switcher-meta">
          {activeProject.location_count ?? 0} location{(activeProject.location_count ?? 0) === 1 ? "" : "s"}
          {" · updated "}{new Date(activeProject.updated_at).toLocaleDateString()}
        </span>
      )}
    </div>
  );
}

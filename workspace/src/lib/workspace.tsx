import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { api, Project } from "./api";

// Global "active project" context — replaces the per-page `<select>` that
// Forecast, Connections, and Store Data each used to keep independently (you'd
// switch project on one screen, open another, and land back on projects[0]).
// One shared load of the project list, one shared active id, persisted across
// reloads and read by every page that needs a project. A `?project_id=` deep
// link still wins on arrival — see useSyncProjectFromUrl in App.tsx — and
// writes back into this context so it "sticks" as you navigate onward.
const STORAGE_KEY = "pm.workspace.v1";

function readStoredProjectId(): number | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return typeof parsed.activeProjectId === "number" ? parsed.activeProjectId : null;
  } catch {
    return null;
  }
}

function writeStoredProjectId(id: number | null) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ activeProjectId: id }));
  } catch {
    // Private-mode/disabled storage — the switcher still works within the session, just doesn't persist.
  }
}

interface WorkspaceState {
  projects: Project[] | null;
  loadError: string | null;
  reload: () => void;
  activeProjectId: number | null;
  setActiveProjectId: (id: number | null) => void;
  activeProject: Project | null;
}

const WorkspaceContext = createContext<WorkspaceState | null>(null);

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [activeProjectId, setActiveProjectIdState] = useState<number | null>(readStoredProjectId);

  const load = () => {
    setLoadError(null);
    api.listProjects()
      .then((data) => {
        setProjects(data.projects);
        setActiveProjectIdState((prev) => {
          if (prev != null && data.projects.some((p) => p.id === prev)) return prev;
          const fallback = data.projects[0]?.id ?? null;
          writeStoredProjectId(fallback);
          return fallback;
        });
      })
      .catch(() => setLoadError("Couldn't load your projects — try again."));
  };

  useEffect(load, []);

  const setActiveProjectId = (id: number | null) => {
    setActiveProjectIdState(id);
    writeStoredProjectId(id);
  };

  const activeProject = projects?.find((p) => p.id === activeProjectId) ?? null;

  return (
    <WorkspaceContext.Provider value={{ projects, loadError, reload: load, activeProjectId, setActiveProjectId, activeProject }}>
      {children}
    </WorkspaceContext.Provider>
  );
}

export function useWorkspace() {
  const ctx = useContext(WorkspaceContext);
  if (!ctx) throw new Error("useWorkspace must be used inside WorkspaceProvider");
  return ctx;
}

/**
 * A `?project_id=` in the URL wins on arrival and writes back into the shared
 * context, so it "sticks" as you navigate onward (N2 in the dashboard audit).
 * Mount once, inside <WorkspaceProvider>.
 */
export function useSyncProjectFromUrl() {
  const { projects, setActiveProjectId } = useWorkspace();
  const location = useLocation();

  useEffect(() => {
    if (!projects) return;
    const params = new URLSearchParams(location.search);
    const raw = params.get("project_id");
    if (!raw) return;
    const id = Number(raw);
    if (id && projects.some((p) => p.id === id)) setActiveProjectId(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.search, projects]);
}

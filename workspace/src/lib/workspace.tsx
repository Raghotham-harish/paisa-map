import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { api, Organization, Project } from "./api";

// Global "active project" context — replaces the per-page `<select>` that
// Forecast, Connections, and Store Data each used to keep independently (you'd
// switch project on one screen, open another, and land back on projects[0]).
// One shared load of the project list, one shared active id, persisted across
// reloads and read by every page that needs a project. A `?project_id=` deep
// link still wins on arrival — see useSyncProjectFromUrl in App.tsx — and
// writes back into this context so it "sticks" as you navigate onward.
//
// Phase C (C4): activeOrgId joins activeProjectId here rather than in a
// second provider — same "one shared load, one shared active id" shape, and
// every page that already reads useWorkspace() gets company context for
// free. Project *listing* is deliberately NOT filtered by activeOrgId yet —
// every real account today has exactly one company (C3's backfill), so
// there is nothing to verify that filter against; new projects DO get
// tagged with activeOrgId at creation time (see ProjectWizard/Projects),
// so the data is correct and ready for that filter once a second real
// company exists to test it against.
const STORAGE_KEY = "pm.workspace.v1";

function readStoredIds(): { activeProjectId: number | null; activeOrgId: number | null } {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { activeProjectId: null, activeOrgId: null };
    const parsed = JSON.parse(raw);
    return {
      activeProjectId: typeof parsed.activeProjectId === "number" ? parsed.activeProjectId : null,
      activeOrgId: typeof parsed.activeOrgId === "number" ? parsed.activeOrgId : null,
    };
  } catch {
    return { activeProjectId: null, activeOrgId: null };
  }
}

function writeStoredIds(activeProjectId: number | null, activeOrgId: number | null) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ activeProjectId, activeOrgId }));
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
  organizations: Organization[] | null;
  orgLoadError: string | null;
  reloadOrgs: () => void;
  activeOrgId: number | null;
  setActiveOrgId: (id: number | null) => void;
  activeOrg: Organization | null;
}

const WorkspaceContext = createContext<WorkspaceState | null>(null);

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [organizations, setOrganizations] = useState<Organization[] | null>(null);
  const [orgLoadError, setOrgLoadError] = useState<string | null>(null);
  const stored = readStoredIds();
  const [activeProjectId, setActiveProjectIdState] = useState<number | null>(stored.activeProjectId);
  const [activeOrgId, setActiveOrgIdState] = useState<number | null>(stored.activeOrgId);

  const load = () => {
    setLoadError(null);
    api.listProjects()
      .then((data) => {
        setProjects(data.projects);
        setActiveProjectIdState((prev) => {
          if (prev != null && data.projects.some((p) => p.id === prev)) return prev;
          const fallback = data.projects[0]?.id ?? null;
          writeStoredIds(fallback, activeOrgId);
          return fallback;
        });
      })
      .catch(() => setLoadError("Couldn't load your projects — try again."));
  };

  const loadOrgs = () => {
    setOrgLoadError(null);
    api.listOrganizations()
      .then((data) => {
        setOrganizations(data.organizations);
        setActiveOrgIdState((prev) => {
          if (prev != null && data.organizations.some((o) => o.id === prev)) return prev;
          const fallback = data.organizations[0]?.id ?? null;
          writeStoredIds(activeProjectId, fallback);
          return fallback;
        });
      })
      .catch(() => setOrgLoadError("Couldn't load your companies — try again."));
  };

  useEffect(load, []);
  useEffect(loadOrgs, []);

  const setActiveProjectId = (id: number | null) => {
    setActiveProjectIdState(id);
    writeStoredIds(id, activeOrgId);
  };

  const setActiveOrgId = (id: number | null) => {
    setActiveOrgIdState(id);
    writeStoredIds(activeProjectId, id);
  };

  const activeProject = projects?.find((p) => p.id === activeProjectId) ?? null;
  const activeOrg = organizations?.find((o) => o.id === activeOrgId) ?? null;

  return (
    <WorkspaceContext.Provider value={{
      projects, loadError, reload: load, activeProjectId, setActiveProjectId, activeProject,
      organizations, orgLoadError, reloadOrgs: loadOrgs, activeOrgId, setActiveOrgId, activeOrg,
    }}>
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

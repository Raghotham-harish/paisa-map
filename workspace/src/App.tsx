import { Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "./lib/auth";
import { WorkspaceProvider, useSyncProjectFromUrl } from "./lib/workspace";
import { ProjectSwitcher } from "./components/ProjectSwitcher";
import SignIn from "./pages/SignIn";
import Dashboard from "./pages/Dashboard";
import MapWorkspace from "./pages/MapWorkspace";
import Projects from "./pages/Projects";
import ProjectDetail from "./pages/ProjectDetail";
import ProjectWizard from "./pages/ProjectWizard";
import SavedLocations from "./pages/SavedLocations";
import Activity from "./pages/Activity";
import Billing from "./pages/Billing";
import Reports from "./pages/Reports";
import CustomerData from "./pages/CustomerData";
import Connections from "./pages/Connections";
import Forecast from "./pages/Forecast";
import ApiKeys from "./pages/ApiKeys";

function WorkspaceUrlSync() {
  useSyncProjectFromUrl();
  return null;
}

// Grouped per the dashboard audit's N6/A7: 12 flat items didn't scale and
// gave no sense of what's what. "Workspace" = explore/build, "Data" = feed
// your own data in, "Account" = you and the relationship with PaisaMap.
// Credits folded into Billing (N5: "two nav items that are both money").
const NAV_SECTIONS: { label: string; items: { to: string; label: string; end?: boolean; icon: string }[] }[] = [
  {
    label: "Workspace",
    items: [
      { to: "/", label: "Dashboard", end: true, icon: "ti-layout-dashboard" },
      { to: "/map", label: "Map workspace", icon: "ti-map-2" },
      { to: "/projects", label: "Projects", icon: "ti-briefcase" },
      { to: "/locations", label: "Saved Locations", icon: "ti-map-pin" },
      { to: "/forecast", label: "Forecast", icon: "ti-trending-up" },
      { to: "/reports", label: "Reports", icon: "ti-file-text" },
    ],
  },
  {
    label: "Data",
    items: [
      { to: "/customer-data", label: "Store Data", icon: "ti-upload" },
      { to: "/connections", label: "Connections", icon: "ti-plug" },
    ],
  },
  {
    label: "Account",
    items: [
      { to: "/activity", label: "Activity", icon: "ti-activity" },
      { to: "/billing", label: "Billing", icon: "ti-receipt" },
      { to: "/api-keys", label: "API Keys", icon: "ti-key" },
    ],
  },
];

export default function App() {
  const { user, loading, signOut } = useAuth();
  const location = useLocation();
  const bleed = location.pathname === "/map";
  // The forecast + compare dashboards need more than the default 880px column.
  const wide = location.pathname === "/forecast";

  if (loading) return <div className="loading">Loading…</div>;
  if (!user) return <SignIn />;

  const initial = (user.name || user.email || "?").trim()[0]?.toUpperCase() || "?";

  return (
    <WorkspaceProvider>
      <WorkspaceUrlSync />
      <div className="shell">
        <aside className="sidenav">
          <a className="brand" href="/">
            <img src="/assets/logo-horizontal-v2.svg" alt="PaisaMaps" height="24" />
          </a>
          <nav>
            {NAV_SECTIONS.map((section) => (
              <div className="sidenav-section" key={section.label}>
                <div className="sidenav-section-label">{section.label}</div>
                {section.items.map((item) => (
                  <NavLink key={item.to} to={item.to} end={item.end} className={({ isActive }) => (isActive ? "active" : "")}>
                    <i className={`ti ${item.icon}`} aria-hidden="true" />
                    <span>{item.label}</span>
                  </NavLink>
                ))}
              </div>
            ))}
          </nav>

          <div className="sidenav-user">
            {user.picture_url ? (
              <img className="avatar" src={user.picture_url} alt="" />
            ) : (
              <div className="avatar avatar-fallback">{initial}</div>
            )}
            <div className="user-info">
              <div className="user-name">{user.name || user.email}</div>
              <span className={`pill plan-${user.plan}`}>{user.plan}</span>
            </div>
          </div>
          <a className="back" href="/">
            <i className="ti ti-arrow-left" aria-hidden="true" /> Back to map
          </a>
          <a
            className="back"
            href="#"
            onClick={(e) => {
              e.preventDefault();
              signOut();
            }}
          >
            <i className="ti ti-logout" aria-hidden="true" /> Sign out
          </a>
        </aside>
        <div className="workspace-col">
          {!bleed && (
            <header className="topbar">
              <ProjectSwitcher />
            </header>
          )}
          <main className={bleed ? "content content-bleed" : wide ? "content content-wide" : "content"}>
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/map" element={<MapWorkspace />} />
              <Route path="/projects" element={<Projects />} />
              <Route path="/projects/new" element={<ProjectWizard />} />
              <Route path="/projects/:id" element={<ProjectDetail />} />
              <Route path="/locations" element={<SavedLocations />} />
              <Route path="/customer-data" element={<CustomerData />} />
              <Route path="/forecast" element={<Forecast />} />
              <Route path="/connections" element={<Connections />} />
              <Route path="/reports" element={<Reports />} />
              <Route path="/activity" element={<Activity />} />
              <Route path="/credits" element={<Navigate to="/billing" replace />} />
              <Route path="/billing" element={<Billing />} />
              <Route path="/api-keys" element={<ApiKeys />} />
            </Routes>
          </main>
        </div>
      </div>
    </WorkspaceProvider>
  );
}

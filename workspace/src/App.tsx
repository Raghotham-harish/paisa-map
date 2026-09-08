import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "./lib/auth";
import SignIn from "./pages/SignIn";
import Dashboard from "./pages/Dashboard";
import MapWorkspace from "./pages/MapWorkspace";
import Projects from "./pages/Projects";
import ProjectWizard from "./pages/ProjectWizard";
import SavedLocations from "./pages/SavedLocations";
import Activity from "./pages/Activity";
import Credits from "./pages/Credits";
import Billing from "./pages/Billing";
import Reports from "./pages/Reports";
import CustomerData from "./pages/CustomerData";
import Connections from "./pages/Connections";
import Forecast from "./pages/Forecast";
import ApiKeys from "./pages/ApiKeys";

const NAV = [
  { to: "/", label: "Dashboard", end: true, icon: "ti-layout-dashboard" },
  { to: "/map", label: "Map workspace", icon: "ti-map-2" },
  { to: "/projects", label: "Projects", icon: "ti-briefcase" },
  { to: "/locations", label: "Saved Locations", icon: "ti-map-pin" },
  { to: "/customer-data", label: "Store Data", icon: "ti-upload" },
  { to: "/forecast", label: "Forecast", icon: "ti-trending-up" },
  { to: "/connections", label: "Connections", icon: "ti-plug" },
  { to: "/reports", label: "Reports", icon: "ti-file-text" },
  { to: "/activity", label: "Activity", icon: "ti-activity" },
  { to: "/credits", label: "Credits", icon: "ti-coin" },
  { to: "/billing", label: "Billing", icon: "ti-receipt" },
  { to: "/api-keys", label: "API Keys", icon: "ti-key" },
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
    <div className="shell">
      <aside className="sidenav">
        <a className="brand" href="/">
          <img src="/assets/logo-horizontal-v2.svg" alt="PaisaMaps" height="24" />
        </a>
        <nav>
          {NAV.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end} className={({ isActive }) => (isActive ? "active" : "")}>
              <i className={`ti ${item.icon}`} aria-hidden="true" />
              <span>{item.label}</span>
            </NavLink>
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
      <main className={bleed ? "content content-bleed" : wide ? "content content-wide" : "content"}>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/map" element={<MapWorkspace />} />
          <Route path="/projects" element={<Projects />} />
          <Route path="/projects/new" element={<ProjectWizard />} />
          <Route path="/locations" element={<SavedLocations />} />
          <Route path="/customer-data" element={<CustomerData />} />
          <Route path="/forecast" element={<Forecast />} />
          <Route path="/connections" element={<Connections />} />
          <Route path="/reports" element={<Reports />} />
          <Route path="/activity" element={<Activity />} />
          <Route path="/credits" element={<Credits />} />
          <Route path="/billing" element={<Billing />} />
          <Route path="/api-keys" element={<ApiKeys />} />
        </Routes>
      </main>
    </div>
  );
}

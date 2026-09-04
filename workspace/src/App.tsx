import { NavLink, Route, Routes } from "react-router-dom";
import { useAuth } from "./lib/auth";
import SignIn from "./pages/SignIn";
import Dashboard from "./pages/Dashboard";
import Projects from "./pages/Projects";
import SavedLocations from "./pages/SavedLocations";
import Activity from "./pages/Activity";
import Credits from "./pages/Credits";
import Reports from "./pages/Reports";

const NAV = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/projects", label: "Projects" },
  { to: "/locations", label: "Saved Locations" },
  { to: "/reports", label: "Reports" },
  { to: "/activity", label: "Activity" },
  { to: "/credits", label: "Credits" },
];

export default function App() {
  const { user, loading, signOut } = useAuth();

  if (loading) return <div className="loading">Loading…</div>;
  if (!user) return <SignIn />;

  return (
    <div className="shell">
      <aside className="sidenav">
        <a className="brand" href="/">
          PaisaMap
        </a>
        <nav>
          {NAV.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end} className={({ isActive }) => (isActive ? "active" : "")}>
              {item.label}
            </NavLink>
          ))}
        </nav>
        <a className="back" href="/">
          ← Back to map
        </a>
        <a
          className="back"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            signOut();
          }}
        >
          Sign out
        </a>
      </aside>
      <main className="content">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/projects" element={<Projects />} />
          <Route path="/locations" element={<SavedLocations />} />
          <Route path="/reports" element={<Reports />} />
          <Route path="/activity" element={<Activity />} />
          <Route path="/credits" element={<Credits />} />
        </Routes>
      </main>
    </div>
  );
}

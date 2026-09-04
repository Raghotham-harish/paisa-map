import { NavLink, Route, Routes } from "react-router-dom";
import { useAuth } from "./lib/auth";
import SignIn from "./pages/SignIn";
import Dashboard from "./pages/Dashboard";
import Projects from "./pages/Projects";

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
          <NavLink to="/" end className={({ isActive }) => (isActive ? "active" : "")}>
            Dashboard
          </NavLink>
          <NavLink to="/projects" className={({ isActive }) => (isActive ? "active" : "")}>
            Projects
          </NavLink>
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
        </Routes>
      </main>
    </div>
  );
}

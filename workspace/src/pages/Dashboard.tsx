import { useAuth } from "../lib/auth";

export default function Dashboard() {
  const { user } = useAuth();
  if (!user) return null;

  return (
    <>
      <h1 className="page-title">Welcome back{user.name ? `, ${user.name.split(" ")[0]}` : ""}</h1>
      <p className="page-sub">{user.email}</p>

      <div className="stat-row">
        <div className="stat-tile">
          <div className="label">Plan</div>
          <div className="value plan">{user.plan}</div>
        </div>
        <div className="stat-tile">
          <div className="label">Credits</div>
          <div className="value">{user.credits}</div>
        </div>
      </div>

      <div className="card">
        <p style={{ margin: 0, color: "var(--ink-soft)", fontSize: 13.5 }}>
          Saved locations, reports, and activity history land in Phase 1. For now,
          head to <strong>Projects</strong> to create your first project — that's
          the container everything else attaches to.
        </p>
      </div>
    </>
  );
}

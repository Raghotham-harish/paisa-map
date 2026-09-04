import { useEffect, useState } from "react";
import { useAuth } from "../lib/auth";
import { api, ActivityEntry, SavedLocation } from "../lib/api";

const ACTION_LABELS: Record<string, string> = {
  login: "Signed in",
  project_create: "Created project",
  location_save: "Saved a location",
};

function describe(entry: ActivityEntry): string {
  const label = ACTION_LABELS[entry.action] || entry.action;
  const pincode = (entry.metadata as any)?.pincode;
  return pincode ? `${label} — ${pincode}` : label;
}

export default function Dashboard() {
  const { user } = useAuth();
  const [activity, setActivity] = useState<ActivityEntry[] | null>(null);
  const [locations, setLocations] = useState<SavedLocation[] | null>(null);

  useEffect(() => {
    api.listActivity(5).then((data) => setActivity(data.activity));
    api.listLocations().then((data) => setLocations(data.locations));
  }, []);

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
        <div className="stat-tile">
          <div className="label">Saved locations</div>
          <div className="value">{locations === null ? "—" : locations.length}</div>
        </div>
      </div>

      <div className="card">
        <p style={{ margin: "0 0 14px", fontSize: 12.5, color: "var(--ink-soft)", fontFamily: "var(--mono)", letterSpacing: ".06em", textTransform: "uppercase" }}>
          Recent activity
        </p>
        {activity === null ? (
          <p style={{ margin: 0, color: "var(--ink-soft)", fontSize: 13.5 }}>Loading…</p>
        ) : activity.length === 0 ? (
          <p style={{ margin: 0, color: "var(--ink-soft)", fontSize: 13.5 }}>
            Nothing yet — save a location on the map or create a project to get started.
          </p>
        ) : (
          <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 8 }}>
            {activity.map((entry) => (
              <li key={entry.id} style={{ display: "flex", justifyContent: "space-between", fontSize: 13.5 }}>
                <span>{describe(entry)}</span>
                <span style={{ color: "var(--ink-soft)", fontSize: 12 }}>{new Date(entry.created_at).toLocaleDateString()}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
}

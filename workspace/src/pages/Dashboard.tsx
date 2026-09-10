import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { api, ActivityEntry, Project, SavedLocation } from "../lib/api";
import { EmptyState } from "../components/EmptyState";
import { illustrations } from "../lib/illustrations";
import { MapSessionState, readMapSession } from "../lib/mapSession";

const ACTION_LABELS: Record<string, string> = {
  login: "Signed in",
  project_create: "Created project",
  location_save: "Saved a location",
  location_score: "Scored a location",
  location_compare: "Compared locations",
  forecast_run: "Ran a forecast",
  report_generate: "Generated a report",
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
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [session, setSession] = useState<MapSessionState | null>(null);

  useEffect(() => {
    api.listActivity(5).then((data) => setActivity(data.activity));
    api.listLocations().then((data) => setLocations(data.locations));
    api.listProjects().then((data) => setProjects(data.projects));
    setSession(readMapSession());
  }, []);

  if (!user) return null;

  // Prefer whatever project the map session was last on (real "pick up where
  // you left off"), falling back to the first project when there's no
  // session yet or it points at a project that's since been deleted.
  const sessionProject = session?.projectId != null ? (projects?.find((p) => p.id === session.projectId) ?? null) : null;
  const activeProject = sessionProject ?? projects?.[0] ?? null;

  // Every project can get a real forecast now (a benchmark one by default,
  // sharper once store data is uploaded) — no more "not enough data" wall.
  const forecastReady = activeProject != null;
  const hasRecentSelection = session?.pincode != null;

  return (
    <>
      <h1 className="page-title">Welcome back{user.name ? `, ${user.name.split(" ")[0]}` : ""}</h1>
      <p className="page-sub">{user.email}</p>

      <div className="card map-hero">
        <div className="map-hero-copy">
          <p className="map-hero-kicker">Your map workspace</p>
          <div className="map-hero-title">
            {activeProject ? activeProject.name : "Start with the map"}
          </div>
          <p className="map-hero-sub">
            {hasRecentSelection ? (
              <>
                Last viewed <b>{session?.name || session?.pincode}</b>
                {(session?.compareCount ?? 0) > 0 ? ` · ${session?.compareCount} in your compare basket` : ""}
                {forecastReady ? " · forecast ready" : ""}.
              </>
            ) : locations && locations.length > 0 ? (
              `${locations.length} saved location${locations.length > 1 ? "s" : ""} · pick up where you left off.`
            ) : (
              "Explore purchasing power, score any pincode, and build a shortlist — all on the map."
            )}
          </p>
          <div className="map-hero-actions">
            <Link className="btn" to={hasRecentSelection ? `/map?project_id=${activeProject?.id ?? ""}&pincode=${session?.pincode}` : "/map"}>
              {hasRecentSelection ? "Resume on the map →" : "Open map workspace →"}
            </Link>
            {activeProject && (
              <Link className="btn secondary" to={`/forecast?project_id=${activeProject.id}`}>
                Resume forecast
              </Link>
            )}
            {!activeProject && <Link className="btn secondary" to="/projects/new">New project</Link>}
          </div>
        </div>
        <div className="map-hero-thumb" aria-hidden="true">
          <svg width="100%" height="100%" viewBox="0 0 360 200" preserveAspectRatio="xMidYMid slice">
            <rect width="360" height="200" fill="#EDEFE7" />
            <g stroke="#C9CEBF" strokeWidth="2" fill="none">
              <path d="M-10 60 C 120 40 240 90 370 50" />
              <path d="M-10 130 C 130 110 260 150 370 120" />
              <path d="M110 -10 C 130 70 100 130 150 210" />
              <path d="M250 -10 C 240 70 270 130 250 210" />
            </g>
            <path d="M300 200 L360 200 L360 92 q-40 20 -40 55 q-2 30 -20 26z" fill="#CAD8D2" />
            <circle cx="180" cy="100" r="46" fill="#216A0B" fillOpacity="0.16" />
            <g fill="#216A0B" stroke="#fff" strokeWidth="2">
              <circle cx="180" cy="96" r="17" />
              <circle cx="120" cy="70" r="12" />
              <circle cx="238" cy="120" r="13" />
              <circle cx="150" cy="140" r="9" />
            </g>
            <circle cx="205" cy="82" r="16" fill="none" stroke="#DFAE3A" strokeWidth="1.5" />
          </svg>
        </div>
      </div>

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
        <div className="stat-tile">
          <div className="label">Projects</div>
          <div className="value">{projects === null ? "—" : projects.length}</div>
        </div>
      </div>

      <div className="card">
        <p style={{ margin: "0 0 14px", fontSize: 12.5, color: "var(--ink-soft)", fontFamily: "var(--mono)", letterSpacing: ".06em", textTransform: "uppercase" }}>
          Recent activity
        </p>
        {activity === null ? (
          <p style={{ margin: 0, color: "var(--ink-soft)", fontSize: 13.5 }}>Loading…</p>
        ) : activity.length === 0 ? (
          <EmptyState
            bare
            illustration={illustrations.noData}
            title="Let's get you started"
            description="Save a location from the map or create a project — either one puts you on the board here."
            primaryAction={{ label: "Open the map", href: "/" }}
            secondaryAction={{ label: "Create a project", to: "/projects" }}
          />
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

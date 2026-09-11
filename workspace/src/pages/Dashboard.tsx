import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { api, ActivityEntry, SavedLocation } from "../lib/api";
import { EmptyState } from "../components/EmptyState";
import { illustrations } from "../lib/illustrations";
import { MapSessionState, readMapSession } from "../lib/mapSession";
import { useWorkspace } from "../lib/workspace";
import { MiniMap } from "../components/MiniMap";
import { AsyncBoundary } from "../components/AsyncBoundary";
import { DataList, DataRow } from "../components/DataList";
import { StatChip } from "../components/StatChip";
import { describeActivity } from "../lib/activity";

export default function Dashboard() {
  const { user } = useAuth();
  const { projects } = useWorkspace();
  const [activity, setActivity] = useState<ActivityEntry[] | null>(null);
  const [activityError, setActivityError] = useState<string | null>(null);
  const [locations, setLocations] = useState<SavedLocation[] | null>(null);
  const [session, setSession] = useState<MapSessionState | null>(null);
  const [thumbScores, setThumbScores] = useState<Record<string, number | null>>({});

  const loadActivity = () => {
    setActivityError(null);
    setActivity(null);
    api.listActivity(5).then((data) => setActivity(data.activity)).catch(() => setActivityError("Couldn't load your recent activity — try again."));
  };

  useEffect(() => {
    loadActivity();
    api.listLocations().then((data) => setLocations(data.locations)).catch(() => setLocations([]));
    setSession(readMapSession());
  }, []);

  // Prefer whatever project the map session was last on (real "pick up where
  // you left off"), falling back to the first project when there's no
  // session yet or it points at a project that's since been deleted.
  const sessionProject = session?.projectId != null ? (projects?.find((p) => p.id === session.projectId) ?? null) : null;
  const activeProject = sessionProject ?? projects?.[0] ?? null;
  const heroLocations = locations?.filter((l) => l.project_id === activeProject?.id) ?? [];

  useEffect(() => {
    // Bounded to the active project's own (typically handful of) locations —
    // reuses the same per-pincode scoring endpoint SavedLocations calls, so
    // the hero thumbnail tints with the map's real ramp instead of a guess.
    heroLocations.forEach((l) => {
      if (l.pincode in thumbScores) return;
      api.getLocationScore(l.pincode)
        .then((s) => setThumbScores((prev) => ({ ...prev, [l.pincode]: s.economic_score })))
        .catch(() => setThumbScores((prev) => ({ ...prev, [l.pincode]: null })));
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeProject?.id, locations]);

  if (!user) return null;

  // Real "added this week" counts from data already on hand — not a fabricated
  // trend, just a client-side filter on created_at (no history endpoint exists
  // or is needed for this).
  const weekAgo = Date.now() - 7 * 24 * 60 * 60 * 1000;
  const newLocations = locations?.filter((l) => new Date(l.created_at).getTime() > weekAgo).length ?? 0;
  const newProjects = projects?.filter((p) => new Date(p.created_at).getTime() > weekAgo).length ?? 0;

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
        <div className="map-hero-thumb">
          <MiniMap
            width={360}
            height={200}
            points={heroLocations.map((l) => ({ lat: l.lat, lng: l.lng, score: thumbScores[l.pincode] }))}
          />
        </div>
      </div>

      <div className="stat-row">
        <Link className="stat-tile stat-tile-link" to="/billing">
          <div className="label">Plan</div>
          <div className="value plan">{user.plan}</div>
        </Link>
        <Link className="stat-tile stat-tile-link" to="/billing">
          <div className="label">Credits</div>
          <div className="value">{user.credits}</div>
        </Link>
        <Link className="stat-tile stat-tile-link" to="/locations">
          <div className="label">Saved locations</div>
          <div className="value">{locations === null ? "—" : locations.length}</div>
          {newLocations > 0 && <div className="stat-tile-delta">+{newLocations} this week</div>}
        </Link>
        <Link className="stat-tile stat-tile-link" to="/projects">
          <div className="label">Projects</div>
          <div className="value">{projects === null ? "—" : projects.length}</div>
          {newProjects > 0 && <div className="stat-tile-delta">+{newProjects} this week</div>}
        </Link>
      </div>

      <div className="card">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 14 }}>
          <p className="kicker" style={{ margin: 0 }}>Recent activity</p>
          <Link to="/activity" style={{ fontSize: 12.5 }}>View all →</Link>
        </div>
        <AsyncBoundary
          loading={activity === null && !activityError}
          error={activityError}
          onRetry={loadActivity}
          empty={activity?.length === 0}
          emptyState={
            <EmptyState
              bare
              illustration={illustrations.noData}
              title="Let's get you started"
              description="Save a location from the map or create a project — either one puts you on the board here."
              primaryAction={{ label: "Open the map", href: "/" }}
              secondaryAction={{ label: "Create a project", to: "/projects" }}
            />
          }
        >
          <DataList>
            {(activity ?? []).map((entry) => (
              <DataRow
                key={entry.id}
                title={describeActivity(entry)}
                trailing={<StatChip icon="ti ti-clock">{new Date(entry.created_at).toLocaleDateString()}</StatChip>}
              />
            ))}
          </DataList>
        </AsyncBoundary>
      </div>
    </>
  );
}

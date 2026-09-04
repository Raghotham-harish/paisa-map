import { useEffect, useState } from "react";
import { api, ActivityEntry } from "../lib/api";
import { EmptyState } from "../components/EmptyState";

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

export default function Activity() {
  const [activity, setActivity] = useState<ActivityEntry[] | null>(null);

  useEffect(() => {
    api.listActivity().then((data) => setActivity(data.activity));
  }, []);

  return (
    <>
      <h1 className="page-title">Activity</h1>
      <p className="page-sub">What's happened on your account, most recent first.</p>

      {activity === null ? (
        <div className="loading">Loading…</div>
      ) : activity.length === 0 ? (
        <EmptyState
          icon="🕘"
          title="Nothing's happened yet"
          description="Activity like saving locations, creating projects, and generating reports will show up here."
          primaryAction={{ label: "Open the map", href: "/" }}
          secondaryAction={{ label: "Create a project", to: "/projects" }}
        />
      ) : (
        <ul className="list">
          {activity.map((entry) => (
            <li key={entry.id}>
              <span className="primary" style={{ fontSize: 13.5 }}>
                {describe(entry)}
              </span>
              <span className="meta">{new Date(entry.created_at).toLocaleString()}</span>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

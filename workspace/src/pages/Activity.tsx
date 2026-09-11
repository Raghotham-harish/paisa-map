import { useEffect, useState } from "react";
import { api, ActivityEntry } from "../lib/api";
import { EmptyState } from "../components/EmptyState";
import { DataList, DataRow } from "../components/DataList";
import { AsyncBoundary } from "../components/AsyncBoundary";

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
  const [loadError, setLoadError] = useState<string | null>(null);

  const load = () => {
    setLoadError(null);
    api.listActivity().then((data) => setActivity(data.activity)).catch(() => setLoadError("Couldn't load your activity — try again."));
  };

  useEffect(load, []);

  return (
    <>
      <h1 className="page-title">Activity</h1>
      <p className="page-sub">What's happened on your account, most recent first.</p>

      <AsyncBoundary
        loading={activity === null && !loadError}
        error={loadError}
        onRetry={load}
        empty={activity?.length === 0}
        emptyState={
          <EmptyState
            icon="🕘"
            title="Nothing's happened yet"
            description="Activity like saving locations, creating projects, and generating reports will show up here."
            primaryAction={{ label: "Open the map", href: "/" }}
            secondaryAction={{ label: "Create a project", to: "/projects" }}
          />
        }
      >
        <DataList>
          {(activity ?? []).map((entry) => (
            <DataRow
              key={entry.id}
              title={describe(entry)}
              trailing={<span className="meta">{new Date(entry.created_at).toLocaleString()}</span>}
            />
          ))}
        </DataList>
      </AsyncBoundary>
    </>
  );
}

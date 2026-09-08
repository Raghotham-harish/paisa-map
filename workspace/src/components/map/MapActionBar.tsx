import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, api, Project } from "../../lib/api";
import { MapSelection } from "../../lib/mapBridge";

/**
 * Always-visible action strip docked over the map. Unlike the (dismissible)
 * LocationPanel, this never goes away — the three primary actions (save /
 * compare / forecast) are reachable at all times, and by default they target
 * the pincode nearest the YOU-ARE-HERE pin (auto-selected by the map on load).
 * Clicking any marker re-targets it.
 */
export function MapActionBar({
  selection,
  project,
  inCompare,
  onToggleCompare,
  onSaved,
}: {
  selection: MapSelection | null;
  project: Project | null;
  inCompare: boolean;
  onToggleCompare: (pincode: string) => void;
  onSaved: () => void;
}) {
  const navigate = useNavigate();
  const pincode = selection?.pincode ?? null;
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "exists" | "error">("idle");

  useEffect(() => setSaveState("idle"), [pincode]);

  const onSave = async () => {
    if (!pincode) return;
    setSaveState("saving");
    try {
      const res = await api.createLocation({
        pincode,
        name: selection?.name,
        lat: selection?.lat,
        lng: selection?.lng,
        project_id: project?.id,
      });
      setSaveState(res.created ? "saved" : "exists");
      onSaved();
    } catch (e) {
      setSaveState(e instanceof ApiError && e.status === 401 ? "error" : "error");
    }
  };

  return (
    <div className="map-action-bar">
      <div className="mab-loc">
        <span className="mab-kicker">{pincode ? "Selected location" : "Locating you…"}</span>
        <span className="mab-name">{selection?.name || pincode || "—"}</span>
        {pincode && selection?.name && selection.name !== pincode && (
          <span className="mono mab-pin">{pincode}</span>
        )}
      </div>
      <div className="mab-actions">
        <button className="btn" onClick={onSave} disabled={!pincode || saveState === "saving"}>
          {saveState === "saving"
            ? "Saving…"
            : saveState === "saved"
              ? "Saved ✓"
              : saveState === "exists"
                ? "Already saved"
                : "Save location"}
        </button>
        <button className="btn secondary" disabled={!pincode} onClick={() => pincode && onToggleCompare(pincode)}>
          {inCompare ? "Remove from compare" : "Add to compare"}
        </button>
        <button
          className="btn secondary"
          disabled={!project}
          title={project ? "Investment → revenue forecast for this project" : "Pick a project to forecast"}
          onClick={() => project && navigate(`/forecast?project_id=${project.id}`)}
        >
          Forecast
        </button>
      </div>
      {saveState === "error" && (
        <span className="mab-err">
          Couldn't save — <a href="/workspace/">sign in</a> and retry.
        </span>
      )}
    </div>
  );
}

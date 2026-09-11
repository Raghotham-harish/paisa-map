import { useEffect, useState } from "react";
import { api, LocationStatus, SavedLocation } from "../lib/api";
import { EmptyState } from "../components/EmptyState";
import { illustrations } from "../lib/illustrations";
import { SearchInput } from "../components/SearchInput";
import { DataList, DataRow } from "../components/DataList";
import { AsyncBoundary } from "../components/AsyncBoundary";
import { UndoToastStack } from "../components/UndoToast";
import { usePendingDelete } from "../lib/undo";
import { MicroBar } from "../components/MicroViz";
import { ramp } from "../components/chartTheme";

const STATUS_OPTIONS: LocationStatus[] = ["shortlist", "reviewing", "approved", "rejected"];

export default function SavedLocations() {
  const [locations, setLocations] = useState<SavedLocation[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [noteDrafts, setNoteDrafts] = useState<Record<number, string>>({});
  const [scores, setScores] = useState<Record<string, number | null>>({});
  const [query, setQuery] = useState("");
  const { pending, remove, undo, isPending } = usePendingDelete();

  const load = () => {
    setLoadError(null);
    api.listLocations().then((data) => {
      setLocations(data.locations);
      const drafts: Record<number, string> = {};
      data.locations.forEach((l) => (drafts[l.id] = l.notes || ""));
      setNoteDrafts(drafts);
      // One small request per distinct pincode — fine at this list's scale (a
      // handful of saved locations, not thousands), and reuses the same public
      // scoring endpoint the map's compare feature calls.
      data.locations.forEach((l) => {
        api.getLocationScore(l.pincode)
          .then((s) => setScores((prev) => ({ ...prev, [l.pincode]: s.economic_score })))
          .catch(() => setScores((prev) => ({ ...prev, [l.pincode]: null })));
      });
    }).catch(() => setLoadError("Couldn't load your saved locations — try again."));
  };

  useEffect(load, []);

  const onStatusChange = async (id: number, status: LocationStatus) => {
    try {
      await api.updateLocation(id, { status });
      load();
    } catch {
      setActionError("Couldn't update that status — try again.");
    }
  };

  const onNotesBlur = async (id: number) => {
    const current = locations?.find((l) => l.id === id);
    if (!current || (current.notes || "") === noteDrafts[id]) return;
    try {
      await api.updateLocation(id, { notes: noteDrafts[id] });
      load();
    } catch {
      setActionError("Couldn't save that note — try again.");
    }
  };

  const onDelete = (loc: SavedLocation) => {
    remove(loc.id, `“${loc.name || loc.pincode}” removed`, async () => {
      try {
        await api.deleteLocation(loc.id);
      } catch {
        setActionError(`Couldn't remove “${loc.name || loc.pincode}” — try again.`);
      }
      load();
    });
  };

  const q = query.trim().toLowerCase();
  const filtered = locations?.filter(
    (l) => !isPending(l.id) && (!q || l.pincode.includes(q) || (l.name || "").toLowerCase().includes(q))
  ) ?? [];

  return (
    <>
      <h1 className="page-title">Saved Locations</h1>
      <p className="page-sub">Locations you've saved from the map — tag them as you evaluate.</p>
      {actionError && <p style={{ color: "var(--flame)", fontSize: 13, marginBottom: 18 }}>{actionError}</p>}

      <AsyncBoundary
        loading={locations === null && !loadError}
        error={loadError}
        onRetry={load}
        empty={locations?.length === 0}
        emptyState={
          <EmptyState
            illustration={illustrations.locationSearch}
            title="No saved locations yet"
            description="Save a pincode from the map to start building your shortlist — you'll be able to tag, note, and score each one here."
            primaryAction={{ label: "Open the map", href: "/" }}
          />
        }
      >
        <>
        <SearchInput value={query} onChange={setQuery} placeholder="Search by name or pincode…" />
        {filtered.length === 0 ? (
          <EmptyState icon="🔍" title="No matches" description={`Nothing matches "${query}".`} />
        ) : (
        <DataList>
          {filtered.map((loc) => (
            <DataRow
              key={loc.id}
              title={loc.name || loc.pincode}
              subtitle={
                <>
                  {loc.pincode}
                  {loc.pincode in scores && scores[loc.pincode] !== null && (
                    <span style={{ marginLeft: 8, display: "inline-flex", alignItems: "center", gap: 6 }}>
                      <MicroBar value={scores[loc.pincode]!} color={ramp("ppi", scores[loc.pincode])} />
                      <span style={{ fontFamily: "var(--mono)", color: "var(--rupee-deep)" }}>
                        {scores[loc.pincode]}/100
                      </span>
                    </span>
                  )}
                </>
              }
              trailing={
                <>
                  <select value={loc.status} onChange={(e) => onStatusChange(loc.id, e.target.value as LocationStatus)}>
                    {STATUS_OPTIONS.map((s) => (
                      <option key={s} value={s}>
                        {s[0].toUpperCase() + s.slice(1)}
                      </option>
                    ))}
                  </select>
                  <button className="btn secondary" onClick={() => onDelete(loc)}>
                    Remove
                  </button>
                </>
              }
              footer={
                <input
                  type="text"
                  className="notes-input"
                  placeholder="Notes — e.g. rent, competition, availability…"
                  value={noteDrafts[loc.id] ?? ""}
                  onChange={(e) => setNoteDrafts({ ...noteDrafts, [loc.id]: e.target.value })}
                  onBlur={() => onNotesBlur(loc.id)}
                />
              }
            />
          ))}
        </DataList>
        )}
        </>
      </AsyncBoundary>
      <UndoToastStack toasts={pending ? [{ key: "location", label: pending.label, onUndo: undo }] : []} />
    </>
  );
}

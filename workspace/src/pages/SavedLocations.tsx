import { useEffect, useState } from "react";
import { api, LocationStatus, SavedLocation } from "../lib/api";
import { EmptyState } from "../components/EmptyState";
import { SearchInput } from "../components/SearchInput";

const STATUS_OPTIONS: LocationStatus[] = ["shortlist", "reviewing", "approved", "rejected"];

export default function SavedLocations() {
  const [locations, setLocations] = useState<SavedLocation[] | null>(null);
  const [noteDrafts, setNoteDrafts] = useState<Record<number, string>>({});
  const [scores, setScores] = useState<Record<string, number | null>>({});
  const [query, setQuery] = useState("");

  const load = () => {
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
    });
  };

  useEffect(load, []);

  const onStatusChange = async (id: number, status: LocationStatus) => {
    await api.updateLocation(id, { status });
    load();
  };

  const onNotesBlur = async (id: number) => {
    const current = locations?.find((l) => l.id === id);
    if (!current || (current.notes || "") === noteDrafts[id]) return;
    await api.updateLocation(id, { notes: noteDrafts[id] });
    load();
  };

  const onDelete = async (id: number) => {
    await api.deleteLocation(id);
    load();
  };

  const q = query.trim().toLowerCase();
  const filtered = locations?.filter(
    (l) => !q || l.pincode.includes(q) || (l.name || "").toLowerCase().includes(q)
  ) ?? [];

  return (
    <>
      <h1 className="page-title">Saved Locations</h1>
      <p className="page-sub">Locations you've saved from the map — tag them as you evaluate.</p>

      {locations === null ? (
        <div className="loading">Loading…</div>
      ) : locations.length === 0 ? (
        <EmptyState
          icon="📍"
          title="No saved locations yet"
          description="Save a pincode from the map to start building your shortlist — you'll be able to tag, note, and score each one here."
          primaryAction={{ label: "Open the map", href: "/" }}
        />
      ) : (
        <>
        <SearchInput value={query} onChange={setQuery} placeholder="Search by name or pincode…" />
        {filtered.length === 0 ? (
          <EmptyState icon="🔍" title="No matches" description={`Nothing matches "${query}".`} />
        ) : (
        <ul className="list">
          {filtered.map((loc) => (
            <li key={loc.id} style={{ flexDirection: "column", alignItems: "stretch", gap: 8 }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
                <div>
                  <div className="primary">{loc.name || loc.pincode}</div>
                  <div className="secondary">
                    {loc.pincode}
                    {loc.pincode in scores && scores[loc.pincode] !== null && (
                      <span style={{ marginLeft: 8, fontFamily: "var(--mono)", color: "var(--rupee-deep)" }}>
                        Economic score {scores[loc.pincode]}/100
                      </span>
                    )}
                  </div>
                </div>
                <div className="row-actions">
                  <select value={loc.status} onChange={(e) => onStatusChange(loc.id, e.target.value as LocationStatus)}>
                    {STATUS_OPTIONS.map((s) => (
                      <option key={s} value={s}>
                        {s[0].toUpperCase() + s.slice(1)}
                      </option>
                    ))}
                  </select>
                  <button className="btn secondary" onClick={() => onDelete(loc.id)}>
                    Remove
                  </button>
                </div>
              </div>
              <input
                type="text"
                className="notes-input"
                placeholder="Notes — e.g. rent, competition, availability…"
                value={noteDrafts[loc.id] ?? ""}
                onChange={(e) => setNoteDrafts({ ...noteDrafts, [loc.id]: e.target.value })}
                onBlur={() => onNotesBlur(loc.id)}
              />
            </li>
          ))}
        </ul>
        )}
        </>
      )}
    </>
  );
}

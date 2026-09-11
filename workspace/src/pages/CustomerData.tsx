import { useEffect, useRef, useState } from "react";
import {
  ApiError, api, CanonicalField, CustomerLocation, CustomerUpload,
  DriverAnalysis, ExpansionRecommendation, LocationTagsResponse,
} from "../lib/api";
import { EmptyState } from "../components/EmptyState";
import { illustrations } from "../lib/illustrations";
import { DataList, DataRow } from "../components/DataList";
import { AsyncBoundary } from "../components/AsyncBoundary";
import { ReturnBanner } from "../components/ReturnTo";
import { UndoToastStack } from "../components/UndoToast";
import { useWorkspace } from "../lib/workspace";
import { usePendingDelete } from "../lib/undo";

const DRIVER_MIN_SAMPLES = 5;

const RISK_CLASS: Record<string, string> = {
  Low: "delta-pos",
  Medium: "reviewing",
  High: "rejected",
  Unknown: "shortlist",
};

const GEOCODE_CLASS: Record<CustomerLocation["geocode_status"], string> = {
  direct: "delta-pos",
  geocoded: "delta-pos",
  pending: "reviewing",
  failed: "rejected",
  unresolvable: "rejected",
};

const FIELD_LABELS: Record<CanonicalField, string> = {
  store_name: "Store name", address: "Address", pincode: "Pincode",
  revenue: "Revenue", rent: "Rent", capex: "CapEx",
};

function money(n: number | null) {
  return n == null ? "—" : `₹${n.toLocaleString("en-IN")}`;
}

export default function CustomerData() {
  const { projects, activeProjectId: projectId, loadError: projectsError, reload: loadProjects } = useWorkspace();
  const { pending: pendingLocation, remove: removeLocation, undo: undoLocation, isPending: isPendingLocation } = usePendingDelete();
  const { pending: pendingUpload, remove: removeUpload, undo: undoUpload, isPending: isPendingUpload } = usePendingDelete();
  const [upload, setUpload] = useState<CustomerUpload | null>(null);
  const [mapping, setMapping] = useState<Partial<Record<CanonicalField, string>>>({});
  const [locations, setLocations] = useState<CustomerLocation[] | null>(null);
  const [pastUploads, setPastUploads] = useState<CustomerUpload[]>([]);
  const [uploading, setUploading] = useState(false);
  const [committing, setCommitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [drivers, setDrivers] = useState<DriverAnalysis | null>(null);
  const [budget, setBudget] = useState("");
  const [recommendation, setRecommendation] = useState<ExpansionRecommendation | null>(null);
  const [recommending, setRecommending] = useState(false);
  const [recommendError, setRecommendError] = useState<string | null>(null);
  const [locationTags, setLocationTags] = useState<LocationTagsResponse | null>(null);
  const [locationTagsLoading, setLocationTagsLoading] = useState(false);
  const [locationTagsMessage, setLocationTagsMessage] = useState<string | null>(null);
  const [locationsError, setLocationsError] = useState<string | null>(null);
  const [driversError, setDriversError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const pollRef = useRef<number | null>(null);

  const loadForProject = (pid: number) => {
    setLocationsError(null);
    setDriversError(null);
    api.listCustomerLocations(pid).then((data) => setLocations(data.locations)).catch(() => setLocationsError("Couldn't load your store data — try again."));
    api.listCustomerUploads(pid).then((data) => setPastUploads(data.uploads)).catch(() => {});
    api.getDriverAnalysis(pid).then(setDrivers).catch(() => setDriversError("Couldn't load driver analysis — try again."));
  };

  useEffect(() => {
    if (projectId != null) {
      loadForProject(projectId);
      setUpload(null);
      setRecommendation(null);
      setBudget("");
      setLocationTags(null);
      setLocationTagsMessage(null);
    }
  }, [projectId]);

  const onLoadLocationTags = async () => {
    if (projectId == null) return;
    setLocationTagsLoading(true);
    setLocationTagsMessage(null);
    try {
      const result = await api.getLocationTags(projectId);
      setLocationTags(result);
    } catch (e) {
      setLocationTags(null);
      if (e instanceof ApiError && e.body?.error === "not_connected") {
        setLocationTagsMessage("Connect Google Analytics for this project first, on the Connections page.");
      } else if (e instanceof ApiError && e.body?.error === "property_not_selected") {
        setLocationTagsMessage("Pick a GA4 property for this connection first, on the Connections page.");
      } else if (e instanceof ApiError && e.body?.detail) {
        setLocationTagsMessage(String(e.body.detail));
      } else {
        setLocationTagsMessage("Couldn't load digital-signal tags.");
      }
    } finally {
      setLocationTagsLoading(false);
    }
  };

  useEffect(() => {
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
  }, []);

  const stopPolling = () => {
    if (pollRef.current) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  const startPolling = (uploadId: number) => {
    stopPolling();
    pollRef.current = window.setInterval(async () => {
      try {
        const { upload: updated } = await api.getCustomerUpload(uploadId);
        setUpload(updated);
        if (updated.status !== "geocoding") {
          stopPolling();
          if (projectId != null) loadForProject(projectId);
        }
      } catch {
        stopPolling();
        setError("Lost track of that import's progress — refresh to check its status.");
      }
    }, 2000);
  };

  const onFileSelected = async (file: File) => {
    if (projectId == null) return;
    setUploading(true);
    setError(null);
    try {
      const { upload: created } = await api.uploadCustomerData(projectId, file);
      setUpload(created);
      setMapping({});
    } catch (e) {
      setError(e instanceof ApiError && e.body?.detail ? String(e.body.detail) : "Upload failed — try again.");
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const onCommit = async () => {
    if (!upload) return;
    setCommitting(true);
    setError(null);
    try {
      const { upload: committed } = await api.commitCustomerUpload(upload.id, mapping);
      setUpload(committed);
      if (committed.status === "geocoding") {
        startPolling(committed.id);
      } else if (projectId != null) {
        loadForProject(projectId);
      }
    } catch (e) {
      setError(e instanceof ApiError && e.body?.detail ? String(e.body.detail) : "Couldn't import that file.");
    } finally {
      setCommitting(false);
    }
  };

  const onDeleteUpload = (u: CustomerUpload) => {
    removeUpload(u.id, `“${u.filename}” deleted`, async () => {
      try {
        await api.deleteCustomerUpload(u.id);
        if (upload?.id === u.id) setUpload(null);
      } catch {
        setError(`Couldn't delete “${u.filename}” — try again.`);
      }
      if (projectId != null) loadForProject(projectId);
    });
  };

  const onDeleteLocation = (loc: CustomerLocation) => {
    const label = loc.store_name || loc.pincode || loc.raw_address || `Location #${loc.id}`;
    removeLocation(loc.id, `“${label}” deleted`, async () => {
      try {
        await api.deleteCustomerLocation(loc.id);
        setLocations((prev) => prev && prev.filter((l) => l.id !== loc.id));
      } catch {
        setError(`Couldn't delete “${label}” — try again.`);
      }
    });
  };

  const onRecommend = async () => {
    const budgetNum = Number(budget);
    if (projectId == null || !budgetNum || budgetNum <= 0) return;
    setRecommending(true);
    setRecommendError(null);
    try {
      const result = await api.getExpansionRecommendation(projectId, budgetNum);
      setRecommendation(result);
    } catch (e) {
      setRecommendError(
        e instanceof ApiError && e.body?.detail ? String(e.body.detail) : "Couldn't build a recommendation."
      );
    } finally {
      setRecommending(false);
    }
  };

  const canCommit = Boolean(mapping.address || mapping.pincode);

  return (
    <>
      <ReturnBanner />
      <h1 className="page-title">Store Data</h1>
      <p className="page-sub">
        Upload your own store performance (revenue, rent, CapEx) and see it joined against PaisaMap's signals.
      </p>

      <AsyncBoundary
        loading={projects === null && !projectsError}
        error={projectsError}
        onRetry={loadProjects}
        empty={projects?.length === 0}
        emptyState={
          <EmptyState
            illustration={illustrations.folderFiles}
            title="Store data needs a project first"
            description="Upload your stores' address and revenue data into a project to see it enriched with location intelligence."
            dependency="You don't have a project yet — create one to unlock uploads."
            primaryAction={{ label: "Create a project", to: "/projects" }}
          />
        }
      >
        <>
          <div className="card" style={{ marginBottom: 24 }}>
            {!upload && (
              <div style={{ marginTop: 14 }}>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".csv,.xlsx"
                  disabled={uploading}
                  onChange={(e) => e.target.files?.[0] && onFileSelected(e.target.files[0])}
                />
                {uploading && <p className="loading" style={{ marginTop: 8 }}>Uploading…</p>}
              </div>
            )}

            {error && <p style={{ color: "var(--flame)", fontSize: 13, marginTop: 10 }}>{error}</p>}

            {upload && upload.status === "pending_mapping" && (
              <div style={{ marginTop: 18 }}>
                <p style={{ fontSize: 13, color: "var(--ink-soft)" }}>
                  <b>{upload.filename}</b> — {upload.row_count} rows. Map each column below (Address or Pincode is required).
                </p>
                <div className="field-grid">
                  {(Object.keys(FIELD_LABELS) as CanonicalField[]).map((field) => (
                    <label key={field}>
                      {FIELD_LABELS[field]}
                      <select
                        value={mapping[field] || ""}
                        onChange={(e) => setMapping({ ...mapping, [field]: e.target.value || undefined })}
                      >
                        <option value="">— none —</option>
                        {(upload.headers || []).map((h) => (
                          <option key={h} value={h}>{h}</option>
                        ))}
                      </select>
                    </label>
                  ))}
                </div>

                {upload.sample_rows.length > 0 && (
                  <div style={{ overflowX: "auto", marginTop: 14 }}>
                    <table style={{ borderCollapse: "collapse", fontSize: 12.5, width: "100%" }}>
                      <thead>
                        <tr>
                          {(upload.headers || []).map((h) => (
                            <th key={h} style={{ textAlign: "left", padding: "4px 8px", borderBottom: "1px solid var(--line)", color: "var(--ink-soft)" }}>
                              {h}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {upload.sample_rows.map((row, i) => (
                          <tr key={i}>
                            {(upload.headers || []).map((h) => (
                              <td key={h} style={{ padding: "4px 8px", borderBottom: "1px solid var(--line)" }}>
                                {row[h]}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                <div style={{ display: "flex", gap: 10, marginTop: 14 }}>
                  <button className="btn" disabled={!canCommit || committing} onClick={onCommit}>
                    {committing ? "Importing…" : "Import"}
                  </button>
                  <button className="btn secondary" onClick={() => setUpload(null)}>Cancel</button>
                </div>
              </div>
            )}

            {upload && upload.status === "geocoding" && (
              <p className="loading" style={{ marginTop: 14 }}>
                Geocoding addresses… this can take a few minutes for larger files.
              </p>
            )}

            {upload && upload.status === "ready" && upload.quality_report && (
              <div style={{ marginTop: 14, display: "flex", gap: 8, flexWrap: "wrap" }}>
                <span className="pill delta-pos">{upload.quality_report.total_rows} rows imported</span>
                {upload.unresolved_count > 0 && (
                  <span className="pill rejected" title="No valid pincode, and no address that could be geocoded — these rows can't be joined to the signals dataset, so they're excluded from your forecast.">
                    {upload.unresolved_count} couldn't be matched to a pincode
                  </span>
                )}
                {upload.quality_report.duplicate_count > 0 && (
                  <span className="pill reviewing">{upload.quality_report.duplicate_count} possible duplicates</span>
                )}
                <button className="btn secondary" onClick={() => setUpload(null)}>Upload another</button>
              </div>
            )}

            {upload && upload.status === "failed" && (
              <p style={{ color: "var(--flame)", fontSize: 13, marginTop: 14 }}>
                {upload.error || "Something went wrong processing this file."}
              </p>
            )}
          </div>

          <AsyncBoundary
            loading={locations === null && !locationsError}
            error={locationsError}
            onRetry={() => projectId != null && loadForProject(projectId)}
            empty={locations?.length === 0}
            emptyState={
              <EmptyState illustration={illustrations.folderFiles} title="No store data yet" description="Upload a CSV or Excel file above to get started." bare />
            }
          >
            <DataList>
              {(locations ?? []).filter((loc) => !isPendingLocation(loc.id)).map((loc) => (
                <DataRow
                  key={loc.id}
                  title={loc.store_name || loc.pincode || loc.raw_address || `Location #${loc.id}`}
                  subtitle={
                    <>
                      {[loc.pincode, loc.raw_address].filter(Boolean).join(" · ") || "No location resolved"}
                      {" · "}Revenue {money(loc.revenue)} · Rent {money(loc.rent)} · CapEx {money(loc.capex)}
                      {loc.extra_fields && Object.keys(loc.extra_fields).length > 0 && (
                        <details style={{ marginTop: 4 }}>
                          <summary style={{ fontSize: 11.5, color: "var(--ink-soft)", cursor: "pointer" }}>
                            {Object.keys(loc.extra_fields).length} extra column{Object.keys(loc.extra_fields).length > 1 ? "s" : ""} from your file
                          </summary>
                          <div style={{ marginTop: 4 }}>
                            {Object.entries(loc.extra_fields).map(([k, v]) => `${k}: ${v}`).join(" · ")}
                          </div>
                        </details>
                      )}
                    </>
                  }
                  trailing={
                    <>
                      <span className={`pill ${GEOCODE_CLASS[loc.geocode_status]}`}>{loc.geocode_status}</span>
                      {loc.intelligence && (
                        <span className={`pill ${RISK_CLASS[loc.intelligence.risk.level]}`}>
                          score {loc.intelligence.economic_score ?? "—"}/100
                        </span>
                      )}
                      <button className="btn secondary" onClick={() => onDeleteLocation(loc)}>Delete</button>
                    </>
                  }
                />
              ))}
            </DataList>
          </AsyncBoundary>

          {locations !== null && locations.length > 0 && (
            <div className="card" style={{ marginTop: 24 }}>
              <p style={{ margin: "0 0 14px", fontSize: 12.5, color: "var(--ink-soft)", fontFamily: "var(--mono)", letterSpacing: ".06em", textTransform: "uppercase" }}>
                What drives your stores?
              </p>
              <AsyncBoundary
                loading={drivers === null && !driversError}
                error={driversError}
                onRetry={() => projectId != null && loadForProject(projectId)}
                empty={drivers != null && !drivers.sufficient_data}
                emptyState={
                  <EmptyState
                    icon="📈"
                    title="Not enough data yet"
                    description={`Driver analysis needs at least ${DRIVER_MIN_SAMPLES} stores with both a resolved location and revenue — you have ${drivers?.sample_size ?? 0} so far.`}
                    bare
                  />
                }
              >
                <>
                  <DataList>
                    {(drivers?.drivers ?? []).map((d) => (
                      <DataRow
                        key={d.signal}
                        title={d.label}
                        subtitle={`based on ${d.sample_size} of your stores`}
                        trailing={
                          <span className={`pill ${d.direction === "positive" ? "delta-pos" : "delta-neg"}`}>
                            {d.direction === "positive" ? "+" : "−"}{Math.abs(d.correlation).toFixed(2)}
                          </span>
                        }
                      />
                    ))}
                  </DataList>
                  {drivers?.note && <p style={{ fontSize: 12, color: "var(--ink-soft)", marginTop: 10 }}>{drivers.note}</p>}
                </>
              </AsyncBoundary>
            </div>
          )}

          {locations !== null && locations.length > 0 && (
            <div className="card" style={{ marginTop: 24 }}>
              <p style={{ margin: "0 0 14px", fontSize: 12.5, color: "var(--ink-soft)", fontFamily: "var(--mono)", letterSpacing: ".06em", textTransform: "uppercase" }}>
                Plan your next stores
              </p>
              <div style={{ display: "flex", gap: 10, alignItems: "flex-end", flexWrap: "wrap" }}>
                <label style={{ flex: "0 0 220px" }}>
                  Budget (₹)
                  <input
                    type="number"
                    min="1"
                    placeholder="e.g. 2000000"
                    value={budget}
                    onChange={(e) => setBudget(e.target.value)}
                  />
                </label>
                <button className="btn" disabled={!budget || recommending} onClick={onRecommend}>
                  {recommending ? "Building…" : "Recommend"}
                </button>
              </div>
              {recommendError && <p style={{ color: "var(--flame)", fontSize: 13, marginTop: 10 }}>{recommendError}</p>}

              {recommendation && (
                <div style={{ marginTop: 18 }}>
                  {recommendation.capex_estimation === "unavailable" && recommendation.detail && (
                    <p style={{ fontSize: 12.5, color: "var(--ink-soft)", marginBottom: 12 }}>{recommendation.detail}</p>
                  )}
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
                    <span className="pill shortlist">{recommendation.portfolio.length} sites recommended</span>
                    {recommendation.total_estimated_capex != null && (
                      <span className="pill delta-pos">{money(recommendation.total_estimated_capex)} of {money(recommendation.budget)}</span>
                    )}
                    {recommendation.driver_weighted && <span className="pill reviewing">weighted by your own drivers</span>}
                  </div>
                  {recommendation.portfolio.length === 0 ? (
                    <EmptyState icon="🔍" title="No sites fit" description="Nothing met the quality bar within this budget — try a larger budget." bare />
                  ) : (
                    <DataList>
                      {recommendation.portfolio.map((c) => (
                        <DataRow
                          key={c.pincode}
                          title={`${c.name} · ${c.pincode}`}
                          subtitle={
                            <>
                              opportunity {c.opportunity_score ?? "—"}/100
                              {c.estimated_capex != null && ` · est. CapEx ${money(c.estimated_capex)}`}
                            </>
                          }
                          trailing={<span className={`pill ${RISK_CLASS[c.risk.level]}`}>{c.risk.level} risk</span>}
                        />
                      ))}
                    </DataList>
                  )}
                </div>
              )}
            </div>
          )}

          {locations !== null && locations.length > 0 && (
            <div className="card" style={{ marginTop: 24 }}>
              <p style={{ margin: "0 0 4px", fontSize: 12.5, color: "var(--ink-soft)", fontFamily: "var(--mono)", letterSpacing: ".06em", textTransform: "uppercase" }}>
                Digital signal by store
              </p>
              <p style={{ margin: "0 0 14px", fontSize: 12.5, color: "var(--ink-soft)" }}>
                Best-effort match of each store's city against your connected Google Analytics traffic — approximate,
                not a guaranteed match (GA4 has no pincode data).
              </p>
              <button className="btn secondary" disabled={locationTagsLoading} onClick={onLoadLocationTags}>
                {locationTagsLoading ? "Loading…" : "Load digital-signal tags"}
              </button>
              {locationTagsMessage && (
                <p style={{ fontSize: 12.5, color: "var(--ink-soft)", marginTop: 10 }}>{locationTagsMessage}</p>
              )}
              {locationTags && (
                <div style={{ marginTop: 14 }}>
                  <p style={{ fontSize: 12.5, color: "var(--ink-soft)", marginBottom: 10 }}>
                    Matched {locationTags.matched_count} of {locationTags.total_count} stores, last {locationTags.window_days} days.
                  </p>
                  <DataList>
                    {locationTags.locations.map((t) => (
                      <DataRow
                        key={t.id}
                        title={t.store_name || t.pincode}
                        subtitle={
                          <>
                            {t.resolved_city || "city unknown"}
                            {t.matched && t.digital_signal && (
                              ` · ${t.digital_signal.sessions} sessions · ${t.digital_signal.conversions} conversions`
                            )}
                          </>
                        }
                        trailing={
                          <span className={`pill ${t.matched ? "delta-pos" : "shortlist"}`}>
                            {t.matched ? "matched" : "no GA4 data"}
                          </span>
                        }
                      />
                    ))}
                  </DataList>
                </div>
              )}
            </div>
          )}

          {pastUploads.length > 0 && (
            <div style={{ marginTop: 24 }}>
              <p style={{ fontSize: 12.5, color: "var(--ink-soft)", fontFamily: "var(--mono)", letterSpacing: ".06em", textTransform: "uppercase" }}>
                Past uploads
              </p>
              <DataList>
                {pastUploads.filter((u) => !isPendingUpload(u.id)).map((u) => (
                  <DataRow
                    key={u.id}
                    title={u.filename}
                    subtitle={`${u.row_count} rows`}
                    trailing={
                      <>
                        <span className={`pill ${u.status === "ready" ? "delta-pos" : u.status === "failed" ? "rejected" : "reviewing"}`}>
                          {u.status}
                        </span>
                        <span className="meta">{new Date(u.created_at).toLocaleDateString()}</span>
                        <button className="btn secondary" onClick={() => onDeleteUpload(u)}>Delete</button>
                      </>
                    }
                  />
                ))}
              </DataList>
            </div>
          )}
        </>
      </AsyncBoundary>
      <UndoToastStack
        toasts={[
          ...(pendingLocation ? [{ key: "location", label: pendingLocation.label, onUndo: undoLocation }] : []),
          ...(pendingUpload ? [{ key: "upload", label: pendingUpload.label, onUndo: undoUpload }] : []),
        ]}
      />
    </>
  );
}

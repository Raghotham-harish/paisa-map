import { useEffect, useRef, useState } from "react";
import {
  ApiError, api, CanonicalField, CustomerLocation, CustomerUpload, Project,
} from "../lib/api";
import { EmptyState } from "../components/EmptyState";

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
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [projectId, setProjectId] = useState<number | null>(null);
  const [upload, setUpload] = useState<CustomerUpload | null>(null);
  const [mapping, setMapping] = useState<Partial<Record<CanonicalField, string>>>({});
  const [locations, setLocations] = useState<CustomerLocation[] | null>(null);
  const [pastUploads, setPastUploads] = useState<CustomerUpload[]>([]);
  const [uploading, setUploading] = useState(false);
  const [committing, setCommitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const pollRef = useRef<number | null>(null);

  useEffect(() => {
    api.listProjects().then((data) => {
      setProjects(data.projects);
      if (data.projects.length > 0) setProjectId(data.projects[0].id);
    });
  }, []);

  const loadForProject = (pid: number) => {
    api.listCustomerLocations(pid).then((data) => setLocations(data.locations));
    api.listCustomerUploads(pid).then((data) => setPastUploads(data.uploads));
  };

  useEffect(() => {
    if (projectId != null) loadForProject(projectId);
  }, [projectId]);

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
      const { upload: updated } = await api.getCustomerUpload(uploadId);
      setUpload(updated);
      if (updated.status !== "geocoding") {
        stopPolling();
        if (projectId != null) loadForProject(projectId);
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

  const onDeleteUpload = async (id: number) => {
    await api.deleteCustomerUpload(id);
    if (upload?.id === id) setUpload(null);
    if (projectId != null) loadForProject(projectId);
  };

  const onDeleteLocation = async (id: number) => {
    await api.deleteCustomerLocation(id);
    setLocations((prev) => prev && prev.filter((l) => l.id !== id));
  };

  const canCommit = Boolean(mapping.address || mapping.pincode);

  return (
    <>
      <h1 className="page-title">Store Data</h1>
      <p className="page-sub">
        Upload your own store performance (revenue, rent, CapEx) and see it joined against PaisaMap's signals.
      </p>

      {projects === null ? (
        <div className="loading">Loading…</div>
      ) : projects.length === 0 ? (
        <EmptyState
          icon="📊"
          title="Store data needs a project first"
          description="Upload your stores' address and revenue data into a project to see it enriched with location intelligence."
          dependency="You don't have a project yet — create one to unlock uploads."
          primaryAction={{ label: "Create a project", to: "/projects" }}
        />
      ) : (
        <>
          <div className="card" style={{ marginBottom: 24 }}>
            <label>
              Project
              <select value={projectId ?? ""} onChange={(e) => { setProjectId(Number(e.target.value)); setUpload(null); }}>
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            </label>

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
                {upload.quality_report.missing_location > 0 && (
                  <span className="pill rejected">{upload.quality_report.missing_location} missing address/pincode</span>
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

          {locations === null ? (
            <div className="loading">Loading…</div>
          ) : locations.length === 0 ? (
            <EmptyState icon="🏬" title="No store data yet" description="Upload a CSV or Excel file above to get started." bare />
          ) : (
            <ul className="list">
              {locations.map((loc) => (
                <li key={loc.id} style={{ flexDirection: "column", alignItems: "stretch", gap: 6 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
                    <div>
                      <div className="primary">{loc.store_name || loc.pincode || loc.raw_address || `Location #${loc.id}`}</div>
                      <div className="secondary">
                        {[loc.pincode, loc.raw_address].filter(Boolean).join(" · ") || "No location resolved"}
                        {" · "}Revenue {money(loc.revenue)} · Rent {money(loc.rent)} · CapEx {money(loc.capex)}
                      </div>
                    </div>
                    <div className="row-actions">
                      <span className={`pill ${GEOCODE_CLASS[loc.geocode_status]}`}>{loc.geocode_status}</span>
                      {loc.intelligence && (
                        <span className={`pill ${RISK_CLASS[loc.intelligence.risk.level]}`}>
                          score {loc.intelligence.economic_score ?? "—"}/100
                        </span>
                      )}
                      <button className="btn secondary" onClick={() => onDeleteLocation(loc.id)}>Delete</button>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}

          {pastUploads.length > 0 && (
            <div style={{ marginTop: 24 }}>
              <p style={{ fontSize: 12.5, color: "var(--ink-soft)", fontFamily: "var(--mono)", letterSpacing: ".06em", textTransform: "uppercase" }}>
                Past uploads
              </p>
              <ul className="list">
                {pastUploads.map((u) => (
                  <li key={u.id}>
                    <div>
                      <div className="primary">{u.filename}</div>
                      <div className="secondary">{u.row_count} rows</div>
                    </div>
                    <div className="row-actions">
                      <span className={`pill ${u.status === "ready" ? "delta-pos" : u.status === "failed" ? "rejected" : "reviewing"}`}>
                        {u.status}
                      </span>
                      <span className="meta">{new Date(u.created_at).toLocaleDateString()}</span>
                      <button className="btn secondary" onClick={() => onDeleteUpload(u.id)}>Delete</button>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </>
  );
}

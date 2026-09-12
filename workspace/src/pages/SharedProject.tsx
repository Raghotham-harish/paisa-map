import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { SharedProject as SharedProjectData, api } from "../lib/api";

/** E2 — the public, read-only landing page a project's share link opens.
 *  Rendered standalone by App.tsx before any auth check (no session needed
 *  at all, unlike InviteAccept) — reads the token from the URL itself
 *  (useLocation, not useParams, since this happens outside any matched
 *  <Route>, same reasoning as InviteAccept). */
export default function SharedProject() {
  const location = useLocation();
  const token = location.pathname.match(/\/projects\/shared\/([^/]+)/)?.[1] ?? "";
  const [project, setProject] = useState<SharedProjectData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setProject(null);
    setError(null);
    api.getSharedProject(token)
      .then((data) => setProject(data.project))
      .catch(() => setError("This link is invalid, has been revoked, or the project was deleted."));
  }, [token]);

  if (error) {
    return (
      <div className="signin-screen">
        <h1>Link not found</h1>
        <p>{error}</p>
      </div>
    );
  }

  if (!project) return <div className="loading">Loading…</div>;

  return (
    <div className="signin-screen" style={{ maxWidth: 640, textAlign: "left" }}>
      <p className="wiz-hint" style={{ marginBottom: 6 }}>Shared project · read-only</p>
      <h1>{project.name}</h1>
      {project.description && <p>{project.description}</p>}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", margin: "12px 0 24px" }}>
        {project.business_type && <span className="pill shortlist">{project.business_type}</span>}
        {project.target_segment && <span className="pill shortlist">{project.target_segment}</span>}
        {project.industry && <span className="pill shortlist">{project.industry}</span>}
      </div>

      <div className="card" style={{ marginBottom: 20 }}>
        <p className="kicker" style={{ marginBottom: 12 }}>Locations ({project.locations.length})</p>
        {project.locations.length === 0 ? (
          <p style={{ color: "var(--ink-soft)", fontSize: 13 }}>No locations saved yet.</p>
        ) : (
          <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.8 }}>
            {project.locations.map((l, i) => (
              <li key={i}>{l.name || l.pincode} {l.name ? <span style={{ color: "var(--ink-soft)" }}>({l.pincode})</span> : null}</li>
            ))}
          </ul>
        )}
      </div>

      <div className="card">
        <p className="kicker" style={{ marginBottom: 12 }}>Reports ({project.reports.length})</p>
        {project.reports.length === 0 ? (
          <p style={{ color: "var(--ink-soft)", fontSize: 13 }}>No finished reports yet.</p>
        ) : (
          <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.8 }}>
            {project.reports.map((r, i) => (
              <li key={i}>{r.title} <span style={{ color: "var(--ink-soft)" }}>— {new Date(r.created_at).toLocaleDateString()}</span></li>
            ))}
          </ul>
        )}
      </div>

      <p style={{ marginTop: 24, fontSize: 12.5, color: "var(--ink-soft)" }}>
        Shared from <a href="https://paisamaps.com">PaisaMaps</a>. Report downloads aren't included here — ask the
        project owner to share an individual report link if you need one.
      </p>
    </div>
  );
}

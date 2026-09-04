import { useEffect, useState } from "react";
import { api, Report } from "../lib/api";

export default function Reports() {
  const [reports, setReports] = useState<Report[] | null>(null);

  useEffect(() => {
    api.listReports().then((data) => setReports(data.reports));
  }, []);

  return (
    <>
      <h1 className="page-title">Reports</h1>
      <p className="page-sub">Generated location intelligence reports for your projects.</p>

      {reports === null ? (
        <div className="loading">Loading…</div>
      ) : reports.length === 0 ? (
        <div className="empty-state">
          Reports show up here once you generate one — that capability is coming in a later phase.
        </div>
      ) : (
        <ul className="list">
          {reports.map((r) => (
            <li key={r.id}>
              <span className="primary">{r.title}</span>
              <span className="meta">{r.status}</span>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

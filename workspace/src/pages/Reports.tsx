import { useEffect, useState } from "react";
import { api, Project, Report } from "../lib/api";
import { EmptyState } from "../components/EmptyState";

export default function Reports() {
  const [reports, setReports] = useState<Report[] | null>(null);
  const [projects, setProjects] = useState<Project[] | null>(null);

  useEffect(() => {
    api.listReports().then((data) => setReports(data.reports));
    api.listProjects().then((data) => setProjects(data.projects));
  }, []);

  return (
    <>
      <h1 className="page-title">Reports</h1>
      <p className="page-sub">Generated location intelligence reports for your projects.</p>

      {reports === null || projects === null ? (
        <div className="loading">Loading…</div>
      ) : reports.length === 0 && projects.length === 0 ? (
        <EmptyState
          icon="📄"
          title="Reports need a project first"
          description="Reports are generated for a project's saved locations."
          dependency="You don't have a project yet — create one to unlock report generation."
          primaryAction={{ label: "Create a project", to: "/projects" }}
        />
      ) : reports.length === 0 ? (
        <EmptyState
          icon="📄"
          title="No reports yet"
          description="Report generation is coming in a later phase — once it ships, you'll be able to generate one for any project below."
          secondaryAction={{ label: "View projects", to: "/projects" }}
        />
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

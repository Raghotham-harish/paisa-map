import { useEffect, useState } from "react";
import { api, Project } from "../lib/api";

export default function Projects() {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    api.listProjects().then((data) => setProjects(data.projects));
  };

  useEffect(load, []);

  const onCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setCreating(true);
    setError(null);
    try {
      await api.createProject(name.trim(), description.trim() || undefined);
      setName("");
      setDescription("");
      load();
    } catch {
      setError("Couldn't create the project — try again.");
    } finally {
      setCreating(false);
    }
  };

  const onDelete = async (id: number) => {
    await api.deleteProject(id);
    load();
  };

  return (
    <>
      <h1 className="page-title">Projects</h1>
      <p className="page-sub">A project is the container for saved locations, comparisons, and reports.</p>

      <form className="new-project-form" onSubmit={onCreate}>
        <input
          type="text"
          placeholder="Project name — e.g. Bangalore Retail Expansion"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <input
          type="text"
          placeholder="Description (optional)"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
        <button className="btn" type="submit" disabled={creating || !name.trim()}>
          {creating ? "Creating…" : "New project"}
        </button>
      </form>
      {error && <p style={{ color: "var(--flame)", fontSize: 13, marginTop: -14, marginBottom: 18 }}>{error}</p>}

      {projects === null ? (
        <div className="loading">Loading projects…</div>
      ) : projects.length === 0 ? (
        <div className="empty-state">No projects yet — create your first one above.</div>
      ) : (
        <ul className="project-list">
          {projects.map((p) => (
            <li key={p.id}>
              <div>
                <div className="name">{p.name}</div>
                {p.description && <div className="desc">{p.description}</div>}
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                <span className="meta">{new Date(p.updated_at).toLocaleDateString()}</span>
                <button className="btn secondary" onClick={() => onDelete(p.id)}>
                  Delete
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

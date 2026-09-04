import { useEffect, useRef, useState } from "react";
import { api, Project, ProjectFields } from "../lib/api";
import { EmptyState } from "../components/EmptyState";
import { SearchInput } from "../components/SearchInput";

const EMPTY_FIELDS: ProjectFields = {
  name: "", description: "", business_type: "", target_segment: "", avg_ticket: "", website_url: "",
};

function BusinessFields({
  fields, onChange,
}: {
  fields: ProjectFields;
  onChange: (fields: ProjectFields) => void;
}) {
  return (
    <div className="field-grid">
      <label>
        Business type
        <input
          type="text"
          placeholder="e.g. Premium Coffee"
          value={fields.business_type || ""}
          onChange={(e) => onChange({ ...fields, business_type: e.target.value })}
        />
      </label>
      <label>
        Target segment
        <input
          type="text"
          placeholder="e.g. Upper-middle / Premium"
          value={fields.target_segment || ""}
          onChange={(e) => onChange({ ...fields, target_segment: e.target.value })}
        />
      </label>
      <label>
        Average ticket (₹)
        <input
          type="text"
          inputMode="decimal"
          placeholder="e.g. 500"
          value={fields.avg_ticket ?? ""}
          onChange={(e) => onChange({ ...fields, avg_ticket: e.target.value })}
        />
      </label>
      <label>
        Website
        <input
          type="text"
          placeholder="e.g. yourbusiness.com"
          value={fields.website_url || ""}
          onChange={(e) => onChange({ ...fields, website_url: e.target.value })}
        />
      </label>
    </div>
  );
}

export default function Projects() {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [newFields, setNewFields] = useState<ProjectFields>(EMPTY_FIELDS);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editFields, setEditFields] = useState<ProjectFields>(EMPTY_FIELDS);
  const [query, setQuery] = useState("");
  const nameInputRef = useRef<HTMLInputElement>(null);

  const load = () => {
    api.listProjects().then((data) => setProjects(data.projects));
  };

  useEffect(load, []);

  const onCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newFields.name?.trim()) return;
    setCreating(true);
    setError(null);
    try {
      await api.createProject({ ...newFields, name: newFields.name.trim() });
      setNewFields(EMPTY_FIELDS);
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

  const startEdit = (p: Project) => {
    setEditingId(p.id);
    setEditFields({
      name: p.name, description: p.description || "",
      business_type: p.business_type || "", target_segment: p.target_segment || "",
      avg_ticket: p.avg_ticket ?? "", website_url: p.website_url || "",
    });
  };

  const onSaveEdit = async (id: number) => {
    await api.updateProject(id, editFields);
    setEditingId(null);
    load();
  };

  return (
    <>
      <h1 className="page-title">Projects</h1>
      <p className="page-sub">A project is the container for saved locations, comparisons, and reports.</p>

      <form className="new-project-form" onSubmit={onCreate}>
        <input
          ref={nameInputRef}
          type="text"
          placeholder="Project name — e.g. Bangalore Retail Expansion"
          value={newFields.name}
          onChange={(e) => setNewFields({ ...newFields, name: e.target.value })}
        />
        <input
          type="text"
          placeholder="Description (optional)"
          value={newFields.description}
          onChange={(e) => setNewFields({ ...newFields, description: e.target.value })}
        />
        <BusinessFields fields={newFields} onChange={setNewFields} />
        <button className="btn" type="submit" disabled={creating || !newFields.name?.trim()}>
          {creating ? "Creating…" : "New project"}
        </button>
      </form>
      {error && <p style={{ color: "var(--flame)", fontSize: 13, marginBottom: 18 }}>{error}</p>}

      {projects === null ? (
        <div className="loading">Loading projects…</div>
      ) : projects.length === 0 ? (
        <EmptyState
          icon="🗂️"
          title="Create your first project"
          description="A project groups saved locations, comparisons, and reports for one expansion effort — e.g. one city or one business line."
          primaryAction={{ label: "Get started", onClick: () => nameInputRef.current?.focus() }}
        />
      ) : (
        <>
        <SearchInput value={query} onChange={setQuery} placeholder="Search projects…" />
        {(() => {
          const q = query.trim().toLowerCase();
          const filtered = projects.filter(
            (p) => !q || p.name.toLowerCase().includes(q) || (p.business_type || "").toLowerCase().includes(q)
          );
          if (filtered.length === 0) {
            return <EmptyState icon="🔍" title="No matches" description={`Nothing matches "${query}".`} />;
          }
          return (
        <ul className="project-list">
          {filtered.map((p) => (
            <li key={p.id}>
              <div className="row-top" onClick={() => (editingId === p.id ? setEditingId(null) : startEdit(p))}>
                <div>
                  <div className="name">{p.name}</div>
                  {p.description && <div className="desc">{p.description}</div>}
                  {(p.business_type || p.target_segment || p.avg_ticket || p.website_url) && (
                    <div className="biz-meta">
                      {p.business_type && <span><b>{p.business_type}</b></span>}
                      {p.target_segment && <span>{p.target_segment}</span>}
                      {p.avg_ticket != null && <span>avg ticket ₹{p.avg_ticket}</span>}
                      {p.website_url && (
                        <a
                          href={p.website_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          onClick={(e) => e.stopPropagation()}
                        >
                          {p.website_url.replace(/^https?:\/\//, "")}
                        </a>
                      )}
                    </div>
                  )}
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                  <span className="meta">{new Date(p.updated_at).toLocaleDateString()}</span>
                  <button
                    className="btn secondary"
                    onClick={(e) => { e.stopPropagation(); onDelete(p.id); }}
                  >
                    Delete
                  </button>
                </div>
              </div>
              {editingId === p.id && (
                <div className="edit-panel" onClick={(e) => e.stopPropagation()}>
                  <input
                    type="text"
                    placeholder="Description"
                    value={editFields.description || ""}
                    onChange={(e) => setEditFields({ ...editFields, description: e.target.value })}
                  />
                  <BusinessFields fields={editFields} onChange={setEditFields} />
                  <div style={{ display: "flex", gap: 10 }}>
                    <button className="btn" onClick={() => onSaveEdit(p.id)}>Save</button>
                    <button className="btn secondary" onClick={() => setEditingId(null)}>Cancel</button>
                  </div>
                </div>
              )}
            </li>
          ))}
        </ul>
          );
        })()}
        </>
      )}
    </>
  );
}

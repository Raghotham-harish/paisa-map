import { ReactNode, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, Project, ProjectFields } from "../lib/api";
import { EmptyState } from "../components/EmptyState";
import { SearchInput } from "../components/SearchInput";
import { DataList, DataRow } from "../components/DataList";
import { AsyncBoundary } from "../components/AsyncBoundary";
import { UndoToastStack } from "../components/UndoToast";
import { usePendingDelete } from "../lib/undo";

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
  const [loadError, setLoadError] = useState<string | null>(null);
  const [newFields, setNewFields] = useState<ProjectFields>(EMPTY_FIELDS);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editFields, setEditFields] = useState<ProjectFields>(EMPTY_FIELDS);
  const [query, setQuery] = useState("");
  const nameInputRef = useRef<HTMLInputElement>(null);
  const { pending, remove, undo, isPending } = usePendingDelete();

  const load = () => {
    setLoadError(null);
    api.listProjects()
      .then((data) => setProjects(data.projects))
      .catch(() => setLoadError("Couldn't load your projects — try again."));
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

  const onDelete = (p: Project) => {
    remove(p.id, `“${p.name}” deleted`, async () => {
      try {
        await api.deleteProject(p.id);
      } catch {
        setError(`Couldn't delete “${p.name}” — try again.`);
      }
      load();
    });
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
      <p className="page-sub">
        A project is the container for saved locations, comparisons, and reports.{" "}
        <Link to="/projects/new">Use the guided setup →</Link>
      </p>

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

      <AsyncBoundary
        loading={projects === null && !loadError}
        error={loadError}
        onRetry={load}
        empty={projects?.length === 0}
        emptyState={
          <EmptyState
            icon="🗂️"
            title="Create your first project"
            description="A project groups saved locations, comparisons, and reports for one expansion effort — e.g. one city or one business line."
            primaryAction={{ label: "Get started", onClick: () => nameInputRef.current?.focus() }}
          />
        }
      >
        <>
        <SearchInput value={query} onChange={setQuery} placeholder="Search projects…" />
        {(() => {
          const q = query.trim().toLowerCase();
          const filtered = (projects ?? []).filter(
            (p) => !isPending(p.id) && (!q || p.name.toLowerCase().includes(q) || (p.business_type || "").toLowerCase().includes(q))
          );
          if (filtered.length === 0) {
            return <EmptyState icon="🔍" title="No matches" description={`Nothing matches "${query}".`} />;
          }
          return (
        <DataList>
          {filtered.map((p) => {
            const chips: ReactNode[] = [];
            if (p.business_type) chips.push(<b key="bt">{p.business_type}</b>);
            if (p.target_segment) chips.push(<span key="ts">{p.target_segment}</span>);
            if (p.avg_ticket != null) chips.push(<span key="at">avg ticket ₹{p.avg_ticket}</span>);
            if (p.website_url) {
              chips.push(
                <a
                  key="url"
                  href={p.website_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={(e) => e.stopPropagation()}
                >
                  {p.website_url.replace(/^https?:\/\//, "")}
                </a>
              );
            }
            return (
              <DataRow
                key={p.id}
                title={p.name}
                subtitle={p.description}
                chips={chips}
                onToggle={() => (editingId === p.id ? setEditingId(null) : startEdit(p))}
                trailing={
                  <>
                    <span className="meta">{new Date(p.updated_at).toLocaleDateString()}</span>
                    <button
                      className="btn secondary"
                      onClick={(e) => { e.stopPropagation(); onDelete(p); }}
                    >
                      Delete
                    </button>
                  </>
                }
                expanded={
                  editingId === p.id ? (
                    <>
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
                    </>
                  ) : undefined
                }
              />
            );
          })}
        </DataList>
          );
        })()}
        </>
      </AsyncBoundary>
      <UndoToastStack toasts={pending ? [{ key: "project", label: pending.label, onUndo: undo }] : []} />
    </>
  );
}

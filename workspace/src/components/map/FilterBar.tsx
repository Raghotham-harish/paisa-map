import { Link } from "react-router-dom";
import { Project, SignalCatalogItem } from "../../lib/api";
import { MultiSelect, Option } from "../MultiSelect";

export function FilterBar({
  projects,
  project,
  onProjectChange,
  catalog,
  signals,
  onSignalsChange,
  primarySignal,
  onPrimaryChange,
}: {
  projects: Project[];
  project: Project | null;
  onProjectChange: (id: number) => void;
  catalog: SignalCatalogItem[];
  signals: string[];
  onSignalsChange: (next: string[]) => void;
  primarySignal: string;
  onPrimaryChange: (key: string) => void;
}) {
  const options: Option[] = catalog.map((c) => ({
    value: c.key,
    label: c.label,
    meta: c.pro ? "PRO" : c.group,
  }));

  return (
    <div className="filter-bar">
      <Link className="brand" to="/">
        <img src="/assets/logo-horizontal.svg" alt="PaisaMap" height="20" />
      </Link>
      <span className="fb-divider" />

      <label className="fb-chip">
        <span className="k">Project</span>
        <select
          value={project?.id ?? ""}
          onChange={(e) => onProjectChange(Number(e.target.value))}
        >
          {projects.length === 0 && <option value="">No projects yet</option>}
          {projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </label>

      <Link className="fb-chip fb-chip-link" to={project ? `/projects/new?edit=${project.id}` : "/projects/new"}>
        <span className="k">Industry</span>
        <b>{project?.industry || project?.business_type || "Set up →"}</b>
      </Link>

      <Link className="fb-chip fb-chip-link" to={project ? `/projects/new?edit=${project.id}` : "/projects/new"}>
        <span className="k">Segment</span>
        <b>{project?.target_segment || "Any"}</b>
      </Link>

      <div className="fb-signals">
        <span className="k">Signals</span>
        <MultiSelect
          options={options}
          value={signals}
          onChange={onSignalsChange}
          placeholder="Add a signal layer…"
        />
      </div>

      {signals.length > 1 && (
        <label className="fb-chip">
          <span className="k">Colour by</span>
          <select value={primarySignal} onChange={(e) => onPrimaryChange(e.target.value)}>
            {signals.map((s) => {
              const c = catalog.find((x) => x.key === s);
              return (
                <option key={s} value={s}>
                  {c?.label ?? s}
                </option>
              );
            })}
          </select>
        </label>
      )}
    </div>
  );
}

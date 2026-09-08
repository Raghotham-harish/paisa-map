import { Link } from "react-router-dom";
import { Project, SignalCatalogItem } from "../../lib/api";
import { SUITABILITY_KEY } from "../../lib/mapBridge";
import { MultiSelect, Option } from "../MultiSelect";
// No brand logo here — the sidenav already carries it; a second wordmark in the
// filter bar was pure redundancy. Signals gets its own row so the primary
// context chips (project / industry / segment / colour-by) never get crowded
// out by a long signal-chip list.

export function FilterBar({
  projects,
  project,
  onProjectChange,
  catalog,
  signals,
  onSignalsChange,
  primarySignal,
  onPrimaryChange,
  suitabilityAvailable,
}: {
  projects: Project[];
  project: Project | null;
  onProjectChange: (id: number) => void;
  catalog: SignalCatalogItem[];
  signals: string[];
  onSignalsChange: (next: string[]) => void;
  primarySignal: string;
  onPrimaryChange: (key: string) => void;
  suitabilityAvailable: boolean;
}) {
  const options: Option[] = catalog.map((c) => ({
    value: c.key,
    label: c.label,
    meta: c.pro ? "PRO" : c.group,
  }));

  return (
    <div className="filter-bar">
      <div className="fb-row">
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

        {(suitabilityAvailable || signals.length > 1) && (
          <label className="fb-chip">
            <span className="k">Colour by</span>
            <select value={primarySignal} onChange={(e) => onPrimaryChange(e.target.value)}>
              {suitabilityAvailable && <option value={SUITABILITY_KEY}>Suitability (project fit)</option>}
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

      <div className="fb-row fb-row-signals">
        <div className="fb-signals">
          <span className="k">Signals</span>
          <MultiSelect
            options={options}
            value={signals}
            onChange={onSignalsChange}
            placeholder="Add a signal layer…"
          />
        </div>
      </div>
    </div>
  );
}

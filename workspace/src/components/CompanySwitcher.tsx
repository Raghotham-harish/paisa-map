import { Link } from "react-router-dom";
import { useWorkspace } from "../lib/workspace";

/** Company switcher for the top bar, next to ProjectSwitcher — same shared
 *  `.topbar-switcher` styling (C4). Most accounts have exactly one company
 *  (every signup gets a default one, see auth.py / C3's backfill), so this
 *  reads like a single-item label most of the time — the select still works
 *  once a second company exists, and "Manage" is always the way into
 *  Company Settings regardless of how many there are. */
export function CompanySwitcher() {
  const { organizations, activeOrgId, setActiveOrgId, orgLoadError } = useWorkspace();

  if (orgLoadError) return null; // Company Settings' own AsyncBoundary surfaces this with a retry
  if (organizations === null) return <span className="topbar-switcher-loading skeleton-line" aria-hidden="true" />;
  if (organizations.length === 0) return null; // shouldn't happen post-signup, but don't crash the topbar if it does

  return (
    <div className="topbar-switcher">
      <span className="topbar-switcher-label">Company</span>
      <select value={activeOrgId ?? ""} onChange={(e) => setActiveOrgId(Number(e.target.value))}>
        {organizations.map((o) => (
          <option key={o.id} value={o.id}>{o.name}</option>
        ))}
      </select>
      <Link to="/company" style={{ fontSize: 12.5 }}>Manage →</Link>
    </div>
  );
}

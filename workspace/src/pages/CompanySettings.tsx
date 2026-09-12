import { useEffect, useState } from "react";
import { ApiError, AuditLogEntry, OrgMember, OrgRole, api } from "../lib/api";
import { useWorkspace } from "../lib/workspace";
import { AsyncBoundary } from "../components/AsyncBoundary";
import { EmptyState } from "../components/EmptyState";
import { DataList, DataRow } from "../components/DataList";
import { StatChip } from "../components/StatChip";
import { describeActivity } from "../lib/activity";

// Metadata detail beyond describeActivity's own pincode suffix — the two
// D4 action types whose metadata is actually worth surfacing here.
function auditDetail(entry: AuditLogEntry): string | null {
  const meta = entry.metadata as Record<string, unknown> | null;
  if (!meta) return null;
  if (entry.action === "data_export") {
    return [meta.dataset, meta.format, meta.row_count != null ? `${meta.row_count} rows` : null]
      .filter(Boolean).join(" · ");
  }
  if (entry.action === "report_share_view") return "via public share link";
  return null;
}

const ROLE_OPTIONS: OrgRole[] = ["owner", "admin", "member"];

const ADD_MEMBER_ERRORS: Record<string, string> = {
  user_not_found: "That email doesn't have a PaisaMap account yet — ask them to sign in once first.",
  already_a_member: "They're already a member of this company.",
  forbidden: "Only an owner or admin can add members.",
};

const MEMBER_ACTION_ERRORS: Record<string, string> = {
  forbidden: "You don't have permission to do that.",
  not_a_member: "That person isn't a member anymore.",
  cannot_remove_last_owner: "Can't remove the only owner — promote someone else first.",
  cannot_demote_last_owner: "Can't demote the only owner — promote someone else first.",
  invalid_role: "That's not a valid role.",
};

/** Company Settings (C4) — rename, website, member management, plus a way
 *  to create additional companies. Most accounts have exactly one (see
 *  CompanySwitcher), so this mostly reads as "manage my one company" —
 *  the multi-company shape (switcher + this page) is there and real for
 *  whenever a second one exists, not a placeholder. */
export default function CompanySettings() {
  const { organizations, orgLoadError, reloadOrgs, activeOrgId, activeOrg, setActiveOrgId } = useWorkspace();
  const [members, setMembers] = useState<OrgMember[] | null>(null);
  const [membersError, setMembersError] = useState<string | null>(null);
  const [auditLog, setAuditLog] = useState<AuditLogEntry[] | null>(null);
  const [auditLogError, setAuditLogError] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editWebsite, setEditWebsite] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newEmail, setNewEmail] = useState("");
  const [newRole, setNewRole] = useState<OrgRole>("member");
  const [adding, setAdding] = useState(false);
  const [newCompanyName, setNewCompanyName] = useState("");
  const [creating, setCreating] = useState(false);

  const loadMembers = () => {
    if (activeOrgId == null) return;
    setMembersError(null);
    api.listOrgMembers(activeOrgId)
      .then((data) => setMembers(data.members))
      .catch(() => setMembersError("Couldn't load members — try again."));
  };

  const loadAuditLog = () => {
    if (activeOrgId == null) return;
    setAuditLogError(null);
    api.getOrgAuditLog(activeOrgId)
      .then((data) => setAuditLog(data.entries))
      .catch(() => setAuditLogError("Couldn't load the audit log — try again."));
  };

  useEffect(() => {
    if (activeOrg) {
      setEditName(activeOrg.name);
      setEditWebsite(activeOrg.website_url || "");
    }
    setMembers(null);
    loadMembers();
    setAuditLog(null);
    // 403s silently for a plain member (the endpoint is owner/admin-only) —
    // only fetched here when the role check below already knows it'll pass.
    if (activeOrg?.role === "owner" || activeOrg?.role === "admin") loadAuditLog();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeOrgId, activeOrg?.role]);

  const isOwnerOrAdmin = activeOrg?.role === "owner" || activeOrg?.role === "admin";
  const isOwner = activeOrg?.role === "owner";

  const onSave = async () => {
    if (activeOrgId == null) return;
    setSaving(true);
    setError(null);
    try {
      await api.updateOrganization(activeOrgId, { name: editName.trim(), website_url: editWebsite.trim() || undefined });
      reloadOrgs();
    } catch {
      setError("Couldn't save changes — try again.");
    } finally {
      setSaving(false);
    }
  };

  const onAddMember = async () => {
    if (activeOrgId == null || !newEmail.trim()) return;
    setAdding(true);
    setError(null);
    try {
      const result = await api.addOrgMember(activeOrgId, newEmail.trim(), newRole);
      setMembers(result.members);
      setNewEmail("");
    } catch (e) {
      const code = e instanceof ApiError ? e.body?.error : null;
      setError((code && ADD_MEMBER_ERRORS[code]) || "Couldn't add that member — try again.");
    } finally {
      setAdding(false);
    }
  };

  const onChangeRole = async (memberUserId: number, role: OrgRole) => {
    if (activeOrgId == null) return;
    setError(null);
    try {
      const result = await api.updateOrgMemberRole(activeOrgId, memberUserId, role);
      setMembers(result.members);
    } catch (e) {
      const code = e instanceof ApiError ? e.body?.error : null;
      setError((code && MEMBER_ACTION_ERRORS[code]) || "Couldn't change that role — try again.");
    }
  };

  const onRemoveMember = async (member: OrgMember) => {
    if (activeOrgId == null) return;
    if (!window.confirm(`Remove ${member.name || member.email} from this company?`)) return;
    setError(null);
    try {
      await api.removeOrgMember(activeOrgId, member.user_id);
      loadMembers();
    } catch (e) {
      const code = e instanceof ApiError ? e.body?.error : null;
      setError((code && MEMBER_ACTION_ERRORS[code]) || "Couldn't remove that member — try again.");
    }
  };

  const onCreateCompany = async () => {
    if (!newCompanyName.trim()) return;
    setCreating(true);
    setError(null);
    try {
      const { organization } = await api.createOrganization(newCompanyName.trim());
      setNewCompanyName("");
      reloadOrgs();
      setActiveOrgId(organization.id);
    } catch {
      setError("Couldn't create that company — try again.");
    } finally {
      setCreating(false);
    }
  };

  const onDeleteCompany = async () => {
    if (activeOrgId == null || !activeOrg) return;
    if ((organizations?.length ?? 0) <= 1) {
      setError("This is your only company — create another one first if you want to delete this one.");
      return;
    }
    if (!window.confirm(`Delete "${activeOrg.name}"? This can't be undone.`)) return;
    try {
      await api.deleteOrganization(activeOrgId);
      setActiveOrgId(null);
      reloadOrgs();
    } catch (e) {
      const code = e instanceof ApiError ? e.body?.error : null;
      setError((code && MEMBER_ACTION_ERRORS[code]) || "Couldn't delete this company — try again.");
    }
  };

  return (
    <>
      <h1 className="page-title">Company Settings</h1>
      <p className="page-sub">Your company's name, website, and who has access to it.</p>

      <AsyncBoundary
        loading={organizations === null && !orgLoadError}
        error={orgLoadError}
        onRetry={reloadOrgs}
        empty={organizations?.length === 0}
        emptyState={<EmptyState icon="🏢" title="No company yet" description="This shouldn't happen — try signing out and back in." />}
      >
        {error && <p style={{ color: "var(--flame)", fontSize: 13, marginBottom: 18 }}>{error}</p>}

        <div className="card" style={{ marginBottom: 20 }}>
          <p className="kicker" style={{ marginBottom: 14 }}>Details</p>
          <div className="field-grid">
            <label>
              Name
              <input
                type="text" value={editName} disabled={!isOwnerOrAdmin}
                onChange={(e) => setEditName(e.target.value)}
              />
            </label>
            <label>
              Website
              <input
                type="text" placeholder="e.g. yourbusiness.com" value={editWebsite} disabled={!isOwnerOrAdmin}
                onChange={(e) => setEditWebsite(e.target.value)}
              />
            </label>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 14, flexWrap: "wrap" }}>
            <StatChip icon="ti ti-crown">Plan: {activeOrg?.plan}</StatChip>
            {isOwnerOrAdmin && (
              <button className="btn" disabled={saving || !editName.trim()} onClick={onSave}>
                {saving ? "Saving…" : "Save"}
              </button>
            )}
          </div>
        </div>

        <div className="card" style={{ marginBottom: 20 }}>
          <p className="kicker" style={{ marginBottom: 14 }}>Members</p>
          <AsyncBoundary
            loading={members === null && !membersError}
            error={membersError}
            onRetry={loadMembers}
            empty={members?.length === 0}
            emptyState={<EmptyState icon="👤" title="No members yet" bare />}
          >
            <DataList>
              {(members ?? []).map((m) => (
                <DataRow
                  key={m.id}
                  title={m.name || m.email}
                  subtitle={m.name ? m.email : undefined}
                  trailing={
                    isOwner ? (
                      <>
                        <select value={m.role} onChange={(e) => onChangeRole(m.user_id, e.target.value as OrgRole)}>
                          {ROLE_OPTIONS.map((r) => <option key={r} value={r}>{r[0].toUpperCase() + r.slice(1)}</option>)}
                        </select>
                        <button className="btn secondary" onClick={() => onRemoveMember(m)}>Remove</button>
                      </>
                    ) : (
                      <StatChip icon="ti ti-user-circle">{m.role}</StatChip>
                    )
                  }
                />
              ))}
            </DataList>
          </AsyncBoundary>
          {isOwnerOrAdmin && (
            <div style={{ display: "flex", gap: 10, alignItems: "flex-end", flexWrap: "wrap", marginTop: 16 }}>
              <label style={{ flex: "1 1 220px" }}>
                Add a member by email
                <input
                  type="email" placeholder="teammate@company.com" value={newEmail}
                  onChange={(e) => setNewEmail(e.target.value)}
                />
              </label>
              <label style={{ flex: "0 0 140px" }}>
                Role
                <select value={newRole} onChange={(e) => setNewRole(e.target.value as OrgRole)}>
                  {ROLE_OPTIONS.filter((r) => r !== "owner").map((r) => <option key={r} value={r}>{r[0].toUpperCase() + r.slice(1)}</option>)}
                </select>
              </label>
              <button className="btn" disabled={adding || !newEmail.trim()} onClick={onAddMember}>
                {adding ? "Adding…" : "Add"}
              </button>
            </div>
          )}
        </div>

        {isOwnerOrAdmin && (
          <div className="card" style={{ marginBottom: 20 }}>
            <p className="kicker" style={{ marginBottom: 14 }}>Data-access audit log</p>
            <p style={{ fontSize: 12.5, color: "var(--ink-soft)", marginBottom: 12 }}>
              Who on your team exported data, downloaded or viewed a report, or scored/compared a location — owner
              and admin visible only.
            </p>
            <AsyncBoundary
              loading={auditLog === null && !auditLogError}
              error={auditLogError}
              onRetry={loadAuditLog}
              empty={auditLog?.length === 0}
              emptyState={<EmptyState icon="🔍" title="No data access yet" bare />}
            >
              <DataList>
                {(auditLog ?? []).map((entry) => {
                  const detail = auditDetail(entry);
                  return (
                    <DataRow
                      key={entry.id}
                      title={describeActivity(entry)}
                      subtitle={[entry.name || entry.email || "Unknown", detail].filter(Boolean).join(" — ")}
                      trailing={<StatChip icon="ti ti-clock">{new Date(entry.created_at).toLocaleString()}</StatChip>}
                    />
                  );
                })}
              </DataList>
            </AsyncBoundary>
          </div>
        )}

        <div className="card" style={{ marginBottom: 20 }}>
          <p className="kicker" style={{ marginBottom: 14 }}>Create another company</p>
          <div style={{ display: "flex", gap: 10, alignItems: "flex-end", flexWrap: "wrap" }}>
            <label style={{ flex: "1 1 260px" }}>
              Company name
              <input
                type="text" placeholder="e.g. A second brand or business line" value={newCompanyName}
                onChange={(e) => setNewCompanyName(e.target.value)}
              />
            </label>
            <button className="btn secondary" disabled={creating || !newCompanyName.trim()} onClick={onCreateCompany}>
              {creating ? "Creating…" : "Create company"}
            </button>
          </div>
        </div>

        {isOwner && (organizations?.length ?? 0) > 1 && (
          <div className="card">
            <p className="kicker" style={{ marginBottom: 10 }}>Danger zone</p>
            <p style={{ fontSize: 12.5, color: "var(--ink-soft)", marginBottom: 12 }}>
              Deletes this company and its membership. Projects already in it are not deleted — see the roadmap's
              Phase C for when they'll move back under a company automatically.
            </p>
            <button className="btn secondary" onClick={onDeleteCompany}>Delete this company</button>
          </div>
        )}
      </AsyncBoundary>
    </>
  );
}

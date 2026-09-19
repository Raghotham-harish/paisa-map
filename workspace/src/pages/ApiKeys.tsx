import { useEffect, useState } from "react";
import { ApiKey, CompanyApiKeys, api } from "../lib/api";
import { useWorkspace } from "../lib/workspace";
import { EmptyState } from "../components/EmptyState";
import { DataList, DataRow } from "../components/DataList";
import { AsyncBoundary } from "../components/AsyncBoundary";
import { Pill } from "../components/Pill";

/** Every key attributed to a company — for its owners/admins (and those of the company that pays for it). */
function CompanyKeys({ orgId, orgName }: { orgId: number; orgName: string }) {
  const [data, setData] = useState<CompanyApiKeys | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = () => {
    setError(null);
    api.listCompanyApiKeys(orgId).then(setData).catch(() => { setData(null); setError("Couldn't load this company's keys."); });
  };
  useEffect(load, [orgId]);
  const revoke = async (id: number) => {
    if (!confirm("Revoke this key? Whatever uses it loses access immediately.")) return;
    try {
      await api.revokeCompanyApiKey(orgId, id);
      load();
    } catch {
      setError("Couldn't revoke that key — try again.");
    }
  };
  if (error) return <p style={{ color: "var(--flame)", fontSize: 13 }}>{error}</p>;
  if (!data) return null;
  const live = data.keys.filter((k) => !k.revoked_at && k.owner_is_member);
  const rest = data.keys.filter((k) => k.revoked_at || !k.owner_is_member);
  return (
    <div data-testid="company-keys">
      <div style={{ fontSize: 13, marginBottom: 10 }}>
        <strong>{orgName}</strong> has {live.length} live key{live.length === 1 ? "" : "s"}, used{" "}
        <strong>{data.usage_today}</strong> time{data.usage_today === 1 ? "" : "s"} today. Requests from all of a company's keys
        share one allowance, so creating more keys doesn't raise it.
      </div>
      {live.length === 0 && <div style={{ fontSize: 12.5, color: "var(--ink-soft)" }}>No live keys.</div>}
      <DataList>
        {live.map((k) => (
          <DataRow
            key={k.id}
            title={k.label || "Untitled key"}
            subtitle={
              <>
                <code>{k.key_prefix}…</code>{" · made by "}{k.owner_name || k.owner_email}{" · "}
                {k.last_used_at ? `last used ${new Date(k.last_used_at).toLocaleDateString()}` : "never used"}{" · "}
                {k.usage_today} request{k.usage_today === 1 ? "" : "s"} today
              </>
            }
            trailing={<button className="btn secondary" onClick={() => revoke(k.id)}>Revoke</button>}
          />
        ))}
      </DataList>
      {rest.length > 0 && (
        <div style={{ fontSize: 12, color: "var(--ink-soft)", marginTop: 8 }}>
          {rest.length} revoked or inactive key{rest.length === 1 ? "" : "s"} not shown (a key stops working when its owner leaves the company).
        </div>
      )}
    </div>
  );
}

export default function ApiKeys() {
  const { organizations, activeOrgId, activeOrg } = useWorkspace();
  // Which company a new key belongs to. Defaults to the one selected in the switcher.
  const [keyOrgId, setKeyOrgId] = useState<number | null>(null);
  const [payingFor, setPayingFor] = useState<Array<{ org_id: number; name: string }>>([]);
  const [adminOrgId, setAdminOrgId] = useState<number | null>(null);
  const [keys, setKeys] = useState<ApiKey[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [label, setLabel] = useState("");
  const [creating, setCreating] = useState(false);
  const [revealedKey, setRevealedKey] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setLoadError(null);
    api.listApiKeys().then((d) => setKeys(d.api_keys)).catch(() => setLoadError("Couldn't load your API keys — try again."));
  };

  useEffect(() => {
    load();
  }, []);

  useEffect(() => {
    if (keyOrgId == null && activeOrgId != null) setKeyOrgId(activeOrgId);
  }, [activeOrgId, keyOrgId]);

  // Companies whose keys this person can administer: the selected one (if they own/admin it) and any it pays for.
  const isAdmin = activeOrg?.role === "owner" || activeOrg?.role === "admin";
  useEffect(() => {
    setPayingFor([]);
    if (activeOrgId == null || !isAdmin) return;
    api.getBillingLink(activeOrgId).then((l) => setPayingFor(l.pays_for)).catch(() => setPayingFor([]));
  }, [activeOrgId, isAdmin]);
  const adminChoices = activeOrgId != null && isAdmin
    ? [{ org_id: activeOrgId, name: activeOrg?.name ?? "This company" }, ...payingFor]
    : [];
  const adminId = adminOrgId != null && adminChoices.some((c) => c.org_id === adminOrgId) ? adminOrgId : adminChoices[0]?.org_id ?? null;

  const onCreate = async () => {
    setCreating(true);
    setError(null);
    try {
      const { key } = await api.createApiKey(label.trim() || undefined, keyOrgId ?? undefined);
      setRevealedKey(key);
      setLabel("");
      setCopied(false);
      load();
    } catch {
      setError("Couldn't create a key — try again.");
    } finally {
      setCreating(false);
    }
  };

  const onCopy = async () => {
    if (!revealedKey) return;
    try {
      await navigator.clipboard.writeText(revealedKey);
      setCopied(true);
    } catch {
      // clipboard permission denied — the key is still selectable/visible in the box below
    }
  };

  const onRevoke = async (id: number) => {
    if (!confirm("Revoke this key? Anything using it will immediately lose access.")) return;
    try {
      await api.revokeApiKey(id);
      load();
    } catch {
      setError("Couldn't revoke that key — try again.");
    }
  };

  const active = (keys ?? []).filter((k) => !k.revoked_at);
  const revoked = (keys ?? []).filter((k) => k.revoked_at);

  return (
    <>
      <h1 className="page-title">API Keys</h1>
      <p className="page-sub">
        Call <code>GET /api/export</code> with an <code>X-API-Key</code> header instead of signing in. A key belongs to a
        company: it gets that company's plan's columns and rate limit, its owners and admins can see and revoke it, and it
        stops working if you leave the company.
      </p>

      {revealedKey && (
        <div className="card" style={{ marginBottom: 24, borderColor: "var(--amber)" }}>
          <p style={{ margin: "0 0 10px", fontWeight: 600 }}>
            Copy this key now — you won't be able to see it again.
          </p>
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <code
              style={{
                flex: "1 1 320px", padding: "8px 10px", background: "var(--paper-2)",
                borderRadius: "var(--radius)", fontSize: 12.5, wordBreak: "break-all",
              }}
            >
              {revealedKey}
            </code>
            <button className="btn secondary" onClick={onCopy}>{copied ? "Copied ✓" : "Copy"}</button>
          </div>
          <button className="btn" style={{ marginTop: 14 }} onClick={() => setRevealedKey(null)}>
            Done — I've saved it
          </button>
        </div>
      )}

      <div className="card" style={{ marginBottom: 24 }}>
        <p className="kicker" style={{ marginBottom: 10 }}>Create a new key</p>
        <div style={{ display: "flex", gap: 10, alignItems: "flex-end", flexWrap: "wrap" }}>
          {(organizations?.length ?? 0) > 1 && (
            <label style={{ flex: "0 1 220px" }}>
              Company
              <select value={keyOrgId ?? ""} onChange={(e) => setKeyOrgId(Number(e.target.value))} aria-label="Company for the new key">
                {organizations!.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
              </select>
            </label>
          )}
          <label style={{ flex: "1 1 240px" }}>
            Label (optional)
            <input
              type="text"
              placeholder="e.g. Production integration"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
            />
          </label>
          <button className="btn" disabled={creating} onClick={onCreate}>
            {creating ? "Creating…" : "Create key"}
          </button>
        </div>
        {error && <p style={{ color: "var(--flame)", fontSize: 13, marginTop: 10 }}>{error}</p>}
      </div>

      <AsyncBoundary
        loading={keys === null && !loadError}
        error={loadError}
        onRetry={load}
        empty={active.length === 0}
        emptyState={
          <EmptyState icon="🔑" title="No API keys yet" description="Create one above to start calling /api/export programmatically." bare />
        }
      >
        <DataList>
          {active.map((k) => (
            <DataRow
              key={k.id}
              title={k.label || "Untitled key"}
              subtitle={
                <>
                  <code>{k.key_prefix}…</code>
                  {k.org_name ? <>{" · "}{k.org_name}</> : null}
                  {" · "}
                  {k.last_used_at ? `last used ${new Date(k.last_used_at).toLocaleDateString()}` : "never used"}
                  {" · "}
                  {k.usage_count_today} request{k.usage_count_today === 1 ? "" : "s"} today
                </>
              }
              trailing={<button className="btn secondary" onClick={() => onRevoke(k.id)}>Revoke</button>}
            />
          ))}
        </DataList>
      </AsyncBoundary>

      {adminChoices.length > 0 && (
        <div className="card" style={{ marginTop: 28 }} data-testid="company-keys-card">
          <p className="kicker" style={{ marginBottom: 10 }}>Everyone's keys in a company</p>
          {adminChoices.length > 1 && (
            <label style={{ display: "block", maxWidth: 260, marginBottom: 12 }}>
              Company
              <select value={adminId ?? ""} onChange={(e) => setAdminOrgId(Number(e.target.value))} aria-label="Company whose keys to show">
                {adminChoices.map((c) => <option key={c.org_id} value={c.org_id}>{c.name}</option>)}
              </select>
            </label>
          )}
          {adminId != null && <CompanyKeys key={adminId} orgId={adminId} orgName={adminChoices.find((c) => c.org_id === adminId)?.name ?? ""} />}
        </div>
      )}

      {revoked.length > 0 && (
        <div style={{ marginTop: 24 }}>
          <p className="kicker" style={{ marginBottom: 10 }}>Revoked</p>
          <DataList>
            {revoked.map((k) => (
              <DataRow
                key={k.id}
                title={k.label || "Untitled key"}
                subtitle={<code>{k.key_prefix}…</code>}
                trailing={<Pill tone="rejected">Revoked</Pill>}
              />
            ))}
          </DataList>
        </div>
      )}
    </>
  );
}

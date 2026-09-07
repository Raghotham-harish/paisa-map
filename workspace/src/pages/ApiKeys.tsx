import { useEffect, useState } from "react";
import { ApiKey, api } from "../lib/api";
import { EmptyState } from "../components/EmptyState";

export default function ApiKeys() {
  const [keys, setKeys] = useState<ApiKey[] | null>(null);
  const [label, setLabel] = useState("");
  const [creating, setCreating] = useState(false);
  const [revealedKey, setRevealedKey] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = () => api.listApiKeys().then((d) => setKeys(d.api_keys));

  useEffect(() => {
    load();
  }, []);

  const onCreate = async () => {
    setCreating(true);
    setError(null);
    try {
      const { key } = await api.createApiKey(label.trim() || undefined);
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
    await api.revokeApiKey(id);
    load();
  };

  const active = (keys ?? []).filter((k) => !k.revoked_at);
  const revoked = (keys ?? []).filter((k) => k.revoked_at);

  return (
    <>
      <h1 className="page-title">API Keys</h1>
      <p className="page-sub">
        Call <code>GET /api/export</code> with an <code>X-API-Key</code> header instead of signing in — a key
        automatically gets your plan's columns and rate limit, so upgrading to Pro elevates every key you hold.
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

      {keys === null ? (
        <div className="loading">Loading…</div>
      ) : active.length === 0 ? (
        <EmptyState icon="🔑" title="No API keys yet" description="Create one above to start calling /api/export programmatically." bare />
      ) : (
        <ul className="list">
          {active.map((k) => (
            <li key={k.id}>
              <div>
                <div className="primary">{k.label || "Untitled key"}</div>
                <div className="secondary">
                  <code>{k.key_prefix}…</code>
                  {" · "}
                  {k.last_used_at ? `last used ${new Date(k.last_used_at).toLocaleDateString()}` : "never used"}
                  {" · "}
                  {k.usage_count_today} request{k.usage_count_today === 1 ? "" : "s"} today
                </div>
              </div>
              <div className="row-actions">
                <button className="btn secondary" onClick={() => onRevoke(k.id)}>Revoke</button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {revoked.length > 0 && (
        <div style={{ marginTop: 24 }}>
          <p className="kicker" style={{ marginBottom: 10 }}>Revoked</p>
          <ul className="list">
            {revoked.map((k) => (
              <li key={k.id}>
                <div>
                  <div className="primary">{k.label || "Untitled key"}</div>
                  <div className="secondary"><code>{k.key_prefix}…</code></div>
                </div>
                <span className="pill rejected">Revoked</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </>
  );
}

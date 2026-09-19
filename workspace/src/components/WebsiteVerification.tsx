import { useEffect, useState } from "react";
import { ApiError, WebsiteVerification as Verification, api } from "../lib/api";

const CHECK_ERRORS: Record<string, string> = {
  domain_taken: "Another company on PaisaMap has already verified this website. If it's yours, contact us and we'll sort it out.",
  too_soon: "You just checked — give it a few seconds and try again.",
  website_changed: "The website changed while we were checking — start again.",
  not_started: "Start verification first.",
  invalid_website: "That website isn't something we can verify — use its plain address, like yourbusiness.com.",
  no_website: "Add your website above and save it first.",
};
const errText = (e: unknown) =>
  e instanceof ApiError && CHECK_ERRORS[e.body?.error] ? CHECK_ERRORS[e.body.error] : "Something went wrong — try again.";

/** Proof that this company controls the website it lists. Until it's verified the website is only a claim:
 *  nobody can find or connect to the company by it. */
export function WebsiteVerification({ orgId, website }: { orgId: number; website: string | null }) {
  const [v, setV] = useState<Verification | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: "ok" | "err"; text: string } | null>(null);
  const [copied, setCopied] = useState(false);

  const load = () => {
    api.getWebsiteVerification(orgId).then(setV).catch(() => setV(null));
  };
  // `website` is in the deps so saving a new website re-reads the status (a changed domain drops the old proof).
  useEffect(load, [orgId, website]);

  const run = async (fn: () => Promise<Verification>, okText?: (r: Verification) => string | null) => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await fn();
      setV(r);
      const t = okText?.(r);
      if (t) setMsg({ tone: r.verified === false ? "err" : "ok", text: t });
    } catch (e) {
      setMsg({ tone: "err", text: errText(e) });
    } finally {
      setBusy(false);
    }
  };

  if (!v || (!v.website && !v.can_manage)) return null;

  const copyTag = async () => {
    if (!v.tag) return;
    try {
      await navigator.clipboard.writeText(v.tag);
      setCopied(true);
    } catch {
      /* clipboard blocked — the tag is still selectable in the box */
    }
  };

  return (
    <div className="card" style={{ marginBottom: 20 }} data-testid="website-verification">
      <p className="kicker" style={{ marginBottom: 10 }}>Website verification</p>

      {!v.website && <div style={{ fontSize: 13, color: "var(--ink-soft)" }}>Add a website above and save it to verify it.</div>}
      {v.website && v.invalid_website && (
        <div style={{ fontSize: 13 }} data-testid="wv-invalid">
          “{v.website}” isn't a plain website address we can verify. Use something like <code>yourbusiness.com</code>.
        </div>
      )}

      {v.domain && v.status === "verified" && (
        <div data-testid="wv-verified">
          <div style={{ fontSize: 14 }}>
            <span style={{ color: "var(--rupee-deep)", fontWeight: 700 }}>✓ Verified</span> — <strong>{v.domain}</strong>
            <span style={{ color: "var(--ink-soft)", fontSize: 12.5 }}>
              {" "}({v.method === "search_console" ? "via Google Search Console" : "via the tag on your site"}
              {v.verified_at ? `, ${new Date(v.verified_at).toLocaleDateString()}` : ""})
            </span>
          </div>
          {v.can_manage && (
            <>
              <label style={{ display: "flex", gap: 8, alignItems: "flex-start", marginTop: 12, fontSize: 13, cursor: "pointer" }}>
                <input
                  type="checkbox" checked={v.discoverable} disabled={busy} style={{ width: "auto", marginTop: 3 }}
                  onChange={(e) => run(() => api.setWebsiteDiscoverable(orgId, e.target.checked))}
                  data-testid="wv-discoverable"
                />
                <span>
                  Let a company that types your website ask to connect (for example, an agency that wants to pay for your
                  credits). They only learn that a company with that website exists — never its name, owner or email —
                  and nothing is linked unless you approve it.
                </span>
              </label>
              <div style={{ marginTop: 12 }}>
                <button className="btn secondary" disabled={busy} onClick={() => run(() => api.removeWebsiteVerification(orgId))}>Remove verification</button>
              </div>
            </>
          )}
        </div>
      )}

      {v.domain && v.status !== "verified" && (
        <div data-testid="wv-pending">
          <div style={{ fontSize: 13.5, marginBottom: 6 }}>
            <strong>{v.domain}</strong> — <span style={{ color: "var(--amber, #b7791f)", fontWeight: 700 }}>verification pending</span>.
            Until it's verified, nobody can find or connect to this company by its website.
          </div>
          {!v.can_manage && <div style={{ fontSize: 12.5, color: "var(--ink-soft)" }}>An owner or admin can verify it.</div>}
          {v.can_manage && v.status === "none" && (
            <button className="btn" disabled={busy} onClick={() => run(() => api.startWebsiteVerification(orgId))}>Verify this website</button>
          )}
          {v.can_manage && v.status === "pending" && v.tag && (
            <>
              <div style={{ fontSize: 13, margin: "10px 0 6px" }}>
                Paste this line inside the <code>&lt;head&gt;</code> of your home page, then press Check:
              </div>
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <code style={{ flex: "1 1 360px", padding: "8px 10px", background: "var(--paper-2)", borderRadius: "var(--radius)", fontSize: 12.5, wordBreak: "break-all" }} data-testid="wv-tag">
                  {v.tag}
                </code>
                <button className="btn secondary" onClick={copyTag}>{copied ? "Copied ✓" : "Copy"}</button>
              </div>
              <div style={{ fontSize: 12, color: "var(--ink-soft)", marginTop: 8, lineHeight: 1.5 }}>
                Or, if a Google Search Console account you own for this site is already connected to this company, just press Check.
                Google Analytics access doesn't count (agencies are often given it for a client's site).
              </div>
              <div style={{ marginTop: 12 }}>
                <button
                  className="btn" disabled={busy} data-testid="wv-check"
                  onClick={() => run(() => api.checkWebsiteVerification(orgId), (r) => (r.verified ? "Verified — thank you." : r.reason ?? "Not verified yet."))}
                >
                  {busy ? "Checking…" : "Check now"}
                </button>
              </div>
            </>
          )}
        </div>
      )}
      {msg && <div style={{ fontSize: 12.5, marginTop: 10, color: msg.tone === "err" ? "var(--flame)" : "var(--rupee-deep)" }} data-testid="wv-msg">{msg.text}</div>}
    </div>
  );
}

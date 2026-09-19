import { useEffect, useState } from "react";
import { ApiError, BillingLink, IncomingLinkRequest, OutgoingLinkRequest, api } from "../lib/api";
import { UsageStatement } from "./UsageStatement";

const STATUS_LABEL: Record<OutgoingLinkRequest["status"], string> = {
  pending: "Waiting for an answer",
  approved: "Accepted",
  declined: "Declined",
  cancelled: "Withdrawn",
  expired: "Expired",
};

const LINK_ERRORS: Record<string, string> = {
  company_has_credits: "That company still holds credits of its own. Spend them first, then approve.",
  already_linked: "Someone already pays for that company. Detach it first, then approve.",
  payer_has_payer: "The company asking is itself paid for by another, so it can't take this on.",
  already_a_payer: "That company pays for others, so it can't be paid for as well.",
  request_invalid: "The person who sent this no longer has the right to, so it was cancelled.",
  expired: "This request has expired — ask them to send a new one.",
  forbidden: "Only the owner of a company can link it.",
};
const linkError = (e: unknown) =>
  e instanceof ApiError && LINK_ERRORS[e.body?.error] ? LINK_ERRORS[e.body.error] : "Something went wrong — try again.";

/** Requests from a company that wants to pay for yours — shown to whoever the request was addressed to. */
export function LinkRequestsInbox({ onChanged }: { onChanged?: () => void }) {
  const [requests, setRequests] = useState<IncomingLinkRequest[] | null>(null);
  const [choice, setChoice] = useState<Record<number, number>>({});
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState<Record<number, string>>({});

  const load = () => {
    api.listIncomingLinkRequests().then((d) => setRequests(d.requests)).catch(() => setRequests([]));
  };
  useEffect(load, []);
  if (!requests || requests.length === 0) return null;

  const act = async (id: number, fn: () => Promise<unknown>) => {
    setBusy(id);
    setError((p) => ({ ...p, [id]: "" }));
    try {
      await fn();
      load();
      onChanged?.();
    } catch (e) {
      setError((p) => ({ ...p, [id]: linkError(e) }));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="card" style={{ marginBottom: 20, borderColor: "var(--amber)" }} data-testid="link-inbox">
      <p className="kicker" style={{ marginBottom: 8 }}>Someone wants to pay for your credits</p>
      {requests.map((r) => {
        const usable = r.companies.filter((c) => !c.blocked);
        const picked = choice[r.id] ?? usable[0]?.org_id;
        return (
          <div key={r.id} style={{ padding: "12px 0", borderTop: "1px solid var(--border)" }} data-testid={`link-request-${r.id}`}>
            <div style={{ fontSize: 14 }}>
              <strong>{r.payer_name}</strong> has asked to cover the credits one of your companies uses
              {r.requested_by_name ? <> (sent by {r.requested_by_name})</> : null}.
            </div>
            {r.note && <div style={{ fontSize: 12.5, color: "var(--ink-soft)", marginTop: 4 }}>“{r.note}”</div>}
            <div style={{ fontSize: 12, color: "var(--ink-soft)", marginTop: 8, lineHeight: 1.5 }}>
              If you approve: that company's credit use draws from {r.payer_name}'s credits, {r.payer_name} sees a usage
              statement (who spent how much, never which projects), its staff use {r.payer_name}'s plan features, and its
              staff can spend only inside a budget {r.payer_name} sets. You can detach at any time.
            </div>
            {r.companies.length === 0 ? (
              <div style={{ fontSize: 12.5, marginTop: 8 }}>
                You don't own a company that can be linked (it must be one you own, that nobody else pays for).
              </div>
            ) : (
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", marginTop: 10 }}>
                <select
                  value={picked ?? ""} onChange={(e) => setChoice((p) => ({ ...p, [r.id]: Number(e.target.value) }))}
                  aria-label="Company to link" style={{ width: "auto" }}
                >
                  {r.companies.map((c) => (
                    <option key={c.org_id} value={c.org_id} disabled={c.blocked != null}>
                      {c.name}{c.blocked ? " — holds its own credits" : ""}
                    </option>
                  ))}
                </select>
                <button className="btn" disabled={busy === r.id || picked == null} onClick={() => act(r.id, () => api.approveLinkRequest(r.id, picked!))}>
                  Approve
                </button>
                <button className="btn secondary" disabled={busy === r.id} onClick={() => act(r.id, () => api.declineLinkRequest(r.id))}>
                  Decline
                </button>
              </div>
            )}
            {error[r.id] && <div style={{ color: "var(--flame)", fontSize: 12, marginTop: 6 }}>{error[r.id]}</div>}
          </div>
        );
      })}
    </div>
  );
}

function ConfirmButton({ label, confirmText, onConfirm, disabled }: {
  label: string; confirmText: string; onConfirm: () => Promise<void>; disabled?: boolean;
}) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  if (!asking) return <button className="btn secondary" disabled={disabled} onClick={() => setAsking(true)}>{label}</button>;
  return (
    <span style={{ display: "inline-flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
      <span style={{ fontSize: 12.5 }}>{confirmText}</span>
      <button className="btn" disabled={busy} onClick={async () => { setBusy(true); try { await onConfirm(); } finally { setBusy(false); setAsking(false); } }}>Yes, detach</button>
      <button className="btn secondary" disabled={busy} onClick={() => setAsking(false)}>Keep</button>
    </span>
  );
}

/** Who pays for the selected company, who it pays for, and the requests in between. */
export function WhoPaysPanel({ orgId, refreshKey = 0, onChanged }: { orgId: number; refreshKey?: number; onChanged?: () => void }) {
  const [link, setLink] = useState<BillingLink | null>(null);
  const [outgoing, setOutgoing] = useState<OutgoingLinkRequest[]>([]);
  const [email, setEmail] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: "ok" | "err"; text: string } | null>(null);

  const load = () => {
    api.getBillingLink(orgId).then((l) => {
      setLink(l);
      if (l.can_manage_links) api.listOutgoingLinkRequests(orgId).then((d) => setOutgoing(d.requests)).catch(() => setOutgoing([]));
    }).catch(() => setLink(null));
  };
  useEffect(load, [orgId, refreshKey]);

  if (!link || (!link.paid_by && !link.can_manage_links)) return null;
  const changed = () => { load(); onChanged?.(); };

  const send = async () => {
    setBusy(true);
    setMsg(null);
    try {
      await api.createLinkRequest(orgId, email.trim(), note.trim() || undefined);
      setEmail("");
      setNote("");
      setMsg({ tone: "ok", text: "Request sent. If that person has an account they will see it under Billing — you'll see their answer here." });
      load();
    } catch (e) {
      const code = e instanceof ApiError ? e.body?.error : null;
      setMsg({ tone: "err", text:
        code === "invalid_email" ? "That doesn't look like an email address."
        : code === "too_many_pending" ? "You have too many open requests — withdraw one first."
        : code === "rate_limited" ? "You've sent a lot of requests today — try again tomorrow."
        : "Couldn't send that — try again." });
    } finally {
      setBusy(false);
    }
  };

  // A usage statement is for owners/admins of a company that pays for others, or is paid for by another.
  const showStatement = (link.role === "owner" || link.role === "admin") && (link.paid_by != null || link.pays_for.length > 0);
  return (
    <>
    <div className="card" style={{ marginBottom: 20 }} data-testid="who-pays">
      <p className="kicker" style={{ marginBottom: 8 }}>Who pays</p>
      {link.paid_by && (
        <div style={{ fontSize: 13.5 }} data-testid="paid-by">
          This company's credits are paid for by <strong>{link.paid_by.name}</strong>.{" "}
          {link.can_detach ? (
            <ConfirmButton
              label="Detach" confirmText={`Stop ${link.paid_by.name} paying? This company will use its own credits, starting at 0.`}
              onConfirm={async () => { await api.detachCompany(orgId); changed(); }}
            />
          ) : (
            <span style={{ color: "var(--ink-soft)", fontSize: 12.5 }}>Only the company's owner can detach it.</span>
          )}
        </div>
      )}
      {link.can_manage_links && (
        <>
          <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 6 }}>Companies you pay for</div>
          {link.pays_for.length === 0 && <div style={{ fontSize: 12.5, color: "var(--ink-soft)", marginBottom: 8 }}>None yet.</div>}
          {link.pays_for.map((c) => (
            <div key={c.org_id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, padding: "6px 0", flexWrap: "wrap" }} data-testid={`pays-for-${c.org_id}`}>
              <span style={{ fontSize: 13.5 }}>{c.name}</span>
              <ConfirmButton
                label="Detach" confirmText={`Stop paying for ${c.name}? It will use its own credits, starting at 0.`}
                onConfirm={async () => { await api.detachCompany(c.org_id); changed(); }}
              />
            </div>
          ))}
          <div style={{ borderTop: "1px solid var(--border)", marginTop: 12, paddingTop: 12 }}>
            <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 4 }}>Ask a company to let you pay for it</div>
            <div style={{ fontSize: 12.5, color: "var(--ink-soft)", marginBottom: 8 }}>
              Enter the email of the person who owns it. They choose which of their companies to link and approve it —
              nothing changes until they do, and either side can detach later.
            </div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <input type="email" value={email} placeholder="owner@theirbusiness.com" onChange={(e) => setEmail(e.target.value)} style={{ width: 260 }} aria-label="Their email" />
              <input type="text" value={note} maxLength={200} placeholder="Optional message" onChange={(e) => setNote(e.target.value)} style={{ width: 240 }} aria-label="Message" />
              <button className="btn secondary" disabled={busy || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim())} onClick={send}>Send request</button>
            </div>
            {msg && <div style={{ fontSize: 12.5, marginTop: 8, color: msg.tone === "err" ? "var(--flame)" : "var(--rupee-deep)" }} data-testid="link-msg">{msg.text}</div>}
            {outgoing.length > 0 && (
              <div style={{ marginTop: 12 }} data-testid="outgoing-requests">
                {outgoing.map((r) => (
                  <div key={r.id} style={{ display: "flex", justifyContent: "space-between", gap: 10, fontSize: 12.5, padding: "4px 0", flexWrap: "wrap" }}>
                    <span>{r.target_email}{r.target_org_name ? ` → ${r.target_org_name}` : ""}</span>
                    <span style={{ color: r.status === "pending" ? "inherit" : "var(--ink-soft)" }}>
                      {STATUS_LABEL[r.status]}
                      {r.status === "pending" && (
                        <> · <a href="#" onClick={(e) => { e.preventDefault(); api.cancelLinkRequest(orgId, r.id).then(load); }}>Withdraw</a></>
                      )}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
    {showStatement && <UsageStatement orgId={orgId} />}
    </>
  );
}

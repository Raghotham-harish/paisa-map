import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { useAuth, useGoogleSignIn } from "../lib/auth";
import { api, ApiError, InviteDetail } from "../lib/api";

/** D1 — the landing page an invite email links to. Rendered in two very
 *  different places by App.tsx: standalone (no session, no WorkspaceProvider)
 *  before sign-in, and again as a normal authenticated route once signed in —
 *  so this reads the token from the URL itself (useLocation, not useParams,
 *  since the standalone render happens outside any matched <Route>) and never
 *  touches useWorkspace(), only useAuth() (available everywhere, wraps App
 *  entirely in main.tsx). */
export default function InviteAccept() {
  const location = useLocation();
  const token = location.pathname.match(/\/invite\/([^/]+)/)?.[1] ?? "";
  const { user, refresh } = useAuth();
  const [invite, setInvite] = useState<InviteDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [resolved, setResolved] = useState<"accepted" | "declined" | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setInvite(null);
    setLoadError(null);
    api.getInviteByToken(token)
      .then((data) => setInvite(data.invite))
      .catch(() => setLoadError("This invite link is invalid or has expired."));
  }, [token]);

  useGoogleSignIn("invite-gsi-button", async (credential) => {
    try {
      await api.signInWithGoogle(credential);
      await refresh();
    } catch {
      // AuthProvider stays signed-out; the button remains for a retry.
    }
  });

  const onAccept = async () => {
    setBusy(true);
    setActionError(null);
    try {
      await api.acceptInvite(token);
      setResolved("accepted");
      // Full reload rather than client-side navigation — WorkspaceProvider
      // only loads organizations once, on mount, and this page may have
      // been reached before it ever mounted (a fresh sign-in). A reload
      // guarantees the new company shows up everywhere immediately.
      setTimeout(() => { window.location.href = "/workspace/company"; }, 1200);
    } catch (e) {
      const code = e instanceof ApiError ? e.body?.error : null;
      setActionError(
        code === "email_mismatch"
          ? `This invite was sent to ${invite?.email} — sign out and sign in with that email instead.`
          : "Couldn't accept this invite — it may have expired or already been used."
      );
    } finally {
      setBusy(false);
    }
  };

  const onDecline = async () => {
    setBusy(true);
    setActionError(null);
    try {
      await api.declineInvite(token);
      setResolved("declined");
    } catch {
      setActionError("Couldn't decline this invite — try again.");
    } finally {
      setBusy(false);
    }
  };

  if (loadError) {
    return (
      <div className="signin-screen">
        <h1>Invite not found</h1>
        <p>{loadError}</p>
      </div>
    );
  }

  if (!invite) return <div className="loading">Loading…</div>;

  if (resolved === "accepted") {
    return (
      <div className="signin-screen">
        <h1>You're in!</h1>
        <p>Redirecting to {invite.org_name}…</p>
      </div>
    );
  }

  if (resolved === "declined") {
    return (
      <div className="signin-screen">
        <h1>Invite declined</h1>
        <p>You can close this page.</p>
      </div>
    );
  }

  const emailMismatch = !!user && user.email.toLowerCase() !== invite.email.toLowerCase();

  return (
    <div className="signin-screen">
      <h1>You're invited</h1>
      <p>
        <strong>{invite.inviter_name || invite.inviter_email}</strong> invited you to join{" "}
        <strong>{invite.org_name}</strong> as a <strong>{invite.role}</strong>.
      </p>
      {actionError && <p style={{ color: "var(--flame)", fontSize: 13 }}>{actionError}</p>}
      {!user && (
        <>
          <p>Sign in with <strong>{invite.email}</strong> to accept.</p>
          <div id="invite-gsi-button" />
        </>
      )}
      {user && emailMismatch && (
        <p style={{ color: "var(--flame)", fontSize: 13 }}>
          You're signed in as {user.email}, but this invite was sent to {invite.email}. Sign out and sign
          in with that email to accept.
        </p>
      )}
      {user && !emailMismatch && (
        <div style={{ display: "flex", gap: 10, marginTop: 16 }}>
          <button className="btn" disabled={busy} onClick={onAccept}>Accept</button>
          <button className="btn secondary" disabled={busy} onClick={onDecline}>Decline</button>
        </div>
      )}
    </div>
  );
}

import { useAuth, useGoogleSignIn } from "../lib/auth";
import { api } from "../lib/api";

export default function SignIn() {
  const { refresh } = useAuth();

  useGoogleSignIn("gsi-button", async (credential) => {
    try {
      await api.signInWithGoogle(credential);
      await refresh();
    } catch {
      // AuthProvider stays signed-out; the button remains for a retry.
    }
  });

  return (
    <div className="signin-screen">
      <h1>PaisaMap Workspace</h1>
      <p>Sign in to save locations, build projects, and generate reports.</p>
      <div id="gsi-button" />
    </div>
  );
}

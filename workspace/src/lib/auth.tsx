import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { api, User } from "./api";

declare global {
  interface Window {
    google?: any;
  }
}

interface AuthState {
  user: User | null;
  loading: boolean;
  refresh: () => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = async () => {
    try {
      const data = await api.me();
      setUser(data.user);
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  const signOut = async () => {
    try {
      await api.signOut();
    } finally {
      setUser(null);
    }
  };

  return (
    <AuthContext.Provider value={{ user, loading, refresh, signOut }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}

// Loads the Google Identity Services script once and renders its button into
// `elementId` once the client ID is known (fetched from /api/config — same
// runtime-config pattern as index.html, no separate build-time injection needed
// for this bundle). Calls onCredential(idToken) when a user signs in.
export function useGoogleSignIn(elementId: string, onCredential: (credential: string) => void) {
  useEffect(() => {
    let cancelled = false;

    api.config().then(({ google_client_id }) => {
      if (cancelled || !google_client_id) return;

      const init = () => {
        if (cancelled) return;
        window.google.accounts.id.initialize({
          client_id: google_client_id,
          callback: (resp: { credential: string }) => onCredential(resp.credential),
          use_fedcm_for_prompt: true,
        });
        const el = document.getElementById(elementId);
        if (el) {
          window.google.accounts.id.renderButton(el, {
            theme: "outline",
            size: "large",
            type: "standard",
            shape: "pill",
            width: 240,
          });
        }
      };

      if (window.google?.accounts?.id) {
        init();
        return;
      }
      const script = document.createElement("script");
      script.src = "https://accounts.google.com/gsi/client";
      script.async = true;
      script.defer = true;
      script.onload = init;
      document.head.appendChild(script);
    });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [elementId]);
}

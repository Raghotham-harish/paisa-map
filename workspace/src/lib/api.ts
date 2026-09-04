// Thin fetch wrapper — credentials:"include" on every call is what makes the
// session cookie (set by POST /api/auth/google, same-origin in production)
// actually ride along on API requests. Without it, some browsers omit cookies
// even on same-origin fetches depending on default fetch mode — set explicitly,
// don't rely on the default.

export class ApiError extends Error {
  status: number;
  body: any;
  constructor(status: number, body: any) {
    super(body?.error || `HTTP ${status}`);
    this.status = status;
    this.body = body;
  }
}

async function request(path: string, opts: RequestInit = {}) {
  const res = await fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, body);
  return body;
}

export interface User {
  id: number;
  email: string;
  name: string | null;
  picture_url: string | null;
  plan: "free" | "pro" | "team";
  credits: number;
}

export interface MeResponse {
  user: User;
  plan: string;
  credits: number;
}

export interface Project {
  id: number;
  name: string;
  description: string | null;
  created_at: string;
  updated_at: string;
}

export const api = {
  config: () => request("/api/config"),
  me: () => request("/api/auth/me") as Promise<MeResponse>,
  signInWithGoogle: (credential: string) =>
    request("/api/auth/google", { method: "POST", body: JSON.stringify({ credential }) }) as Promise<MeResponse>,
  signOut: () => request("/api/auth/logout", { method: "POST" }),
  listProjects: () => request("/api/projects") as Promise<{ projects: Project[] }>,
  createProject: (name: string, description?: string) =>
    request("/api/projects", { method: "POST", body: JSON.stringify({ name, description }) }) as Promise<{ project: Project }>,
  deleteProject: (id: number) => request(`/api/projects/${id}`, { method: "DELETE" }),
};

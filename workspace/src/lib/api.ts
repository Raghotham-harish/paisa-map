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
  business_type: string | null;
  target_segment: string | null;
  avg_ticket: number | null;
  created_at: string;
  updated_at: string;
}

export interface ProjectFields {
  name?: string;
  description?: string;
  business_type?: string;
  target_segment?: string;
  avg_ticket?: string | number;
}

export type LocationStatus = "shortlist" | "reviewing" | "approved" | "rejected";

export interface SavedLocation {
  id: number;
  project_id: number;
  pincode: string;
  name: string | null;
  lat: number | null;
  lng: number | null;
  status: LocationStatus;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface ActivityEntry {
  id: number;
  action: string;
  target_type: string | null;
  target_id: number | null;
  metadata: Record<string, unknown> | null;
  created_at: string;
}

export interface CreditLedgerEntry {
  id: number;
  delta: number;
  reason: string;
  balance_after: number;
  created_at: string;
}

export interface Report {
  id: number;
  project_id: number;
  title: string;
  format: string;
  status: string;
  created_at: string;
}

export const api = {
  config: () => request("/api/config"),
  me: () => request("/api/auth/me") as Promise<MeResponse>,
  signInWithGoogle: (credential: string) =>
    request("/api/auth/google", { method: "POST", body: JSON.stringify({ credential }) }) as Promise<MeResponse>,
  signOut: () => request("/api/auth/logout", { method: "POST" }),

  listProjects: () => request("/api/projects") as Promise<{ projects: Project[] }>,
  createProject: (fields: ProjectFields) =>
    request("/api/projects", { method: "POST", body: JSON.stringify(fields) }) as Promise<{ project: Project }>,
  updateProject: (id: number, fields: ProjectFields) =>
    request(`/api/projects/${id}`, { method: "PUT", body: JSON.stringify(fields) }) as Promise<{ project: Project }>,
  deleteProject: (id: number) => request(`/api/projects/${id}`, { method: "DELETE" }),

  listLocations: (projectId?: number) =>
    request(`/api/locations${projectId ? `?project_id=${projectId}` : ""}`) as Promise<{ locations: SavedLocation[] }>,
  updateLocation: (id: number, fields: { status?: LocationStatus; notes?: string }) =>
    request(`/api/locations/${id}`, { method: "PUT", body: JSON.stringify(fields) }) as Promise<{ location: SavedLocation }>,
  deleteLocation: (id: number) => request(`/api/locations/${id}`, { method: "DELETE" }),

  listActivity: (limit = 50) => request(`/api/activity?limit=${limit}`) as Promise<{ activity: ActivityEntry[] }>,

  getCredits: () => request("/api/credits") as Promise<{ balance: number; ledger: CreditLedgerEntry[] }>,

  listReports: () => request("/api/reports") as Promise<{ reports: Report[] }>,
};

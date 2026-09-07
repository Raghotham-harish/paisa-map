// "Pick up where you left off" state for the dashboard hero — last-selected
// pincode and compare-basket size from the map workspace. Both are currently
// fully transient in React/postMessage state (mapBridge.ts's plain useState,
// index.html's own compareList) and don't survive a reload or a trip back to
// the dashboard, so this is a per-viewer convenience only, not data anyone
// else needs to see — localStorage, not a backend column.
const KEY = "pm.mapSession.v1";

export interface MapSessionState {
  projectId: number | null;
  pincode: string | null;
  name: string | null;
  compareCount: number;
  updatedAt: string;
}

export function saveMapSession(s: Omit<MapSessionState, "updatedAt">) {
  try {
    localStorage.setItem(KEY, JSON.stringify({ ...s, updatedAt: new Date().toISOString() }));
  } catch {
    // Private-mode/disabled storage — session-resume is a convenience, never required.
  }
}

export function readMapSession(): MapSessionState | null {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as MapSessionState) : null;
  } catch {
    return null;
  }
}

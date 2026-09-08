// Bridge between the React map-first shell and the embedded public map
// (index.html served at /?embed=1). The map posts {source:"paisamap-map", ...}
// messages up; the shell posts {source:"paisamap-shell", ...} down. In dev the
// map is served by the Flask server on :8080 (the vite dev server only proxies
// /api and /assets), so it's a cross-origin frame — in prod it's same-origin.

import { useCallback, useEffect, useRef, useState } from "react";

// Synthetic "signal" id for the project-fit Suitability layer — not a real
// catalog column; index.html computes/holds its own per-pincode score map.
export const SUITABILITY_KEY = "__suitability__";

const MAP_ORIGIN = import.meta.env.DEV ? "http://localhost:8080" : window.location.origin;
// __MAP_BUILD__ is injected by vite.config.ts (a per-build token) so the
// iframe reloads the public index.html after every deploy instead of serving
// a cached copy with an outdated palette / bridge.
declare const __MAP_BUILD__: string;
export const MAP_SRC = `${import.meta.env.DEV ? "http://localhost:8080" : ""}/?embed=1&v=${__MAP_BUILD__}`;

export type MarketTier = "core" | "edge" | "expansion" | null;

/** The 4 native map styles worth exposing in the workspace shell — a subset
 *  of index.html's full representation picker (which also has bivariate/topn/
 *  icons; those stay iframe-only, reachable by clicking inside the map
 *  itself, not promoted to a shell-level control). */
export type MapStyle = "symbol" | "choropleth" | "heatmap" | "cluster";

export interface MapSelection {
  pincode: string | null;
  name: string | null;
  lat: number;
  lng: number;
  tier: MarketTier;
  /** true when the map auto-picked this (YOU-ARE-HERE nearest), not a click. */
  auto?: boolean;
}

export interface MapKpis {
  pincodesInView: number;
  metricLabel: string;
  metricAvg: number | null;
  medianIncome: number | null;
  topTierZones: number;
  coreCount: number;
  edgeCount: number;
  expansionCount: number;
  /** Sum of the household-count estimate over pincodes in view — null until
   *  build_household_estimates.py's companion file has loaded (or if it
   *  never was, e.g. offline dev without having run that script). */
  householdsInView: number | null;
}

export interface MyStorePoint {
  lat: number;
  lng: number;
  name?: string | null;
}

type Outbound =
  | { type: "setSignal"; col: string }
  | { type: "flyTo"; pincode: string }
  | { type: "toggleCompare"; pincode: string }
  | { type: "save"; pincode: string }
  | { type: "refreshAuth" }
  | { type: "requestViewport" }
  | { type: "setRepresentation"; repr: MapStyle }
  | { type: "setRings"; on: boolean; radiusKm?: number }
  | { type: "setMyStores"; stores: MyStorePoint[] }
  // Drives the "Suitability (project fit)" signal layer. Two shapes (note: the
  // discriminant is `mode`, not `source` — `source` is the bridge envelope key
  // and spreading a second `source` here would silently break delivery):
  //  - mode "preview": the map computes a fast client-side composite itself
  //    from `signals` (+ optional `avgTicket`) — used on project select and
  //    live while the user toggles signals, no round-trip.
  //  - mode "scored": authoritative per-pincode scores from
  //    /api/expansion/surface, replacing the preview once they arrive.
  | {
      type: "setSuitability";
      mode: "preview";
      label: string;
      signals: string[];
      avgTicket?: number | null;
    }
  | {
      type: "setSuitability";
      mode: "scored";
      scores: Record<string, number>;
    };

export function useMapBridge(iframeRef: React.RefObject<HTMLIFrameElement>) {
  const [ready, setReady] = useState(false);
  const [selected, setSelected] = useState<MapSelection | null>(null);
  const [compare, setCompare] = useState<string[]>([]);
  const [saved, setSaved] = useState<string[]>([]);
  const [kpis, setKpis] = useState<MapKpis | null>(null);
  // Bumped each time the map asks for sign-in (Save while signed out).
  const [needAuthAt, setNeedAuthAt] = useState(0);
  const readyRef = useRef(false);

  const send = useCallback(
    (msg: Outbound) => {
      const w = iframeRef.current?.contentWindow;
      if (!w) return;
      w.postMessage({ source: "paisamap-shell", ...msg }, MAP_ORIGIN);
    },
    [iframeRef],
  );

  useEffect(() => {
    function onMessage(ev: MessageEvent) {
      if (ev.origin !== MAP_ORIGIN) return;
      const d = ev.data;
      if (!d || d.source !== "paisamap-map") return;
      switch (d.type) {
        case "ready":
          readyRef.current = true;
          setReady(true);
          break;
        case "select":
          setSelected({ pincode: d.pincode ?? null, name: d.name ?? null, lat: d.lat, lng: d.lng, tier: d.tier ?? null, auto: !!d.auto });
          break;
        case "compare":
          setCompare(Array.isArray(d.pincodes) ? d.pincodes : []);
          break;
        case "saved":
          setSaved((prev) => (prev.includes(d.pincode) ? prev : [...prev, d.pincode]));
          break;
        case "viewport":
          if (d.kpis) setKpis(d.kpis);
          break;
        case "needAuth":
          setNeedAuthAt(Date.now());
          break;
      }
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  return { ready, selected, compare, saved, kpis, needAuthAt, send };
}

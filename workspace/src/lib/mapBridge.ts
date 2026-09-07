// Bridge between the React map-first shell and the embedded public map
// (index.html served at /?embed=1). The map posts {source:"paisamap-map", ...}
// messages up; the shell posts {source:"paisamap-shell", ...} down. In dev the
// map is served by the Flask server on :8080 (the vite dev server only proxies
// /api and /assets), so it's a cross-origin frame — in prod it's same-origin.

import { useCallback, useEffect, useRef, useState } from "react";

const MAP_ORIGIN = import.meta.env.DEV ? "http://localhost:8080" : window.location.origin;
export const MAP_SRC = `${import.meta.env.DEV ? "http://localhost:8080" : ""}/?embed=1`;

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
  | { type: "setMyStores"; stores: MyStorePoint[] };

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
          setSelected({ pincode: d.pincode ?? null, name: d.name ?? null, lat: d.lat, lng: d.lng, tier: d.tier ?? null });
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

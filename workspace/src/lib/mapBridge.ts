// Bridge between the React map-first shell and the embedded public map
// (index.html served at /?embed=1). The map posts {source:"paisamap-map", ...}
// messages up; the shell posts {source:"paisamap-shell", ...} down. In dev the
// map is served by the Flask server on :8080 (the vite dev server only proxies
// /api and /assets), so it's a cross-origin frame — in prod it's same-origin.

import { useCallback, useEffect, useRef, useState } from "react";

const MAP_ORIGIN = import.meta.env.DEV ? "http://localhost:8080" : window.location.origin;
export const MAP_SRC = `${import.meta.env.DEV ? "http://localhost:8080" : ""}/?embed=1`;

export interface MapSelection {
  pincode: string | null;
  name: string | null;
  lat: number;
  lng: number;
}

export interface MapKpis {
  pincodesInView: number;
  metricLabel: string;
  metricAvg: number | null;
  medianIncome: number | null;
  topTierZones: number;
}

type Outbound =
  | { type: "setSignal"; col: string }
  | { type: "flyTo"; pincode: string }
  | { type: "toggleCompare"; pincode: string }
  | { type: "save"; pincode: string }
  | { type: "refreshAuth" }
  | { type: "requestViewport" };

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
          setSelected({ pincode: d.pincode ?? null, name: d.name ?? null, lat: d.lat, lng: d.lng });
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

import { ramp, RUPEE_DEEP } from "./chartTheme";

export interface MiniMapPoint {
  lat: number | null;
  lng: number | null;
  /** 0–100 economic score to tint by, matching the map's own ramp. Omit (vs.
   *  null) to render a plain brand-colour dot instead of the ramp's "unknown"
   *  grey — the caller may simply not have fetched a score for this point. */
  score?: number | null;
}

/**
 * Per-project map thumbnail — plots saved locations scaled to their own
 * lat/lng bounding box, tinted by economic score where known. Client-side
 * SVG (no tiles, no new server rasterisation pipeline): cheap, and reuses
 * the same `ramp("ppi", …)` coloring the map and SavedLocations already use,
 * so a project's thumbnail reads consistently with the rest of the app.
 */
export function MiniMap({
  points,
  width = 200,
  height = 120,
}: {
  points: MiniMapPoint[];
  width?: number;
  height?: number;
}) {
  const valid = points.filter(
    (p): p is MiniMapPoint & { lat: number; lng: number } => p.lat != null && p.lng != null
  );

  if (valid.length === 0) {
    return (
      <svg className="mini-map mini-map-empty" viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
        <rect width={width} height={height} rx={8} fill="var(--paper-3)" />
        <g stroke="var(--border)" strokeWidth={1.5} fill="none">
          <circle cx={width / 2} cy={height / 2 - 3} r={7} />
          <path d={`M${width / 2} ${height / 2 + 4} l0 7`} />
        </g>
      </svg>
    );
  }

  const pad = Math.min(width, height) * 0.16;
  const lats = valid.map((p) => p.lat);
  const lngs = valid.map((p) => p.lng);
  // A single point (or a cluster of near-identical ones) collapses the span
  // to ~0 — fall back to a small fixed degree-span so it centers instead of
  // dividing by zero.
  const latSpan = Math.max(Math.max(...lats) - Math.min(...lats), 0.01);
  const lngSpan = Math.max(Math.max(...lngs) - Math.min(...lngs), 0.01);
  const latMin = Math.min(...lats);
  const lngMin = Math.min(...lngs);

  const project = (lat: number, lng: number) => {
    const x = pad + ((lng - lngMin) / lngSpan) * (width - pad * 2);
    // Latitude increases northward — flip so higher lat renders higher (lower y).
    const y = pad + (1 - (lat - latMin) / latSpan) * (height - pad * 2);
    return [x, y];
  };

  return (
    <svg className="mini-map" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="xMidYMid meet" aria-hidden="true">
      <rect width={width} height={height} rx={8} fill="var(--paper-2)" />
      {valid.map((p, i) => {
        const [x, y] = project(p.lat, p.lng);
        const fill = p.score !== undefined ? ramp("ppi", p.score) : RUPEE_DEEP;
        return <circle key={i} cx={x} cy={y} r={valid.length > 1 ? 4.5 : 6} fill={fill} fillOpacity={0.88} stroke="#fff" strokeWidth={1.25} />;
      })}
    </svg>
  );
}

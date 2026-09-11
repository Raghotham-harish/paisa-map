import { GRID, RUPEE, STROKE_PRIMARY } from "./chartTheme";

/** Inline horizontal fill bar for a list row — e.g. budget used, score out of 100. */
export function MicroBar({
  value,
  max = 100,
  color = RUPEE,
}: {
  value: number;
  max?: number;
  color?: string;
}) {
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  return (
    <span className="micro-bar" role="img" aria-label={`${Math.round(pct)}%`}>
      <span className="micro-bar-fill" style={{ width: `${pct}%`, background: color }} />
    </span>
  );
}

/** Tiny trend line for a list row — reuses the Forecast chart theme's stroke weight. */
export function Sparkline({
  points,
  width = 64,
  height = 20,
  color = RUPEE,
}: {
  points: number[];
  width?: number;
  height?: number;
  color?: string;
}) {
  if (points.length < 2) {
    return <svg className="sparkline" width={width} height={height} aria-hidden="true" />;
  }
  const lo = Math.min(...points);
  const hi = Math.max(...points);
  const span = hi - lo || 1;
  const pad = 2;
  const step = (width - pad * 2) / (points.length - 1);
  const path = points
    .map((v, i) => {
      const x = pad + i * step;
      const y = pad + (1 - (v - lo) / span) * (height - pad * 2);
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg className="sparkline" width={width} height={height} aria-hidden="true">
      <line x1={0} y1={height - 0.5} x2={width} y2={height - 0.5} stroke={GRID} strokeWidth={1} />
      <path d={path} fill="none" stroke={color} strokeWidth={STROKE_PRIMARY} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

import { MapKpis } from "../../lib/mapBridge";

function fmt(n: number | null, kind: "int" | "money" | "num") {
  if (n == null) return "—";
  if (kind === "money") return `₹${Math.round(n).toLocaleString("en-IN")}`;
  if (kind === "int") return Math.round(n).toLocaleString("en-IN");
  return n.toLocaleString("en-IN", { maximumFractionDigits: 1 });
}

export function KpiStrip({ kpis }: { kpis: MapKpis | null }) {
  const tiles: { label: string; value: string }[] = kpis
    ? [
        { label: "Pincodes in view", value: fmt(kpis.pincodesInView, "int") },
        { label: kpis.metricLabel, value: fmt(kpis.metricAvg, "num") },
        { label: "Median income /mo", value: fmt(kpis.medianIncome, "money") },
        { label: "Top-tier zones", value: fmt(kpis.topTierZones, "int") },
        // A population proxy, not a project's calibrated reachable-spend
        // figure (that only exists at forecast time, per-project) — omitted
        // entirely rather than shown as "—" when the estimate file hasn't
        // loaded, since a whole extra always-dash tile reads as broken.
        ...(kpis.householdsInView != null
          ? [{ label: "Households in view", value: fmt(kpis.householdsInView, "int") }]
          : []),
      ]
    : [];

  if (!kpis) return null;

  return (
    <div className="kpi-strip">
      {tiles.map((t) => (
        <div className="card kpi-tile" key={t.label}>
          <div className="kicker">{t.label}</div>
          <div className="mono kpi-value">{t.value}</div>
        </div>
      ))}
    </div>
  );
}

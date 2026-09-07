import { useEffect, useState } from "react";
import { api, LocationScore, Project } from "../../lib/api";

type Ranked = LocationScore & { rank: number };

function money(n: number | null | undefined) {
  return n == null ? "—" : `₹${Math.round(n).toLocaleString("en-IN")}`;
}

export function CompareModal({
  pincodes,
  project,
  onClose,
}: {
  pincodes: string[];
  project: Project | null;
  onClose: () => void;
}) {
  const [rows, setRows] = useState<Ranked[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .compareLocations(pincodes, {
        avg_ticket: project?.avg_ticket ?? null,
        target_segment: project?.target_segment ?? null,
        business_type: project?.business_type ?? project?.industry ?? null,
      })
      .then((d) => setRows(d.locations))
      .catch(() => setError("Couldn't load the comparison."));
  }, [pincodes, project]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const rowDef: { label: string; render: (l: Ranked) => React.ReactNode }[] = [
    { label: "Economic score", render: (l) => <span className="cmp-score">{l.economic_score ?? "—"}<small> /100</small></span> },
    { label: "Opportunity", render: (l) => l.opportunity?.opportunity_score ?? l.economic_score ?? "—" },
    { label: "Suitability", render: (l) => l.opportunity?.suitability ?? "—" },
    { label: "Risk", render: (l) => l.risk.level },
    { label: "Income /mo", render: (l) => money(l.income) },
    { label: "Spend /mo", render: (l) => money(l.spend) },
    { label: "vs nearby", render: (l) => (l.benchmark.neighbours?.diff_pct != null ? `${l.benchmark.neighbours.diff_pct}%` : "—") },
    { label: "Top signals", render: (l) => (l.top_signals ?? []).slice(0, 3).join(", ") || "—" },
    { label: "Read", render: (l) => <span className="cmp-summary">{l.executive_summary}</span> },
  ];

  return (
    <div className="map-modal-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="card map-modal" role="dialog" aria-label="Compare locations">
        <div className="map-modal-head">
          <h2 style={{ margin: 0, fontSize: 17 }}>Compare locations</h2>
          <button type="button" aria-label="Close" onClick={onClose}>
            <svg width="16" height="16" viewBox="0 0 16 16" stroke="currentColor" strokeWidth="1.7"><path d="M3 3l10 10M13 3L3 13" /></svg>
          </button>
        </div>
        <p style={{ fontSize: 12, color: "var(--ink-soft)", margin: "0 0 12px" }}>
          Modelled estimates from PaisaMap's PPI ensemble — not real transaction records.
        </p>
        {error && <p style={{ color: "var(--flame)", fontSize: 13 }}>{error}</p>}
        {!rows && !error && <p className="loading">Loading…</p>}
        {rows && (
          <div className="cmp-scroll">
            <table className="cmp-table">
              <thead>
                <tr>
                  <th />
                  {rows.map((l) => (
                    <th key={l.pincode}>
                      <span className={`cmp-rank ${l.rank === 1 ? "r1" : ""}`}>{l.rank}</span>
                      {l.name}
                      <span className="cmp-pin">{l.pincode}</span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rowDef.map((rd) => (
                  <tr key={rd.label}>
                    <th>{rd.label}</th>
                    {rows.map((l) => (
                      <td key={l.pincode}>{rd.render(l)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

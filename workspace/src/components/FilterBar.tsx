import { ReactNode } from "react";

/**
 * Shared label+select filter row — the pattern Forecast's hotspot chart
 * (`HotspotBubbles` in forecastCharts.tsx) introduced for "Colour by / X axis
 * / Show" filters. Pulled out here so other screens (list pages, future B3
 * redesigns) can reuse the same row instead of re-implementing it ad hoc.
 */
export function FilterBar({ children }: { children: ReactNode }) {
  return <div className="filter-bar">{children}</div>;
}

export function FilterSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <label>
      {label}
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    </label>
  );
}

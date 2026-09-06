import { SignalCatalogItem } from "../../lib/api";
import { SingleSelect } from "../MultiSelect";

export function MapControls({
  catalog,
  primarySignal,
  onPrimaryChange,
}: {
  catalog: SignalCatalogItem[];
  primarySignal: string;
  onPrimaryChange: (key: string) => void;
}) {
  const current = catalog.find((c) => c.key === primarySignal);
  return (
    <div className="card map-controls">
      <span className="kicker">Map controls</span>

      <div className="mc-section">
        <span className="mc-label">Signal layer</span>
        <SingleSelect
          options={catalog.map((c) => ({ value: c.key, label: c.label, meta: c.pro ? "PRO" : c.group }))}
          value={primarySignal}
          onChange={onPrimaryChange}
          placeholder="Choose a signal…"
        />
      </div>

      <div className="mc-section">
        <div className="mc-legend-bar" />
        <div className="mc-legend-ticks">
          <span>low</span>
          <span>{current?.label ?? "PPI"}</span>
          <span>high</span>
        </div>
      </div>

      <p className="mc-note">
        Choropleth, boundary polygons, hotspot clusters and distance rings render on the map.
        Pan or zoom to update the metrics below.
      </p>
    </div>
  );
}

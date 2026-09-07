import { SignalCatalogItem } from "../../lib/api";
import { MapKpis, MapStyle } from "../../lib/mapBridge";
import { SingleSelect } from "../MultiSelect";

const STYLE_OPTIONS: { value: MapStyle; label: string }[] = [
  { value: "symbol", label: "Pins" },
  { value: "choropleth", label: "Choropleth" },
  { value: "heatmap", label: "Heatmap" },
  { value: "cluster", label: "Clusters" },
];

export function MapControls({
  catalog,
  primarySignal,
  onPrimaryChange,
  mapStyle,
  onMapStyleChange,
  ringsOn,
  onRingsToggle,
  catchmentKm,
  myStoresOn,
  onMyStoresToggle,
  myStoresCount,
  kpis,
}: {
  catalog: SignalCatalogItem[];
  primarySignal: string;
  onPrimaryChange: (key: string) => void;
  mapStyle: MapStyle;
  onMapStyleChange: (style: MapStyle) => void;
  ringsOn: boolean;
  onRingsToggle: (on: boolean) => void;
  catchmentKm: number;
  myStoresOn: boolean;
  onMyStoresToggle: (on: boolean) => void;
  myStoresCount: number;
  kpis: MapKpis | null;
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

      <div className="mc-section">
        <span className="mc-label">Map style</span>
        <div className="mc-style-row">
          {STYLE_OPTIONS.map((s) => (
            <button
              key={s.value}
              type="button"
              className={`mc-style-btn ${mapStyle === s.value ? "active" : ""}`}
              onClick={() => onMapStyleChange(s.value)}
            >
              {s.label}
            </button>
          ))}
        </div>
      </div>

      <div className="mc-section mc-toggles">
        <label className="mc-toggle">
          <input type="checkbox" checked={ringsOn} onChange={(e) => onRingsToggle(e.target.checked)} />
          Distance ring ({catchmentKm.toFixed(1)} km)
        </label>
        <label className="mc-toggle">
          <input
            type="checkbox"
            checked={myStoresOn}
            disabled={myStoresCount === 0}
            onChange={(e) => onMyStoresToggle(e.target.checked)}
          />
          My stores{myStoresCount > 0 ? ` (${myStoresCount})` : ""}
        </label>
      </div>

      {kpis && (kpis.coreCount > 0 || kpis.edgeCount > 0 || kpis.expansionCount > 0) && (
        <div className="mc-section">
          <span className="mc-label">Market tiers in view</span>
          <div className="mc-tiers">
            <span className="mc-tier mc-tier-core">Core {kpis.coreCount}</span>
            <span className="mc-tier mc-tier-edge">Edge {kpis.edgeCount}</span>
            <span className="mc-tier mc-tier-expansion">Expansion {kpis.expansionCount}</span>
          </div>
        </div>
      )}

      <p className="mc-note">Pan or zoom to update the metrics below.</p>
    </div>
  );
}

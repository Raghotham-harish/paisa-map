import { SignalCatalogItem } from "../../lib/api";
import { MapKpis, MapStyle, SUITABILITY_KEY } from "../../lib/mapBridge";
import { SingleSelect } from "../MultiSelect";

const STYLE_OPTIONS: { value: MapStyle; label: string }[] = [
  { value: "symbol", label: "Pins" },
  { value: "choropleth", label: "Choropleth" },
  { value: "heatmap", label: "Heatmap" },
  { value: "cluster", label: "Clusters" },
];

// Mirrors index.html's PALETTES / LEGEND_TICKS (low → high) so the shell legend
// shows the same colours and bucket boundaries the map actually paints. These
// are now two-hue DIVERGING ramps — see the note on PALETTES in index.html.
const LEGEND: Record<string, { palette: string[]; ticks: string[] }> = {
  [SUITABILITY_KEY]: {
    palette: ["#D53E4F", "#FC8D59", "#FEE08B", "#E6F598", "#99D594", "#3288BD"],
    ticks: ["0", "20", "35", "50", "65", "80+"],
  },
  est_monthly_income_hh: {
    palette: ["#762A83", "#AF8DC3", "#E7D4E8", "#D9F0D3", "#7FBF7B", "#1B7837"],
    ticks: ["<20k", "₹20k", "₹35k", "₹50k", "₹75k", "₹1.2L+"],
  },
  est_monthly_spend_hh: {
    palette: ["#8C510A", "#D8B365", "#F6E8C3", "#C7EAE5", "#5AB4AC", "#01665E"],
    ticks: ["<17k", "₹17k", "₹28k", "₹40k", "₹60k", "₹90k+"],
  },
  ppi_ml: {
    palette: ["#D73027", "#FC8D59", "#FEE08B", "#D9EF8B", "#91CF60", "#1A9850"],
    ticks: ["<60", "60", "80", "100", "120", "145+"],
  },
};
const SIGNAL_LEGEND = {
  palette: ["#B2182B", "#EF8A62", "#FDDBC7", "#D1E5F0", "#67A9CF", "#2166AC"],
  ticks: ["low", "", "", "", "", "high"],
};

export function MapControls({
  catalog,
  primarySignal,
  onPrimaryChange,
  suitabilityAvailable,
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
  suitabilityAvailable: boolean;
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
  const signalOptions = [
    ...(suitabilityAvailable
      ? [{ value: SUITABILITY_KEY, label: "Suitability (project fit)", meta: "FIT" }]
      : []),
    ...catalog.map((c) => ({ value: c.key, label: c.label, meta: c.pro ? "PRO" : c.group })),
  ];
  const currentLabel =
    primarySignal === SUITABILITY_KEY
      ? "Project fit"
      : catalog.find((c) => c.key === primarySignal)?.label ?? "PPI";
  return (
    <div className="card map-controls">
      <span className="kicker">Map controls</span>

      <div className="mc-section">
        <span className="mc-label">Signal layer</span>
        <SingleSelect
          options={signalOptions}
          value={primarySignal}
          onChange={onPrimaryChange}
          placeholder="Choose a signal…"
        />
        {primarySignal === SUITABILITY_KEY && (
          <p className="mc-note" style={{ marginTop: 4 }}>
            Every pincode scored 0–100 on how well it fits this project's signals
            and average ticket. Edit the signals in the top bar to re-weight it.
          </p>
        )}
      </div>

      <div className="mc-section">
        {(() => {
          const l = LEGEND[primarySignal] ?? SIGNAL_LEGEND;
          return (
            <>
              <div className="mc-legend-bar">
                {l.palette.map((c, i) => (
                  <i key={i} style={{ background: c }} />
                ))}
              </div>
              <div className="mc-legend-ticks">
                {l.ticks.map((t, i) => (
                  <span key={i}>{t}</span>
                ))}
              </div>
              <div className="mc-legend-ticks" style={{ marginTop: 2 }}>
                <span>{currentLabel}</span>
              </div>
            </>
          );
        })()}
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

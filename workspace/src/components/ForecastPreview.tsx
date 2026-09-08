import { Link } from "react-router-dom";
import { illustrations } from "../lib/illustrations";
import { HotspotBubbles, KpiStrip, LeverLines, ReachCurve, SplitBars } from "./forecastCharts";

/**
 * Rich empty state for the Forecast page — the real dashboard, populated with
 * clearly-tagged sample numbers, so users can see the destination before they
 * upload store data. Same layout and components as the live forecast.
 */

const SAMPLE_LEVERS = [
  { label: "Avg income /mo", fit: 82 },
  { label: "UPI txn value", fit: 54 },
  { label: "Bank branches /lakh", fit: 71 },
  { label: "Property rate /sqft", fit: 33 },
  { label: "Footfall index", fit: 63 },
];
const SAMPLE_CANDIDATES = [
  { label: "Indiranagar", values: [88, 61, 74, 40, 70] },
  { label: "Whitefield", values: [72, 48, 66, 55, 52] },
  { label: "HSR Layout", values: [80, 70, 58, 44, 77] },
];
const SAMPLE_CURVE = [0, 3.1, 5.4, 7.0, 8.0, 8.6, 8.9].map((y, i) => ({
  investment: [0, 0.5, 1.0, 1.5, 2.0, 2.6, 3.2][i] * 1e7,
  monthly_revenue: y * 1e5,
}));
const SAMPLE_SITES = [
  { name: "Indiranagar", x: 42e5, y: 8.5e5, capex: 90e5, tier: "core" },
  { name: "Whitefield", x: 34e5, y: 6.0e5, capex: 62e5, tier: "edge" },
  { name: "HSR Layout", x: 30e5, y: 7.2e5, capex: 55e5, tier: "core" },
  { name: "Yelahanka", x: 18e5, y: 3.5e5, capex: 40e5, tier: "expansion" },
  { name: "Kanakapura Rd", x: 22e5, y: 4.8e5, capex: 44e5, tier: "edge" },
];

export function ForecastPreview({ detail }: { detail?: string | null }) {
  return (
    <>
      <div className="card forecast-cta" style={{ marginBottom: 20 }}>
        <img className="forecast-cta-art" src={illustrations.folderFiles} alt="" aria-hidden="true" />
        <div className="forecast-cta-body">
          <div className="kicker">Forecast needs your store data</div>
          <p style={{ fontSize: 13.5, lineHeight: 1.6, margin: "6px 0 12px", maxWidth: 560 }}>
            {detail ||
              "Upload at least 3 of your existing stores (with monthly revenue). PaisaMap calibrates the model on how your own locations actually perform, then projects where the next stores should go. Everything below is illustrative sample data."}
          </p>
          <Link className="btn" to="/customer-data">Upload store data</Link>
        </div>
      </div>

      <div className="preview-wrap">
        <span className="preview-badge">Sample forecast — real numbers after you upload stores</span>

        <KpiStrip
          items={[
            { label: "Added revenue / mo", value: "₹8.0 L" },
            { label: "Segment market capture", value: "4.2% → 6.8%" },
            { label: "New stores", value: "4" },
            { label: "Blended payback", value: "14 mo" },
            { label: "Candidates weighed", value: "12,480" },
          ]}
        />

        <div className="card fc-chart-card">
          <div className="fc-card-head">
            <span className="kicker">Investment → projected reachable revenue</span>
            <span className="wiz-hint">the dashed line is your budget</span>
          </div>
          <ReachCurve points={SAMPLE_CURVE} budget={2e7} dimFrom={2e7} />
          <div className="callout-sweetspot">
            <b>Sweet spot ≈ ₹2.0 Cr.</b> Beyond this, each extra rupee buys materially less revenue.
          </div>
        </div>

        <div className="card fc-chart-card">
          <div className="kicker">Does each signal actually move revenue?</div>
          <LeverLines levers={SAMPLE_LEVERS} candidates={SAMPLE_CANDIDATES} />
        </div>

        <div className="card fc-chart-card">
          <div className="fc-card-head">
            <span className="kicker">Hotspots — market size vs opportunity</span>
            <span className="wiz-hint">
              bubble = est. CapEx · <span style={{ color: "var(--rupee-deep)" }}>●</span> core ·{" "}
              <span style={{ color: "#8A5A00" }}>●</span> edge · <span style={{ color: "var(--ink-soft)" }}>●</span> expansion
            </span>
          </div>
          <HotspotBubbles points={SAMPLE_SITES} />
        </div>

        <div className="card fc-chart-card">
          <div className="kicker">Recommended investment split</div>
          <SplitBars
            rows={[
              { name: "Whitefield", investment: 82e5, share: 100, note: "+₹2.4 Cr reachable spend" },
              { name: "Indiranagar", investment: 64e5, share: 78, note: "+₹1.9 Cr reachable spend" },
              { name: "HSR Layout", investment: 54e5, share: 66, note: "+₹1.5 Cr reachable spend" },
            ]}
          />
          <div className="fc-nudge">
            <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="#7A5B12" strokeWidth="1.5" style={{ flex: "none", marginTop: 1 }}>
              <path d="M8 1l2 4 4 .6-3 3 .7 4L8 14.6 4.3 16.6 5 12.6 2 9.6l4-.6z" />
            </svg>
            <div><b>Nudge:</b> move ₹15 L from Indiranagar to Whitefield → <b>+₹40 L/mo</b>, payback 14 → 13 mo.</div>
          </div>
        </div>

        <div className="fc-two">
          <div className="card">
            <div className="kicker">Forecast confidence</div>
            <span className="pill reviewing" style={{ marginTop: 8, display: "inline-block" }}>medium</span>
            <ul className="fc-rail-bullets">
              <li>Fitted regression · R² 0.58</li>
              <li>±18% confidence band</li>
              <li>Reach is modelled household spend, not booked sales</li>
            </ul>
          </div>
          <div className="card">
            <div className="kicker">Levers you're under-using</div>
            <div className="fc-lever-list">
              {SAMPLE_LEVERS.map((l) => {
                const word = l.fit < 40 ? "weak" : l.fit < 60 ? "partial" : "strong";
                const tone = l.fit < 40 ? "rejected" : l.fit < 60 ? "reviewing" : "delta-pos";
                return (
                  <div className="fc-lever" key={l.label}>
                    <span>{l.label}</span>
                    <span className={`pill ${tone}`}>{word}</span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

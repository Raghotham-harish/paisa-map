import { illustrations } from "../lib/illustrations";
import { ReturnToLink } from "./ReturnTo";
import {
  ConfidenceMeter, CurveDrivers, ForecastSummaryCard, HotspotBubbles, KpiStrip, LeverSplitRadar,
  LocationCompareRadar, LocationFactorTable, LocationSwotTable, ReachCurve, SignalRevenuePanel, SplitBars,
} from "./forecastCharts";

/**
 * Rich empty state for the Forecast page — the real dashboard, populated with
 * clearly-tagged sample numbers, so users can see the destination before they
 * upload store data. Same layout and components as the live forecast.
 */

const SAMPLE_CURVE = [0, 3.1, 5.4, 7.0, 8.0, 8.6, 8.9].map((y, i) => ({
  investment: [0, 0.5, 1.0, 1.5, 2.0, 2.6, 3.2][i] * 1e7,
  monthly_revenue: y * 1e5,
  stores: [0, 1, 2, 3, 4, 5, 6][i],
}));

const SAMPLE_DRIVERS = [
  { key: "reachable_spend", label: "Reachable spend per site", value: 9.2e8, unit: "currency", delta_pct: 34, direction: "up" as const, basis: "sample" },
  { key: "wallet_share", label: "Category wallet share", value: 5, unit: "percent", delta_pct: null, direction: "flat" as const, basis: "sample" },
  { key: "entrant_capture", label: "New-entrant capture", value: 1, unit: "percent", delta_pct: null, direction: "flat" as const, basis: "sample" },
  { key: "ppi", label: "PPI percentile of top sites", value: 78, unit: "index", delta_pct: 22, direction: "up" as const, basis: "sample" },
  { key: "cannibalisation", label: "Catchment-overlap drag", value: 12, unit: "percent", delta_pct: null, direction: "down" as const, basis: "sample" },
];

const SAMPLE_SIGNALS = [
  { label: "Avg income /mo", lever_fit: 82, site_avg_percentile: 80, verdict: "moves revenue", direction: "positive" as const, sample_size: 6 },
  { label: "Bank branches /lakh", lever_fit: 61, site_avg_percentile: 66, verdict: "moves revenue", direction: "positive" as const, sample_size: 6 },
  { label: "UPI txn value", lever_fit: 34, site_avg_percentile: 54, verdict: "weak link", direction: "positive" as const, sample_size: 6 },
  { label: "Property rate /sqft", lever_fit: null, site_avg_percentile: 40, verdict: "unproven — upload stores", direction: null, sample_size: 0 },
];

const mkFactors = (base: number[]) => [
  ["supply_chain", "Supply chain"], ["distribution", "Distribution access"], ["transport", "Transport & logistics"],
  ["mobility", "Mobility & connectivity"], ["eodb", "Ease of doing business"], ["geo_infra", "Geo-infra / residential mix"],
].map(([key, label], i) => ({ key, label, score: base[i], basis: "sample", delta_vs_median: base[i] - 50, delta_vs_your_stores: null }));

const SAMPLE_FACTOR_SITES = [
  { name: "Indiranagar", factors: mkFactors([58, 88, 74, 80, 55, 92]) },
  { name: "Whitefield", factors: mkFactors([72, 60, 66, 55, 70, 58]) },
  { name: "HSR Layout", factors: mkFactors([50, 78, 62, 72, 52, 80]) },
];

const SAMPLE_SWOT = SAMPLE_FACTOR_SITES.map((s) => ({
  name: s.name,
  swot: {
    strengths: s.factors.filter((f) => f.score >= 65).map((f) => ({ label: f.label, score: f.score, basis: "sample" })),
    weaknesses: s.factors.filter((f) => f.score <= 55).map((f) => ({ label: f.label, score: f.score, basis: "sample" })),
    opportunities: [{ label: "Room to grow: footfall", basis: "sample" }],
    threats: [{ label: "Medium data-volatility risk", basis: "sample" }],
  },
}));

const SAMPLE_SITES = [
  { name: "Indiranagar", reach_gross: 9.2e8, monthly_revenue: 8.5e5, households: 41000, capex: 90e5, ppi_percentile: 88, income_percentile: 84, footfall_percentile: 79, within_budget: true, state: "Karnataka", tier: "core" },
  { name: "Whitefield", reach_gross: 7.4e8, monthly_revenue: 6.0e5, households: 52000, capex: 62e5, ppi_percentile: 62, income_percentile: 58, footfall_percentile: 55, within_budget: true, state: "Karnataka", tier: "edge" },
  { name: "HSR Layout", reach_gross: 8.0e8, monthly_revenue: 7.2e5, households: 38000, capex: 55e5, ppi_percentile: 80, income_percentile: 77, footfall_percentile: 74, within_budget: true, state: "Karnataka", tier: "core" },
  { name: "Yelahanka", reach_gross: 4.0e8, monthly_revenue: 3.5e5, households: 29000, capex: 40e5, ppi_percentile: 44, income_percentile: 41, footfall_percentile: 38, within_budget: false, state: "Karnataka", tier: "expansion" },
  { name: "Kanakapura Rd", reach_gross: 5.0e8, monthly_revenue: 4.8e5, households: 33000, capex: 44e5, ppi_percentile: 55, income_percentile: 52, footfall_percentile: 49, within_budget: false, state: "Karnataka", tier: "edge" },
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
              "Every project already gets a benchmark forecast. Upload at least 3 of your existing stores (with monthly revenue) and PaisaMap calibrates the model on how your own locations actually perform. Everything below is illustrative sample data."}
          </p>
          <ReturnToLink className="btn" to="/customer-data" fromLabel="your forecast">Upload store data</ReturnToLink>
        </div>
      </div>

      <div className="preview-wrap">
        <span className="preview-badge">Sample forecast — real numbers after you upload stores</span>

        <KpiStrip
          items={[
            { label: "Added revenue / mo", value: "₹8.0 L" },
            { label: "Segment market capture", value: "1.4% → 2.6%" },
            { label: "New stores this budget funds", value: "4" },
            { label: "Blended payback", value: "14 mo" },
            { label: "Candidates weighed", value: "1,240" },
          ]}
        />

        <div className="card fc-chart-card">
          <div className="fc-card-head">
            <span className="kicker">Investment → projected reachable revenue</span>
          </div>
          <div className="fc-curve-layout">
            <div>
              <ReachCurve points={SAMPLE_CURVE} budget={2e7} dimFrom={2e7} />
              <div className="callout-sweetspot"><b>Sweet spot ≈ ₹2.0 Cr.</b> Beyond this, each extra rupee buys materially less.</div>
            </div>
            <CurveDrivers drivers={SAMPLE_DRIVERS} />
          </div>
        </div>

        <div className="card fc-chart-card">
          <div className="kicker">Does each signal actually move revenue?</div>
          <SignalRevenuePanel rows={SAMPLE_SIGNALS} />
        </div>

        <div className="card fc-chart-card">
          <div className="kicker">Hotspots — market size vs modelled revenue</div>
          <HotspotBubbles points={SAMPLE_SITES} />
        </div>

        <div className="fc-two-wide">
          <div className="card fc-chart-card">
            <div className="kicker">Recommended budget split by lever</div>
            <LeverSplitRadar levers={[
              { label: "Advertising & Marketing", pct: 27 }, { label: "Sales & Distribution", pct: 20 },
              { label: "Promotions & Discounts", pct: 16 }, { label: "Production & Supply Chain", pct: 15 },
              { label: "Store Ops & CapEx", pct: 12 }, { label: "R&D / Product", pct: 10 },
            ]} />
          </div>
          <div className="card fc-chart-card">
            <div className="kicker">How the top sites score on operating factors</div>
            <LocationFactorTable sites={SAMPLE_FACTOR_SITES} />
          </div>
        </div>

        <div className="card fc-chart-card">
          <div className="kicker">Top sites compared — operating-factor profile</div>
          <LocationCompareRadar sites={SAMPLE_FACTOR_SITES} />
        </div>

        <div className="card fc-chart-card">
          <div className="kicker">Recommended investment split</div>
          <SplitBars
            rows={[
              { name: "Whitefield", investment: 82e5, share: 100, note: "+₹6.0 L/mo" },
              { name: "Indiranagar", investment: 64e5, share: 78, note: "+₹8.5 L/mo" },
              { name: "HSR Layout", investment: 54e5, share: 66, note: "+₹7.2 L/mo" },
            ]}
          />
        </div>

        <div className="fc-two">
          <div className="card">
            <div className="kicker">Forecast confidence</div>
            <ConfidenceMeter confidence="medium" bandPct={18} method="regression" r2={0.58} candidates={1240} />
          </div>
          <div className="card">
            <div className="kicker">SWOT — top recommended sites</div>
            <LocationSwotTable sites={SAMPLE_SWOT} />
          </div>
        </div>

        <ForecastSummaryCard
          headline="At ₹2.0 Cr — 4 new stores"
          stats={[
            { label: "Investment deployed", value: "₹2.0 Cr" },
            { label: "Added revenue / mo", value: "₹8.0 L", tone: "pos" },
            { label: "Added gross profit / mo", value: "₹2.8 L", tone: "pos" },
            { label: "Blended payback", value: "14 mo" },
            { label: "Market capture", value: "1.4% → 2.6%" },
            { label: "Gross profit over 18 mo", value: "₹33 L", tone: "pos" },
          ]}
          costs={[
            { label: "total fit-out CapEx", value: "₹2.0 Cr" },
            { label: "per store (avg)", value: "₹50 L" },
            { label: "credits for this forecast", value: "8" },
          ]}
          footnote="Sample values. Real numbers use your project's signals, target market and a capture rate fitted on your uploaded stores."
        />
      </div>
    </>
  );
}

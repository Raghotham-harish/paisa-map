import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ApiError, OutcomeGoal, Project, ProjectFields, RevenuePeriod, SignalCatalogItem, api } from "../lib/api";
import { MultiSelect, Option, SingleSelect } from "../components/MultiSelect";

const INDUSTRIES = [
  "Apparel & Footwear", "Jewellery & Accessories", "Food & Beverage / QSR", "Grocery & Kirana",
  "Consumer Electronics", "Furniture & Home", "Pharmacy & Wellness", "Beauty & Personal Care",
  "Banking & Financial Services", "Education & Coaching", "Healthcare & Diagnostics",
  "Real Estate", "Automotive", "Fitness & Gyms", "Hospitality & Hotels", "Logistics & Warehousing",
  "Manufacturing / Factory", "E-commerce / D2C",
].map((v) => ({ value: v, label: v }));

const SEGMENTS = [
  "Premium & upper-middle", "Mass-market", "Value / budget", "Students & young professionals",
  "Families with children", "Senior citizens", "Small businesses (B2B)", "Enterprises (B2B)",
].map((v) => ({ value: v, label: v }));

const OUTCOMES: { value: OutcomeGoal; label: string }[] = [
  { value: "revenue_reach", label: "Maximise revenue reach" },
  { value: "store_count", label: "Maximise number of stores" },
  { value: "balanced", label: "Balanced growth & reach" },
];

const STEPS = ["Company & industry", "Data & integrations", "Signals & market", "Investment & outcome"];

export default function ProjectWizard() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const editId = Number(params.get("edit")) || null;

  const [step, setStep] = useState(0);
  const [catalog, setCatalog] = useState<SignalCatalogItem[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [website, setWebsite] = useState("");
  const [industry, setIndustry] = useState("");
  const [segment, setSegment] = useState("");
  const [avgTicket, setAvgTicket] = useState("");
  const [signals, setSignals] = useState<string[]>(["ppi_ml"]);
  const [pincodes, setPincodes] = useState<string[]>([]);
  const [catchment, setCatchment] = useState(3);
  const [investment, setInvestment] = useState("");
  const [outcome, setOutcome] = useState<OutcomeGoal>("balanced");
  const [horizon, setHorizon] = useState("18");
  const [grossMargin, setGrossMargin] = useState("");
  const [revenuePeriod, setRevenuePeriod] = useState<RevenuePeriod>("monthly");

  useEffect(() => {
    api.signalCatalog().then((d) => setCatalog(d.signals)).catch(() => setCatalog([]));
    if (editId) {
      api.listProjects().then((d) => {
        const p = d.projects.find((x) => x.id === editId);
        if (!p) return;
        setName(p.name);
        setWebsite(p.website_url || "");
        setIndustry(p.industry || p.business_type || "");
        setSegment(p.target_segment || "");
        setAvgTicket(p.avg_ticket != null ? String(p.avg_ticket) : "");
        setSignals(p.signals?.length ? p.signals : ["ppi_ml"]);
        setPincodes(p.target_pincodes || []);
        if (p.catchment_km != null) setCatchment(p.catchment_km);
        setInvestment(p.total_investment != null ? String(p.total_investment) : "");
        if (p.outcome_goal) setOutcome(p.outcome_goal);
        if (p.time_horizon_months != null) setHorizon(String(p.time_horizon_months));
        setGrossMargin(p.gross_margin_pct != null ? String(p.gross_margin_pct) : "");
        if (p.revenue_period) setRevenuePeriod(p.revenue_period);
      });
    }
  }, [editId]);

  const signalOptions: Option[] = useMemo(
    () => catalog.map((c) => ({ value: c.key, label: c.label, meta: c.pro ? "PRO" : c.group })),
    [catalog],
  );

  const canFinish = name.trim().length > 0;

  const submit = async () => {
    setSaving(true);
    setError(null);
    const fields: ProjectFields = {
      name: name.trim(),
      website_url: website || undefined,
      industry: industry || undefined,
      business_type: industry || undefined,
      target_segment: segment || undefined,
      avg_ticket: avgTicket || undefined,
      signals,
      target_pincodes: pincodes,
      catchment_km: catchment,
      total_investment: investment || undefined,
      outcome_goal: outcome,
      time_horizon_months: horizon || undefined,
      gross_margin_pct: grossMargin || undefined,
      revenue_period: revenuePeriod,
    };
    try {
      const { project } = editId
        ? ((await api.updateProject(editId, fields)) as { project: Project })
        : await api.createProject(fields);
      navigate(`/map?project_id=${project.id}`);
    } catch (e) {
      setError(e instanceof ApiError && e.body?.error === "name is required" ? "Give the project a name." : "Couldn't save — try again.");
      setSaving(false);
      setStep(0);
    }
  };

  return (
    <>
      <h1 className="page-title">{editId ? "Edit project" : "New project"}</h1>
      <p className="page-sub">
        Every field is a search box — pick an option, or type something new and press Enter to add it.
      </p>

      <div className="wiz-rail">
        {STEPS.map((label, i) => (
          <button
            key={label}
            className={`wiz-step ${i === step ? "active" : ""} ${i < step ? "done" : ""}`}
            onClick={() => setStep(i)}
          >
            <span className="wiz-step-num">{i < step ? "✓" : i + 1}</span>
            {label}
          </button>
        ))}
      </div>

      {error && <p style={{ color: "var(--flame)", fontSize: 13, marginBottom: 14 }}>{error}</p>}

      <div className="card wiz-card">
        {step === 0 && (
          <div className="field-grid">
            <label>
              Project name
              <input type="text" placeholder="e.g. Bangalore Retail Expansion" value={name} onChange={(e) => setName(e.target.value)} />
            </label>
            <label>
              Website
              <input type="text" placeholder="yourbusiness.com" value={website} onChange={(e) => setWebsite(e.target.value)} />
            </label>
            <label>
              Primary industry
              <SingleSelect options={INDUSTRIES} value={industry} onChange={setIndustry} allowCustom placeholder="Search industries…" />
            </label>
            <label>
              Average ticket (₹)
              <input type="text" inputMode="decimal" placeholder="e.g. 2400" value={avgTicket} onChange={(e) => setAvgTicket(e.target.value)} />
            </label>
          </div>
        )}

        {step === 1 && (
          <div>
            <p style={{ fontSize: 13, color: "var(--ink-soft)", marginTop: 0 }}>
              Link your own data so PaisaMap can weight each signal by what actually moves your sales.
              You can do this now or later from <Link to="/connections">Connections</Link> and <Link to="/customer-data">Store Data</Link>.
            </p>
            <div className="wiz-integrations">
              <div className="wiz-int-card">
                <b>Google Analytics</b>
                <span>Traffic & conversions by city</span>
                <Link className="btn secondary" to="/connections">Connect</Link>
              </div>
              <div className="wiz-int-card">
                <b>Search Console</b>
                <span>What people search before buying</span>
                <Link className="btn secondary" to="/connections">Connect</Link>
              </div>
              <div className="wiz-int-card wiz-int-soon">
                <b>Salesforce</b>
                <span>Won-deal ship-to pincodes</span>
                <span className="pill shortlist">Coming soon</span>
              </div>
              <div className="wiz-int-card">
                <b>CSV upload</b>
                <span>Store revenue, rent, footfall</span>
                <Link className="btn secondary" to="/customer-data">Upload</Link>
              </div>
            </div>
          </div>
        )}

        {step === 2 && (
          <div className="wiz-grid-2">
            <label>
              Business levers to capture
              <MultiSelect options={signalOptions} value={signals} onChange={setSignals} allowCustom placeholder="Search signals, or add your own…" />
              <span className="wiz-hint">{signals.length} selected · your uploaded revenue weights each one.</span>
            </label>
            <label>
              Target market — pincodes, areas or cities
              <MultiSelect
                options={[]}
                value={pincodes}
                onChange={setPincodes}
                allowCustom
                placeholder="Type a pincode or area, press Enter"
              />
            </label>
            <label>
              Segment
              <SingleSelect options={SEGMENTS} value={segment} onChange={setSegment} allowCustom placeholder="Search segments…" />
            </label>
            <label>
              Catchment radius per site — {catchment.toFixed(1)} km
              <input type="range" min={0.5} max={15} step={0.5} value={catchment} onChange={(e) => setCatchment(Number(e.target.value))} />
            </label>
          </div>
        )}

        {step === 3 && (
          <div className="field-grid">
            <label>
              Total investment (₹)
              <input type="text" inputMode="decimal" placeholder="e.g. 20000000" value={investment} onChange={(e) => setInvestment(e.target.value)} />
            </label>
            <label>
              What are you optimising for?
              <SingleSelect options={OUTCOMES} value={outcome} onChange={(v) => setOutcome(v as OutcomeGoal)} placeholder="Choose an outcome…" />
            </label>
            <label>
              Time horizon (months)
              <input type="text" inputMode="numeric" placeholder="18" value={horizon} onChange={(e) => setHorizon(e.target.value)} />
            </label>
            <label>
              Gross margin (%)
              <input type="text" inputMode="decimal" placeholder="e.g. 38" value={grossMargin} onChange={(e) => setGrossMargin(e.target.value)} />
              <span className="wiz-hint">Used for payback. Left blank → a retail default is assumed.</span>
            </label>
            <label>
              Store revenue figures are
              <SingleSelect
                options={[{ value: "monthly", label: "Monthly" }, { value: "annual", label: "Annual" }]}
                value={revenuePeriod}
                onChange={(v) => setRevenuePeriod((v as RevenuePeriod) || "monthly")}
                placeholder="Monthly or annual…"
              />
              <span className="wiz-hint">How to read the revenue column in your uploaded store data.</span>
            </label>
            <p className="wiz-hint" style={{ gridColumn: "1 / -1" }}>
              These feed the <b>Forecast</b> — investment → reachable-revenue curve, recommended ₹ split and payback —
              once you've uploaded store data for this project.
            </p>
          </div>
        )}
      </div>

      <div className="wiz-footer">
        <button className="btn secondary" disabled={step === 0} onClick={() => setStep((s) => Math.max(0, s - 1))}>
          ← Back
        </button>
        {step < STEPS.length - 1 ? (
          <button className="btn" onClick={() => setStep((s) => Math.min(STEPS.length - 1, s + 1))}>
            Continue →
          </button>
        ) : (
          <button className="btn" disabled={!canFinish || saving} onClick={submit}>
            {saving ? "Saving…" : editId ? "Save changes" : "Build intelligence →"}
          </button>
        )}
      </div>
    </>
  );
}

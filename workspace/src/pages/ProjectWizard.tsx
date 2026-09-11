import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  ApiError,
  CustomerUpload,
  OAuthConnection,
  OutcomeGoal,
  PROVIDER_LABEL,
  ProjectFields,
  RevenuePeriod,
  SignalCatalogItem,
  api,
} from "../lib/api";
import { MultiSelect, Option, SingleSelect } from "../components/MultiSelect";
import { fuzzyBestMatch } from "../lib/fuzzyMatch";
import { ReturnToLink } from "../components/ReturnTo";
import { Pill } from "../components/Pill";
import { useWorkspace } from "../lib/workspace";

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
  const { activeOrgId } = useWorkspace();

  const [step, setStep] = useState(0);
  const [catalog, setCatalog] = useState<SignalCatalogItem[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Set once the project exists on the server — either `edit`'s id up front,
  // or whatever createProject() hands back the first time step 0 is left.
  // From then on every step persists via updateProject, so leaving the
  // wizard partway through still leaves a real (draft) project behind.
  const [projectId, setProjectId] = useState<number | null>(editId);
  const [connections, setConnections] = useState<OAuthConnection[]>([]);
  const [uploads, setUploads] = useState<CustomerUpload[]>([]);

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

  // Real connection / upload state for step 1's integration cards — only
  // meaningful once a project id exists, so re-fetch whenever that appears
  // or the user lands back on that step.
  useEffect(() => {
    if (!projectId || step !== 1) return;
    api.getConnections(projectId).then((d) => setConnections(d.connections)).catch(() => setConnections([]));
    api.listCustomerUploads(projectId).then((d) => setUploads(d.uploads)).catch(() => setUploads([]));
  }, [projectId, step]);

  const signalOptions: Option[] = useMemo(
    () => catalog.map((c) => ({ value: c.key, label: c.label, meta: c.pro ? "PRO" : c.group })),
    [catalog],
  );

  const canFinish = name.trim().length > 0;

  const buildFields = (): ProjectFields => ({
    name: name.trim(),
    org_id: activeOrgId ?? undefined,
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
  });

  // Creates the project the first time it's called (as soon as step 0 has a
  // name), then updates it in place on every later call — this is the whole
  // "save draft" story: the schema already supports partial projects and
  // partial PUTs (blueprints/projects.py's _wizard_fields), so there was
  // never a backend gap here, just a frontend that only ever saved once at
  // the very end.
  const persistStep = async (): Promise<number | null> => {
    if (!name.trim()) {
      setError("Give the project a name.");
      return null;
    }
    setError(null);
    const fields = buildFields();
    try {
      if (projectId) {
        await api.updateProject(projectId, fields);
        return projectId;
      }
      const { project } = await api.createProject(fields);
      setProjectId(project.id);
      return project.id;
    } catch (e) {
      setError(e instanceof ApiError && e.body?.error === "name is required" ? "Give the project a name." : "Couldn't save — try again.");
      return null;
    }
  };

  const handleContinue = async () => {
    setSaving(true);
    const id = await persistStep();
    setSaving(false);
    if (id != null) setStep((s) => Math.min(STEPS.length - 1, s + 1));
  };

  const submit = async () => {
    setSaving(true);
    const id = await persistStep();
    setSaving(false);
    if (id != null) navigate(`/map?project_id=${id}`);
    else setStep(0);
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
              <IntegrationCard
                title="Google Analytics"
                desc="Traffic & conversions by city"
                conn={connections.find((c) => c.provider === "google_analytics")}
              />
              <IntegrationCard
                title="Search Console"
                desc="What people search before buying"
                conn={connections.find((c) => c.provider === "search_console")}
              />
              <div className="wiz-int-card wiz-int-soon">
                <b>Salesforce</b>
                <span>Won-deal ship-to pincodes</span>
                <Pill tone="shortlist">Coming soon</Pill>
              </div>
              <UploadCard uploads={uploads} />
            </div>
          </div>
        )}

        {step === 2 && (
          <div className="wiz-grid-2">
            <label>
              Business levers to capture
              <MultiSelect
                options={signalOptions}
                value={signals}
                onChange={setSignals}
                allowCustom
                placeholder="Search signals, or add your own…"
                suggest={(q) => fuzzyBestMatch(q, signalOptions)}
              />
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
          <button className="btn" disabled={saving} onClick={handleContinue}>
            {saving ? "Saving…" : "Continue →"}
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

/** GA4 / Search Console card — real linked-account state once a project
 *  exists, the plain "Connect" link before that (new, unsaved project). */
function IntegrationCard({ title, desc, conn }: { title: string; desc: string; conn: OAuthConnection | undefined }) {
  return (
    <div className="wiz-int-card">
      <b>{title}</b>
      <span>{desc}</span>
      {conn ? (
        <>
          <Pill tone={conn.status === "connected" ? "approved" : "rejected"}>
            {conn.status === "connected"
              ? `Linked${conn.external_account_email ? " · " + conn.external_account_email : ""}`
              : `Reconnect ${PROVIDER_LABEL[conn.provider]}`}
          </Pill>
          <ReturnToLink className="btn secondary" to="/connections" fromLabel="project setup">Manage</ReturnToLink>
        </>
      ) : (
        <ReturnToLink className="btn secondary" to="/connections" fromLabel="project setup">Connect</ReturnToLink>
      )}
    </div>
  );
}

const UPLOAD_STATUS_PILL: Record<CustomerUpload["status"], string> = {
  ready: "approved",
  pending_mapping: "reviewing",
  geocoding: "reviewing",
  failed: "rejected",
};

/** CSV upload card — real status/row-count once something's been uploaded
 *  for this project, the plain "Upload" link before that. */
function UploadCard({ uploads }: { uploads: CustomerUpload[] }) {
  const latest = uploads[0];
  return (
    <div className="wiz-int-card">
      <b>CSV upload</b>
      <span>Store revenue, rent, footfall</span>
      {latest ? (
        <>
          <Pill tone={UPLOAD_STATUS_PILL[latest.status]}>
            {latest.status === "ready" ? `Linked · ${latest.row_count} rows` : latest.status.replace("_", " ")}
          </Pill>
          <ReturnToLink className="btn secondary" to="/customer-data" fromLabel="project setup">Manage</ReturnToLink>
        </>
      ) : (
        <ReturnToLink className="btn secondary" to="/customer-data" fromLabel="project setup">Upload</ReturnToLink>
      )}
    </div>
  );
}

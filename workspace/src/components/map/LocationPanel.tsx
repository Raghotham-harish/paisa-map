import { useEffect, useState } from "react";
import { ApiError, api, DriverAnalysis, LocationScore, Project } from "../../lib/api";
import { MapSelection } from "../../lib/mapBridge";
import { prettySignal } from "../../lib/signalLabel";

const RISK_CLASS: Record<string, string> = {
  Low: "delta-pos",
  Medium: "reviewing",
  High: "rejected",
  Unknown: "shortlist",
};

const TIER_LABEL: Record<"core" | "edge" | "expansion", string> = {
  core: "Core",
  edge: "Edge",
  expansion: "Expansion",
};

function money(n: number | null | undefined) {
  return n == null ? "—" : `₹${Math.round(n).toLocaleString("en-IN")}`;
}

function Bench({ label, diff }: { label: string; diff: number | null | undefined }) {
  if (diff == null) return null;
  const cls = diff > 0.05 ? "pos" : diff < -0.05 ? "neg" : "flat";
  return (
    <div className="lp-bench">
      <span className="lp-bench-label">{label}</span>
      <span className={`lp-bench-val ${cls}`}>
        {diff > 0.05 ? "+" : ""}{Math.abs(diff) < 0.05 ? "0" : diff}%
      </span>
    </div>
  );
}

// The three primary actions (save / compare / forecast) live in the always-on
// MapActionBar now, not here — this panel is purely the (dismissible) reading
// surface for a location's intelligence.
export function LocationPanel({
  selection,
  project,
}: {
  selection: MapSelection | null;
  project: Project | null;
  inCompare?: boolean;
  onToggleCompare?: (pincode: string) => void;
  onSaved?: () => void;
}) {
  const [score, setScore] = useState<LocationScore | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dismissedFor, setDismissedFor] = useState<string | null>(null);
  const [projectDrivers, setProjectDrivers] = useState<DriverAnalysis | null>(null);

  const pincode = selection?.pincode ?? null;

  // Project-scoped, not per-location — one fetch per project selection, reused
  // across every pincode clicked while that project stays active.
  useEffect(() => {
    if (!project) {
      setProjectDrivers(null);
      return;
    }
    let cancelled = false;
    api.getDriverAnalysis(project.id).then((d) => {
      if (!cancelled) setProjectDrivers(d);
    }).catch(() => {
      if (!cancelled) setProjectDrivers(null);
    });
    return () => {
      cancelled = true;
    };
  }, [project]);

  useEffect(() => {
    if (!pincode) {
      setScore(null);
      return;
    }
    setLoading(true);
    setError(null);
    let cancelled = false;
    api
      .getLocationScore(pincode, {
        avg_ticket: project?.avg_ticket ?? null,
        target_segment: project?.target_segment ?? null,
        business_type: project?.business_type ?? project?.industry ?? null,
      }, { auto: selection?.auto })
      .then((s) => {
        if (!cancelled) setScore(s);
      })
      .catch((e) => {
        if (cancelled) return;
        setScore(null);
        setError(
          e instanceof ApiError && e.status === 404
            ? "This pincode isn't in the scored dataset yet."
            : "Couldn't load intelligence for this location.",
        );
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [pincode, project?.avg_ticket, project?.target_segment, project?.business_type, project?.industry]);

  if (!selection || dismissedFor === pincode) return null;

  const headline = score?.opportunity?.opportunity_score ?? score?.economic_score ?? null;
  const headlineLabel = score?.opportunity ? "opportunity" : "economic";

  const useProjectDrivers = !!(projectDrivers?.sufficient_data && projectDrivers.drivers.length > 0);
  const drivers: string[] = useProjectDrivers
    ? projectDrivers!.drivers.map((d) => d.label)
    : (score?.top_signals ?? []).slice(0, 4).map(prettySignal);

  return (
    <div className="card location-panel">
      <div className="lp-head">
        <span className="kicker">Location intelligence</span>
        <button type="button" aria-label="Dismiss" onClick={() => setDismissedFor(pincode)}>
          <svg width="14" height="14" viewBox="0 0 14 14" stroke="currentColor" strokeWidth="1.7">
            <path d="M3 3l8 8M11 3l-8 8" />
          </svg>
        </button>
      </div>

      <div className="lp-body">
        <div className="lp-title">
          <span className="lp-name">{selection.name || pincode}</span>
          {pincode && <span className="mono lp-pin">{pincode}</span>}
          {selection.tier && <span className={`pill mc-tier-${selection.tier}`}>{TIER_LABEL[selection.tier]}</span>}
        </div>

        {loading && <p className="loading" style={{ padding: "20px 0" }}>Loading…</p>}
        {error && <p style={{ color: "var(--flame)", fontSize: 12.5 }}>{error}</p>}

        {score && !loading && (
          <>
            <div className="lp-score">
              <span className="mono lp-score-num">{headline != null ? Math.round(headline) : "—"}</span>
              <span className="lp-score-unit">/100 {headlineLabel}</span>
            </div>

            <div className="lp-pills">
              {score.opportunity && <span className="pill approved">{score.opportunity.suitability}</span>}
              <span className={`pill ${RISK_CLASS[score.risk.level] ?? "shortlist"}`}>{score.risk.level} risk</span>
            </div>

            <div className="lp-benches">
              <Bench label="vs district" diff={score.benchmark.district?.diff_pct} />
              <Bench label="vs state" diff={score.benchmark.state?.diff_pct} />
              <Bench label="vs nearby" diff={score.benchmark.neighbours?.diff_pct} />
            </div>

            <div className="lp-money">
              <span>Income <b>{money(score.income)}</b>/mo</span>
              <span>Spend <b>{money(score.spend)}</b>/mo</span>
            </div>

            {drivers.length > 0 && (
              <div className="lp-drivers">
                {drivers.map((d) => (
                  <span className="lp-driver" key={d}>{d}</span>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

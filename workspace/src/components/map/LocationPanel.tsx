import { useEffect, useState } from "react";
import { ApiError, api, LocationScore, Project } from "../../lib/api";
import { MapSelection } from "../../lib/mapBridge";

const RISK_CLASS: Record<string, string> = {
  Low: "delta-pos",
  Medium: "reviewing",
  High: "rejected",
  Unknown: "shortlist",
};

function money(n: number | null | undefined) {
  return n == null ? "—" : `₹${Math.round(n).toLocaleString("en-IN")}`;
}

function Bench({ label, diff }: { label: string; diff: number | null | undefined }) {
  if (diff == null) return null;
  const pct = Math.max(6, Math.min(100, 50 + diff * 0.45)); // visual only
  return (
    <div className="lp-bench">
      <span className="lp-bench-label">{label}</span>
      <span className="lp-bench-track">
        <span className="lp-bench-fill" style={{ width: `${pct}%` }} />
      </span>
      <span className={`lp-bench-val ${diff >= 0 ? "pos" : "neg"}`}>
        {diff >= 0 ? "+" : ""}
        {diff}%
      </span>
    </div>
  );
}

export function LocationPanel({
  selection,
  project,
  inCompare,
  onToggleCompare,
  onSaved,
}: {
  selection: MapSelection | null;
  project: Project | null;
  inCompare: boolean;
  onToggleCompare: (pincode: string) => void;
  onSaved: () => void;
}) {
  const [score, setScore] = useState<LocationScore | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dismissedFor, setDismissedFor] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "exists" | "error">("idle");

  const pincode = selection?.pincode ?? null;

  useEffect(() => {
    setSaveState("idle");
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
      })
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
  const headlineLabel = score?.opportunity ? "opportunity" : "economic score";

  const onSave = async () => {
    if (!pincode) return;
    setSaveState("saving");
    try {
      const res = await api.createLocation({
        pincode,
        name: selection.name,
        lat: selection.lat,
        lng: selection.lng,
        project_id: project?.id,
      });
      setSaveState(res.created ? "saved" : "exists");
      onSaved();
    } catch (e) {
      setSaveState(e instanceof ApiError && e.status === 401 ? "error" : "error");
    }
  };

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
        </div>

        {loading && <p className="loading" style={{ padding: "24px 0" }}>Loading…</p>}
        {error && <p style={{ color: "var(--flame)", fontSize: 12.5 }}>{error}</p>}

        {score && !loading && (
          <>
            <div className="lp-score">
              <span className="mono lp-score-num">{headline != null ? Math.round(headline) : "—"}</span>
              <span className="lp-score-unit">/100 {headlineLabel}</span>
            </div>
            <div className="lp-sub">
              {score.benchmark.india?.n
                ? `Ranked against ${score.benchmark.india.n.toLocaleString("en-IN")} pincodes nationally`
                : "Modelled estimate"}
            </div>

            <div className="lp-benches">
              <Bench label={`vs ${score.benchmark.district?.label ?? "district"}`} diff={score.benchmark.district?.diff_pct} />
              <Bench label={`vs ${score.benchmark.state?.label ?? "state"}`} diff={score.benchmark.state?.diff_pct} />
              <Bench label="vs nearby" diff={score.benchmark.neighbours?.diff_pct} />
            </div>

            <div className="lp-pills">
              {score.opportunity && <span className="pill approved">{score.opportunity.suitability}</span>}
              <span className={`pill ${RISK_CLASS[score.risk.level] ?? "shortlist"}`}>{score.risk.level} risk</span>
            </div>

            {score.top_signals?.length > 0 && (
              <>
                <div className="kicker" style={{ marginTop: 14 }}>What drives the model</div>
                <div className="lp-drivers">
                  {score.top_signals.map((s) => (
                    <span className="lp-driver" key={s}>
                      {s}
                    </span>
                  ))}
                </div>
              </>
            )}

            <div className="lp-money">
              <span>Income {money(score.income)}/mo</span>
              <span>Spend {money(score.spend)}/mo</span>
            </div>

            <p className="lp-summary">{score.executive_summary}</p>
          </>
        )}
      </div>

      <div className="lp-actions">
        <button className="btn" onClick={onSave} disabled={!pincode || saveState === "saving"}>
          {saveState === "saving"
            ? "Saving…"
            : saveState === "saved"
              ? "Saved ✓"
              : saveState === "exists"
                ? "Already saved"
                : "Save location"}
        </button>
        <div className="lp-actions-row">
          <button className="btn secondary" disabled={!pincode} onClick={() => pincode && onToggleCompare(pincode)}>
            {inCompare ? "Remove from compare" : "Add to compare"}
          </button>
          <button className="btn secondary" disabled title="Forecasting arrives in the next release">
            Forecast
          </button>
        </div>
        {saveState === "error" && (
          <p style={{ color: "var(--flame)", fontSize: 12, margin: "6px 0 0" }}>
            Couldn't save — <a href="/workspace/">sign in</a> and try again.
          </p>
        )}
      </div>
    </div>
  );
}

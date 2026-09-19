import { useEffect, useState } from "react";
import { StatementPeriod, UsageStatement as Statement, api } from "../lib/api";

const PERIOD_LABEL: Record<StatementPeriod, string> = {
  this_month: "This month",
  last_month: "Last month",
  last_30d: "Last 30 days",
};
const REASON_LABEL: Record<string, string> = {
  forecast: "Forecast",
  report_generate: "Report",
  expansion_recommend: "Expansion recommendation",
};

/**
 * What was spent, by which company and which person, and on which kind of action —
 * never which project or location. For the paying company: every company it covers.
 * For a client's own owner/admin: just their company.
 */
export function UsageStatement({ orgId }: { orgId: number }) {
  const [period, setPeriod] = useState<StatementPeriod>("this_month");
  const [data, setData] = useState<Statement | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    setFailed(false);
    api.getUsageStatement(orgId, period).then((d) => alive && setData(d)).catch(() => alive && setFailed(true));
    return () => { alive = false; };
  }, [orgId, period]);

  if (failed) return null;
  return (
    <div className="card" style={{ marginBottom: 20 }} data-testid="usage-statement">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8, marginBottom: 6 }}>
        <p className="kicker" style={{ margin: 0 }}>Usage statement</p>
        <select value={period} onChange={(e) => setPeriod(e.target.value as StatementPeriod)} aria-label="Statement period" style={{ width: "auto" }}>
          {(Object.keys(PERIOD_LABEL) as StatementPeriod[]).map((p) => <option key={p} value={p}>{PERIOD_LABEL[p]}</option>)}
        </select>
      </div>
      <div style={{ fontSize: 12.5, color: "var(--ink-soft)", marginBottom: 8 }}>
        Credits spent by each company and person. It never shows which projects or locations they worked on.
      </div>
      {!data ? <div style={{ fontSize: 13 }}>Loading…</div> : (
        <>
          <div style={{ fontSize: 14, marginBottom: 8 }}><strong data-testid="statement-total">{data.total_credits_used}</strong> credits used in total</div>
          {data.companies.map((c) => (
            <div key={c.org_id} style={{ padding: "10px 0", borderTop: "1px solid var(--border)" }} data-testid={`statement-company-${c.org_id}`}>
              <div style={{ display: "flex", justifyContent: "space-between", fontWeight: 700, fontSize: 14 }}>
                <span>{c.name}</span><span>{c.credits_used}</span>
              </div>
              {c.people.length === 0 && <div style={{ fontSize: 12.5, color: "var(--ink-soft)" }}>No spending in this period.</div>}
              {c.people.map((p) => (
                <div key={p.user_id} style={{ display: "flex", justifyContent: "space-between", gap: 10, fontSize: 12.5, padding: "3px 0 0 12px", flexWrap: "wrap" }}>
                  <span>{p.name || p.email}
                    <span style={{ color: "var(--ink-soft)" }}> · {p.actions.map((a) => `${a.count}× ${REASON_LABEL[a.reason] ?? a.reason}`).join(", ")}</span>
                  </span>
                  <span>{p.credits_used}</span>
                </div>
              ))}
            </div>
          ))}
        </>
      )}
    </div>
  );
}

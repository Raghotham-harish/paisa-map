import { useEffect, useState } from "react";
import { MemberBudgetRow, MemberBudgets, api } from "../lib/api";
import { BudgetEditor } from "./BudgetEditor";
import { budgetWindowText } from "./BudgetMeter";

function MemberRow({ orgId, member, maxAmount, canEdit, onChanged }: {
  orgId: number; member: MemberBudgetRow; maxAmount?: number; canEdit: boolean; onChanged: () => void;
}) {
  const b = member.budget;
  return (
    <div style={{ padding: "12px 0", borderTop: "1px solid var(--border)" }} data-testid={`member-budget-row-${member.user_id}`}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", marginBottom: canEdit ? 8 : 0 }}>
        <div style={{ fontSize: 14 }}>
          <span style={{ fontWeight: 700 }}>{member.name || member.email}</span>
          <span style={{ color: "var(--ink-soft)", fontSize: 12.5 }}> · {member.role}{member.name ? ` · ${member.email}` : ""}</span>
        </div>
        <div style={{ fontSize: 12.5, color: "var(--ink-soft)" }}>
          {b && b.active
            ? <>{b.used} of {b.amount} used {budgetWindowText(b)} · <strong style={{ color: b.exhausted ? "var(--flame)" : b.warn ? "#8A6410" : "inherit" }}>{b.pct}%</strong></>
            : <>Spent {member.used_30d} in the last 30 days{b && !b.active ? " · allowance ended" : ""}</>}
        </div>
      </div>
      {canEdit && (
        <BudgetEditor
          key={`${b?.amount ?? "-"}:${b?.period ?? "-"}`}
          budget={b}
          maxAmount={maxAmount}
          ariaLabel={`Allowance for ${member.name || member.email}, in credits`}
          onSave={async (payload) => { await api.setMemberBudget(orgId, member.user_id, payload); onChanged(); }}
          onRemove={async () => { await api.deleteMemberBudget(orgId, member.user_id); onChanged(); }}
        />
      )}
    </div>
  );
}

/**
 * Personal allowances inside a company: an owner/admin of the company (a client's
 * own admin, say) caps what each colleague may spend in its name. Always inside the
 * company's own budget — the lower of the two binds. Hidden for a one-person company.
 */
export function MemberBudgetsManager({ orgId }: { orgId: number }) {
  const [data, setData] = useState<MemberBudgets | null>(null);
  const [failed, setFailed] = useState(false);

  const load = () => {
    api.listMemberBudgets(orgId).then((d) => { setData(d); setFailed(false); }).catch(() => setFailed(true));
  };
  useEffect(load, [orgId]);

  if (failed || !data || data.members.length < 2) return null;
  const parent = data.company_budget && data.company_budget.active ? data.company_budget : null;
  const canEdit = parent != null || data.can_set_without_company_budget;

  return (
    <div className="card" style={{ marginBottom: 20 }} data-testid="member-budgets-manager">
      <p className="kicker" style={{ marginBottom: 6 }}>Personal allowances</p>
      <div style={{ fontSize: 12.5, color: "var(--ink-soft)", marginBottom: 6 }}>
        Limit how many credits each person can spend for this company. An allowance sits inside the company's own budget
        {parent ? ` (${parent.amount} credits ${budgetWindowText(parent)})` : ""}, so the lower of the two always applies.
      </div>
      {!data.enforced && (
        <div style={{ fontSize: 12, background: "#FCF4E0", border: "1px solid #E6D3A0", borderRadius: 8, padding: "8px 10px", margin: "8px 0" }}>
          Shared wallets aren't switched on yet, so allowances you set now are saved but not enforced.
        </div>
      )}
      {!canEdit && (
        <div style={{ fontSize: 12.5, marginBottom: 8 }} data-testid="member-budgets-needs-budget">
          This company doesn't have a credit budget yet. Allowances work inside that budget, so ask whoever pays for
          this company to set one first.
        </div>
      )}
      {data.members.map((m) => (
        <MemberRow key={m.user_id} orgId={orgId} member={m} maxAmount={parent?.amount} canEdit={canEdit} onChanged={load} />
      ))}
    </div>
  );
}

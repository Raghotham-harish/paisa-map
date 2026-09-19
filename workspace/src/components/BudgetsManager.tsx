import { useEffect, useState } from "react";
import { ApiError, WalletBudgetCompany, WalletBudgets, api } from "../lib/api";
import { budgetWindowText } from "./BudgetMeter";
import { BudgetEditor } from "./BudgetEditor";

const errorText = (e: unknown, fallback: string) =>
  e instanceof ApiError && e.body?.error === "invalid_end_date" ? "Pick an end date in the future."
  : e instanceof ApiError && e.body?.error === "invalid_amount" ? "Enter a whole number of credits (0 pauses spending)."
  : fallback;

function BudgetRow({ walletOrgId, company, onChanged }: {
  walletOrgId: number; company: WalletBudgetCompany; onChanged: () => void;
}) {
  const b = company.budget;
  return (
    <div style={{ padding: "14px 0", borderTop: "1px solid var(--border)" }} data-testid={`budget-row-${company.org_id}`}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", marginBottom: 8 }}>
        <div style={{ fontWeight: 700, fontSize: 14 }}>
          {company.name}{company.is_wallet ? <span style={{ fontWeight: 500, color: "var(--ink-soft)" }}> · your company</span> : null}
        </div>
        <div style={{ fontSize: 12.5, color: "var(--ink-soft)" }}>
          {b && b.active
            ? <>{b.used} of {b.amount} used {budgetWindowText(b)} · <strong style={{ color: b.exhausted ? "var(--flame)" : b.warn ? "#8A6410" : "inherit" }}>{b.pct}%</strong></>
            : <>Spent {company.used_30d} in the last 30 days</>}
        </div>
      </div>
      {!b && (
        <div style={{ fontSize: 12.5, color: "var(--ink-soft)", marginBottom: 8 }}>
          {company.is_wallet
            ? "No budget — your own team can spend freely."
            : "No budget — this company's own staff can't spend credits until you set one."}
        </div>
      )}
      {b && !b.active && (
        <div style={{ fontSize: 12.5, color: "var(--flame)", marginBottom: 8 }}>
          This budget has ended. Set a new one to let this company's staff spend again.
        </div>
      )}
      <BudgetEditor
        key={`${b?.amount ?? "-"}:${b?.period ?? "-"}`}
        budget={b}
        ariaLabel={`Budget for ${company.name}, in credits`}
        onSave={async (payload) => { await api.setBudget(walletOrgId, company.org_id, payload); onChanged(); }}
        onRemove={async () => { await api.deleteBudget(walletOrgId, company.org_id); onChanged(); }}
      />
    </div>
  );
}

/**
 * Budgets for a paying company (owner/admin only). Shown only once the company
 * pays for others or already has a budget — a solo company has nothing to
 * apportion. Budgets are in credits; the server warns at 80% and hard-stops at 100%.
 */
export function BudgetsManager({ walletOrgId }: { walletOrgId: number }) {
  const [data, setData] = useState<WalletBudgets | null>(null);
  const [failed, setFailed] = useState(false);
  const [reserve, setReserve] = useState("");
  const [reserveBusy, setReserveBusy] = useState(false);
  const [reserveError, setReserveError] = useState<string | null>(null);

  const load = () => {
    api.listBudgets(walletOrgId)
      .then((d) => { setData(d); setReserve(d.reserve ? String(d.reserve) : ""); setFailed(false); })
      .catch(() => setFailed(true));
  };
  useEffect(load, [walletOrgId]);

  if (failed || !data) return null;
  const hasClients = data.companies.some((c) => !c.is_wallet);
  if (!hasClients && !data.companies.some((c) => c.budget) && !data.reserve) return null;

  const saveReserve = async () => {
    setReserveBusy(true);
    setReserveError(null);
    try {
      await api.setReserve(walletOrgId, reserve.trim() === "" ? null : Number(reserve));
      load();
    } catch (e) {
      setReserveError(errorText(e, "Couldn't save — try again."));
    } finally {
      setReserveBusy(false);
    }
  };
  const reserveValid = reserve.trim() === "" || (Number.isInteger(Number(reserve)) && Number(reserve) >= 0);

  return (
    <div className="card" style={{ marginBottom: 20 }} data-testid="budgets-manager">
      <p className="kicker" style={{ marginBottom: 6 }}>Credit budgets</p>
      <div style={{ fontSize: 12.5, color: "var(--ink-soft)", marginBottom: 6 }}>
        Cap what each company can spend from your credits. People get a warning at {data.warn_pct}% and spending stops
        at the limit — nothing is ever billed beyond it. You can raise a budget at any time.
      </div>
      {!data.enforced && (
        <div style={{ fontSize: 12, background: "#FCF4E0", border: "1px solid #E6D3A0", borderRadius: 8, padding: "8px 10px", margin: "8px 0" }}>
          Shared wallets aren't switched on yet, so budgets you set now are saved but not enforced.
        </div>
      )}
      {data.companies.map((c) => (
        <BudgetRow key={c.org_id} walletOrgId={walletOrgId} company={c} onChanged={load} />
      ))}
      <div style={{ padding: "14px 0 0", borderTop: "1px solid var(--border)" }}>
        <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 4 }}>Keep in reserve</div>
        <div style={{ fontSize: 12.5, color: "var(--ink-soft)", marginBottom: 8 }}>
          Credits held back for your own company. Spending on client companies can't take your balance below this
          (currently {data.balance} credits).
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <input
            type="number" min={0} step={1} value={reserve} placeholder="None"
            onChange={(e) => setReserve(e.target.value)} style={{ width: 110 }} aria-label="Reserve, in credits"
          />
          <button className="btn secondary" disabled={reserveBusy || !reserveValid} onClick={saveReserve}>Save reserve</button>
        </div>
        {reserveError && <div style={{ color: "var(--flame)", fontSize: 12, marginTop: 6 }}>{reserveError}</div>}
      </div>
    </div>
  );
}

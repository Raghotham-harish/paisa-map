import { useState } from "react";
import { ApiError, BudgetPeriod, BudgetStatus } from "../lib/api";
import { PERIOD_OPTIONS } from "./BudgetMeter";

const errorText = (e: unknown, maxAmount?: number) => {
  const code = e instanceof ApiError ? e.body?.error : null;
  if (code === "invalid_end_date") return "Pick an end date in the future.";
  if (code === "invalid_amount") return "Enter a whole number of credits (0 pauses spending).";
  if (code === "exceeds_company_budget") return `That's more than the company's own budget${maxAmount != null ? ` (${maxAmount} credits)` : ""}.`;
  if (code === "company_budget_required") return "The company needs a budget first — ask whoever pays for it to set one.";
  return "Couldn't save — try again.";
};

export interface BudgetPayload { amount: number; period: BudgetPeriod; ends_at?: string }

/** Amount + period (+ end date) controls with Set/Update/Remove — shared by company budgets and personal allowances. */
export function BudgetEditor({ budget, ariaLabel, onSave, onRemove, maxAmount }: {
  budget: BudgetStatus | null;
  ariaLabel: string;
  onSave: (payload: BudgetPayload) => Promise<void>;
  onRemove: () => Promise<void>;
  maxAmount?: number;
}) {
  const [amount, setAmount] = useState(budget ? String(budget.amount) : "");
  const [period, setPeriod] = useState<BudgetPeriod>(budget?.period ?? "billing_cycle");
  const [endsAt, setEndsAt] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(errorText(e, maxAmount));
    } finally {
      setBusy(false);
    }
  };

  const n = Number(amount);
  const valid = amount.trim() !== "" && Number.isInteger(n) && n >= 0
    && (period !== "until_date" || endsAt !== "")
    && (maxAmount == null || n <= maxAmount);
  const hint = PERIOD_OPTIONS.find((p) => p.id === period)?.hint;

  return (
    <>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <input
          type="number" min={0} max={maxAmount} step={1} value={amount} placeholder="Credits"
          onChange={(e) => setAmount(e.target.value)} style={{ width: 110 }} aria-label={ariaLabel}
        />
        <select value={period} onChange={(e) => setPeriod(e.target.value as BudgetPeriod)} aria-label="Budget period" style={{ width: "auto" }}>
          {PERIOD_OPTIONS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
        </select>
        {period === "until_date" && (
          <input type="date" value={endsAt} onChange={(e) => setEndsAt(e.target.value)} aria-label="Budget end date" style={{ width: "auto" }} />
        )}
        <button
          className="btn secondary" disabled={busy || !valid}
          onClick={() => run(() => onSave({ amount: n, period, ...(period === "until_date" ? { ends_at: endsAt } : {}) }))}
        >
          {budget ? "Update" : "Set"}
        </button>
        {budget && (
          <button className="btn secondary" disabled={busy} onClick={() => run(async () => { await onRemove(); setAmount(""); })}>
            Remove
          </button>
        )}
      </div>
      {hint && <div style={{ fontSize: 11.5, color: "var(--ink-soft)", marginTop: 6 }}>{hint}</div>}
      {maxAmount != null && n > maxAmount && (
        <div style={{ color: "var(--flame)", fontSize: 12, marginTop: 6 }}>
          At most {maxAmount} credits — the company's own budget.
        </div>
      )}
      {error && <div style={{ color: "var(--flame)", fontSize: 12, marginTop: 6 }}>{error}</div>}
    </>
  );
}

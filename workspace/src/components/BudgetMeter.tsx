import { BudgetPeriod, BudgetStatus } from "../lib/api";

export const PERIOD_OPTIONS: Array<{ id: BudgetPeriod; label: string; hint: string }> = [
  {
    id: "billing_cycle",
    label: "Billing cycle",
    hint: "Follows the paying company's billing month. Until subscriptions start, that is the calendar month.",
  },
  { id: "calendar_month", label: "Calendar month", hint: "Resets at midnight IST on the 1st." },
  { id: "weekly", label: "Weekly", hint: "Resets at midnight IST every Monday." },
  { id: "quarterly", label: "Calendar quarter", hint: "Resets on 1 Jan, 1 Apr, 1 Jul and 1 Oct (IST)." },
  { id: "one_off", label: "One-off total", hint: "A single allowance that never resets." },
  { id: "until_date", label: "Until a date", hint: "Counts from now until the end of the date you pick." },
];

const IST = "Asia/Kolkata";
const fmtDay = (d: Date) => d.toLocaleDateString("en-IN", { timeZone: IST, day: "numeric", month: "short" });

/** "this month" / "in total" / "until 30 Sep" — what the budget's window covers, in words. */
export function budgetWindowText(b: BudgetStatus): string {
  switch (b.resolved_period) {
    case "calendar_month": return "this month";
    case "weekly": return "this week";
    case "quarterly": return "this quarter";
    case "billing_cycle": return "this billing cycle";
    case "one_off": return "in total";
    case "until_date":
      // window_end is the instant the chosen day ENDS, so step back a minute to name that day.
      return b.window_end ? `until ${fmtDay(new Date(new Date(b.window_end).getTime() - 60_000))}` : "until a date";
  }
}

/** The company's own budget and how much of it is used. Warns at 80%, stops at 100% — the server enforces it. */
export function BudgetMeter({ budget, paidByName, personal }: { budget: BudgetStatus; paidByName?: string | null; personal?: boolean }) {
  if (!budget.active) return null;
  // A personal allowance is raised by the company's own admins; a company budget by whoever pays for it.
  const who = personal ? "an admin of this company" : (paidByName ?? "an admin of this company");
  const pct = Math.min(100, budget.pct);
  const tone = budget.exhausted ? "var(--flame)" : budget.warn ? "var(--amber)" : "var(--rupee)";
  const resets = budget.window_end && budget.resolved_period !== "until_date"
    ? ` It resets on ${fmtDay(new Date(budget.window_end))}.` : "";
  return (
    <div className="card" style={{ marginBottom: 20 }} data-testid={personal ? "member-budget-meter" : "budget-meter"}>
      <p className="kicker" style={{ marginBottom: 8 }}>{personal ? "Your credit allowance" : "Credit budget"} · {budgetWindowText(budget)}</p>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13, marginBottom: 6 }}>
        <span><strong>{budget.used}</strong> of {budget.amount} credits used{personal ? " by you" : ""}</span>
        <span style={{ color: tone, fontWeight: 700 }}>{budget.pct}%</span>
      </div>
      <div style={{ height: 8, borderRadius: 4, background: "var(--paper-3)", overflow: "hidden" }}>
        <div style={{ width: `${pct}%`, height: "100%", background: tone }} />
      </div>
      {budget.exhausted ? (
        <div style={{ color: "var(--flame)", fontSize: 12.5, marginTop: 10 }}>
          {personal ? "Your allowance is used up, so you can't spend credits here" : "This budget is used up, so credits can't be spent here"} until {who} raises it.{resets}
        </div>
      ) : budget.warn ? (
        <div style={{ fontSize: 12.5, marginTop: 10, color: "var(--ink-soft)" }}>
          {budget.remaining} credits left. Once it is used up, spending here stops until {who} raises it.{resets}
        </div>
      ) : null}
    </div>
  );
}

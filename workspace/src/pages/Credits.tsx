import { useEffect, useState } from "react";
import { api, CreditLedgerEntry } from "../lib/api";

const REASON_LABELS: Record<string, string> = {
  signup_bonus: "Signup bonus",
};

export default function Credits() {
  const [balance, setBalance] = useState<number | null>(null);
  const [ledger, setLedger] = useState<CreditLedgerEntry[] | null>(null);

  useEffect(() => {
    api.getCredits().then((data) => {
      setBalance(data.balance);
      setLedger(data.ledger);
    });
  }, []);

  return (
    <>
      <h1 className="page-title">Credits</h1>
      <p className="page-sub">Your balance and transaction history.</p>

      <div className="stat-row">
        <div className="stat-tile">
          <div className="label">Balance</div>
          <div className="value">{balance ?? "—"}</div>
        </div>
      </div>

      {ledger === null ? (
        <div className="loading">Loading…</div>
      ) : ledger.length === 0 ? (
        <div className="empty-state">No credit activity yet.</div>
      ) : (
        <ul className="list">
          {ledger.map((entry) => (
            <li key={entry.id}>
              <div>
                <div className="primary">{REASON_LABELS[entry.reason] || entry.reason}</div>
                <div className="secondary">{new Date(entry.created_at).toLocaleString()}</div>
              </div>
              <div className="row-actions">
                <span className={`pill ${entry.delta >= 0 ? "delta-pos" : "delta-neg"}`}>
                  {entry.delta >= 0 ? "+" : ""}
                  {entry.delta}
                </span>
                <span className="meta">balance: {entry.balance_after}</span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

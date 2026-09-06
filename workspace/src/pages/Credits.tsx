import { useEffect, useState } from "react";
import { api, CreditLedgerEntry, PricingConfig } from "../lib/api";
import { EmptyState } from "../components/EmptyState";
import { useAuth } from "../lib/auth";
import { openCheckout } from "../lib/razorpay";

const REASON_LABELS: Record<string, string> = {
  signup_bonus: "Signup bonus",
  credit_purchase: "Credit purchase",
  report_generate: "Report generated",
  expansion_recommend: "Expansion recommendation",
};

export default function Credits() {
  const { user, refresh } = useAuth();
  const [balance, setBalance] = useState<number | null>(null);
  const [ledger, setLedger] = useState<CreditLedgerEntry[] | null>(null);
  const [pricing, setPricing] = useState<PricingConfig | null>(null);
  const [buying, setBuying] = useState<string | null>(null);
  const [buyError, setBuyError] = useState<string | null>(null);

  const load = () =>
    api.getCredits().then((data) => {
      setBalance(data.balance);
      setLedger(data.ledger);
    });

  useEffect(() => {
    load();
    api.getPricing().then(setPricing);
  }, []);

  const onBuyPack = async (packId: string) => {
    setBuying(packId);
    setBuyError(null);
    try {
      const { razorpay_order_id, razorpay_key_id, amount_paise } = await api.createCreditOrder(packId);
      await openCheckout({
        key: razorpay_key_id,
        amount: amount_paise,
        currency: "INR",
        order_id: razorpay_order_id,
        description: pricing?.credit_packs[packId]?.label || "Credits",
        prefillEmail: user?.email,
        onSuccess: async (resp) => {
          await api.verifyPayment(resp);
          await refresh();
          await load();
        },
        onDismiss: () => setBuying(null),
      });
    } catch {
      setBuyError("Couldn't start checkout — try again.");
    } finally {
      setBuying(null);
    }
  };

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

      {pricing && (
        <div className="card" style={{ marginBottom: 24 }}>
          <p
            style={{
              margin: "0 0 14px", fontSize: 12.5, color: "var(--ink-soft)", fontFamily: "var(--mono)",
              letterSpacing: ".06em", textTransform: "uppercase",
            }}
          >
            Buy credits
          </p>
          {buyError && <div style={{ color: "var(--flame)", fontSize: 12, marginBottom: 10 }}>{buyError}</div>}
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            {Object.entries(pricing.credit_packs).map(([packId, pack]) => (
              <button
                key={packId}
                className="btn secondary"
                disabled={buying !== null}
                onClick={() => onBuyPack(packId)}
              >
                {buying === packId ? "Opening…" : `${pack.label} — ₹${(pack.price_paise / 100).toLocaleString("en-IN")}`}
              </button>
            ))}
          </div>
        </div>
      )}

      {ledger === null ? (
        <div className="loading">Loading…</div>
      ) : ledger.length === 0 ? (
        <EmptyState
          icon="💳"
          title="No credit activity yet"
          description="Credit awards and spend will show up here as you use paid features."
        />
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

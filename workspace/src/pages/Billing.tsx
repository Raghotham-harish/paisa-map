import { useEffect, useState } from "react";
import { api, CreditLedgerEntry, Invoice, PricingConfig } from "../lib/api";
import { EmptyState } from "../components/EmptyState";
import { useAuth } from "../lib/auth";
import { openCheckout } from "../lib/razorpay";
import { DataList, DataRow } from "../components/DataList";
import { AsyncBoundary } from "../components/AsyncBoundary";

const PLAN_ORDER: Array<"free" | "pro" | "team"> = ["free", "pro", "team"];

const REASON_LABELS: Record<string, string> = {
  signup_bonus: "Signup bonus",
  credit_purchase: "Credit purchase",
  report_generate: "Report generated",
  expansion_recommend: "Expansion recommendation",
};

/** "Billing" — plan, credits, and invoices together (N5: these used to be two separate nav items that were both "money"). */
export default function Billing() {
  const { user, refresh } = useAuth();
  const [pricing, setPricing] = useState<PricingConfig | null>(null);
  const [invoices, setInvoices] = useState<Invoice[] | null>(null);
  const [invoicesError, setInvoicesError] = useState<string | null>(null);
  const [upgrading, setUpgrading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [balance, setBalance] = useState<number | null>(null);
  const [ledger, setLedger] = useState<CreditLedgerEntry[] | null>(null);
  const [creditsError, setCreditsError] = useState<string | null>(null);
  const [buying, setBuying] = useState<string | null>(null);
  const [buyError, setBuyError] = useState<string | null>(null);

  const loadInvoices = () => {
    setInvoicesError(null);
    api.listInvoices().then((data) => setInvoices(data.invoices)).catch(() => setInvoicesError("Couldn't load your invoices — try again."));
  };

  const loadCredits = () => {
    setCreditsError(null);
    api.getCredits().then((data) => {
      setBalance(data.balance);
      setLedger(data.ledger);
    }).catch(() => setCreditsError("Couldn't load your credit history — try again."));
  };

  useEffect(() => {
    api.getPricing().then(setPricing);
    loadInvoices();
    loadCredits();
  }, []);

  const onUpgrade = async (plan: "pro" | "team") => {
    setUpgrading(plan);
    setError(null);
    try {
      const { razorpay_order_id, razorpay_key_id, amount_paise } = await api.createPlanOrder(plan);
      await openCheckout({
        key: razorpay_key_id,
        amount: amount_paise,
        currency: "INR",
        order_id: razorpay_order_id,
        description: `${pricing?.plan_prices[plan]?.label || plan} plan`,
        prefillEmail: user?.email,
        onSuccess: async (resp) => {
          await api.verifyPayment(resp);
          await refresh();
          loadInvoices();
        },
        onDismiss: () => setUpgrading(null),
      });
    } catch {
      setError("Couldn't start checkout — try again.");
    } finally {
      setUpgrading(null);
    }
  };

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
          loadCredits();
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
      <h1 className="page-title">Billing</h1>
      <p className="page-sub">Your plan, credits, and invoices.</p>

      {error && <div style={{ color: "var(--flame)", fontSize: 12, marginBottom: 10 }}>{error}</div>}

      <p className="kicker" style={{ marginBottom: 10 }}>Plan</p>
      {pricing && user && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 14, marginBottom: 28 }}>
          {PLAN_ORDER.map((plan) => {
            const isCurrent = user.plan === plan;
            const cfg = plan === "free" ? null : pricing.plan_prices[plan];
            return (
              <div key={plan} className="card" style={isCurrent ? { borderColor: "var(--rupee)" } : undefined}>
                <p style={{ margin: "0 0 4px", fontSize: 15, fontWeight: 700, textTransform: "capitalize" }}>{plan}</p>
                <p style={{ margin: "0 0 14px", fontSize: 20, fontWeight: 700 }}>
                  {cfg ? `₹${(cfg.price_paise / 100).toLocaleString("en-IN")}/mo` : "Free"}
                </p>
                {isCurrent ? (
                  <span className={`pill plan-${plan}`}>Current plan</span>
                ) : plan === "free" ? (
                  <span className="pill">—</span>
                ) : (
                  <button
                    className="btn secondary"
                    disabled={upgrading !== null}
                    onClick={() => onUpgrade(plan as "pro" | "team")}
                  >
                    {upgrading === plan ? "Opening…" : `Upgrade to ${cfg?.label}`}
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}

      <p className="kicker" style={{ marginBottom: 10 }}>Credits</p>
      <div className="stat-row" style={{ marginBottom: 14 }}>
        <div className="stat-tile">
          <div className="label">Balance</div>
          <div className="value">{balance ?? "—"}</div>
        </div>
      </div>
      {pricing && (
        <div className="card" style={{ marginBottom: 20 }}>
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
      <AsyncBoundary
        loading={ledger === null && !creditsError}
        error={creditsError}
        onRetry={loadCredits}
        empty={ledger?.length === 0}
        emptyState={
          <EmptyState
            icon="💳"
            title="No credit activity yet"
            description="Credit awards and spend will show up here as you use paid features."
            bare
          />
        }
      >
        <DataList>
          {(ledger ?? []).map((entry) => (
            <DataRow
              key={entry.id}
              title={REASON_LABELS[entry.reason] || entry.reason}
              subtitle={new Date(entry.created_at).toLocaleString()}
              trailing={
                <>
                  <span className={`pill ${entry.delta >= 0 ? "delta-pos" : "delta-neg"}`}>
                    {entry.delta >= 0 ? "+" : ""}
                    {entry.delta}
                  </span>
                  <span className="meta">balance: {entry.balance_after}</span>
                </>
              }
            />
          ))}
        </DataList>
      </AsyncBoundary>

      <p className="kicker" style={{ margin: "28px 0 10px" }}>Invoices</p>
      <AsyncBoundary
        loading={invoices === null && !invoicesError}
        error={invoicesError}
        onRetry={loadInvoices}
        empty={invoices?.length === 0}
        emptyState={
          <EmptyState
            icon="🧾"
            title="No invoices yet"
            description="Invoices for credit purchases, plan upgrades, and one-off report purchases will show up here."
            bare
          />
        }
      >
        <DataList>
          {(invoices ?? []).map((inv) => (
            <DataRow
              key={inv.id}
              title={inv.line_item_label}
              subtitle={`${inv.invoice_number} · ${new Date(inv.created_at).toLocaleDateString()}`}
              trailing={
                <>
                  <span className="meta">₹{(inv.total_amount_paise / 100).toLocaleString("en-IN")}</span>
                  <a className="btn secondary" href={api.invoiceDownloadUrl(inv.id)}>
                    Download PDF
                  </a>
                </>
              }
            />
          ))}
        </DataList>
      </AsyncBoundary>
    </>
  );
}

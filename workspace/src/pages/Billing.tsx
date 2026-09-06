import { useEffect, useState } from "react";
import { api, Invoice, PricingConfig } from "../lib/api";
import { EmptyState } from "../components/EmptyState";
import { useAuth } from "../lib/auth";
import { openCheckout } from "../lib/razorpay";

const PLAN_ORDER: Array<"free" | "pro" | "team"> = ["free", "pro", "team"];

export default function Billing() {
  const { user, refresh } = useAuth();
  const [pricing, setPricing] = useState<PricingConfig | null>(null);
  const [invoices, setInvoices] = useState<Invoice[] | null>(null);
  const [upgrading, setUpgrading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getPricing().then(setPricing);
    api.listInvoices().then((data) => setInvoices(data.invoices));
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
          api.listInvoices().then((data) => setInvoices(data.invoices));
        },
        onDismiss: () => setUpgrading(null),
      });
    } catch {
      setError("Couldn't start checkout — try again.");
    } finally {
      setUpgrading(null);
    }
  };

  return (
    <>
      <h1 className="page-title">Billing</h1>
      <p className="page-sub">Your plan and invoices.</p>

      {error && <div style={{ color: "var(--flame)", fontSize: 12, marginBottom: 10 }}>{error}</div>}

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

      {invoices === null ? (
        <div className="loading">Loading…</div>
      ) : invoices.length === 0 ? (
        <EmptyState
          icon="🧾"
          title="No invoices yet"
          description="Invoices for credit purchases, plan upgrades, and one-off report purchases will show up here."
        />
      ) : (
        <ul className="list">
          {invoices.map((inv) => (
            <li key={inv.id}>
              <div>
                <div className="primary">{inv.line_item_label}</div>
                <div className="secondary">
                  {inv.invoice_number} · {new Date(inv.created_at).toLocaleDateString()}
                </div>
              </div>
              <div className="row-actions">
                <span className="meta">₹{(inv.total_amount_paise / 100).toLocaleString("en-IN")}</span>
                <a className="btn secondary" href={api.invoiceDownloadUrl(inv.id)}>
                  Download PDF
                </a>
              </div>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

import { useState } from "react";
import { api, ApiError, BuyerDetails, formatListPrice, SubscriptionCheckout, SubscriptionsResponse } from "../lib/api";
import { openSubscriptionCheckout } from "../lib/razorpay";
import { BUYER_ERRORS } from "./BillingDetails";

// Statuses in which a subscription still holds (or is about to hold) the plan.
export const LIVE_STATUSES = ["authenticated", "active", "pending", "halted", "paused"];
const ENDED = ["cancelled", "completed", "expired"];

const ERRORS: Record<string, string> = {
  already_subscribed: "This company already has a subscription — refresh the page.",
  subscriptions_unavailable: "Monthly plans aren't open yet.",
  gateway_error: "Razorpay didn't respond — try again in a minute.",
  not_past_due: "There's no failed payment to fix any more — refresh the page.",
  no_subscription: "There's no subscription to cancel — refresh the page.",
  not_configured: "Payments aren't set up on this server.",
};

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
}

function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    return BUYER_ERRORS[e.message] || ERRORS[e.message] || e.body?.detail || "Something went wrong — try again.";
  }
  return "Something went wrong — try again.";
}

/** True when this panel, not the legacy Pro/Team grid, should own the "Plan" section. */
export function subscriptionPanelActive(data: SubscriptionsResponse | null): boolean {
  if (!data) return false;
  const sub = data.subscription;
  return data.state.enabled || !!data.assisted || !!data.standing || (!!sub && sub.status !== "created");
}

/** Billing v2 "Plan" section: pick Starter/Growth and subscribe, see the current plan and
 *  its next renewal, cancel, and fix a failed renewal. Everything is decided server-side;
 *  this only shows it and hands the customer to Razorpay Checkout. */
export function SubscriptionPanel({
  data,
  orgId,
  buyer,
  gstCharged,
  email,
  onChanged,
}: {
  data: SubscriptionsResponse;
  orgId: number | null;
  buyer: BuyerDetails;
  gstCharged: boolean;
  email?: string;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const sub = data.subscription;
  const live = !!sub && LIVE_STATUSES.includes(sub.status);
  const standing = data.standing;

  const checkout = async (key: string, label: string, start: () => Promise<SubscriptionCheckout>) => {
    setBusy(key);
    setError(null);
    setNote(null);
    try {
      const co = await start();
      await openSubscriptionCheckout({
        key: co.razorpay_key_id,
        subscription_id: co.razorpay_subscription_id,
        description: `${label} plan — monthly`,
        prefillEmail: email,
        onSuccess: async (resp) => {
          try {
            const r = await api.verifySubscription(resp);
            setNote(r.status === "active"
              ? `Done — you're on ${label}. Your monthly credits are in your balance.`
              : "Payment received — we're waiting for the bank to confirm it. Your plan switches on in a few minutes.");
          } catch {
            setError("Your payment went through but we couldn't confirm it yet. Refresh in a minute — you won't be charged twice.");
          } finally {
            setBusy(null);
            onChanged();
          }
        },
        onDismiss: () => {
          setBusy(null);
          onChanged();
        },
      });
    } catch (e) {
      setError(errorText(e));
      setBusy(null);
    }
  };

  const onCancel = async () => {
    if (!sub) return;
    const msg = sub.status === "active"
      ? `Cancel the ${sub.label} plan? You keep it until ${fmtDate(sub.current_end)} and won't be charged again. There's no refund for the current month.`
      : `Cancel the ${sub.label} plan? It hasn't been paid for yet, so it ends now.`;
    if (!window.confirm(msg)) return;
    setBusy("cancel");
    setError(null);
    setNote(null);
    try {
      await api.cancelSubscription(orgId);
      onChanged();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  const showOptions = !live && !data.assisted && data.state.enabled && data.options.length > 0;

  return (
    <div style={{ marginBottom: 28 }} data-testid="subscription-panel">
      {standing && (
        <div className="card" role="alert" data-testid="past-due-card"
             style={{ borderColor: "var(--flame)", background: "#FDF3F3", marginBottom: 14 }}>
          <p style={{ margin: "0 0 6px", fontWeight: 700, color: "var(--flame)" }}>
            <i className="ti ti-alert-triangle" aria-hidden="true" />{" "}
            {standing.locked ? "Your account is paused" : "Your last payment failed"}
          </p>
          <p style={{ margin: "0 0 12px", fontSize: 13 }}>
            {standing.locked
              ? "The monthly renewal is still unpaid, so paid features are paused for everyone on this plan. Your projects, saved locations and reports are safe — they come back the moment a payment goes through."
              : `We couldn't take this month's renewal. Update your payment method by ${fmtDate(standing.lock_at)} to keep using paid features without a break.`}
          </p>
          {live && (sub!.status === "pending" || sub!.status === "halted") && (
            <button className="btn" disabled={busy !== null} data-testid="recover-btn"
                    onClick={() => checkout("recover", sub!.label, () => api.recoverSubscription(orgId))}>
              {busy === "recover" ? "Opening…" : "Update payment method"}
            </button>
          )}
        </div>
      )}

      {data.assisted && (
        <div className="card" style={{ borderColor: "var(--rupee)", marginBottom: 14 }} data-testid="assisted-plan-card">
          <p style={{ margin: "0 0 4px", fontSize: 20, fontWeight: 700 }}>{data.assisted.label}</p>
          <span className="pill plan-pro">Current plan</span>
          <p style={{ margin: "12px 0 0", fontSize: 13, color: "var(--ink-soft)" }}>
            Set up with you directly by PaisaMap, until {fmtDate(data.assisted.period_end)}. Talk to us to renew or change it.
          </p>
        </div>
      )}

      {live && sub && (
        <div className="card" style={{ borderColor: "var(--rupee)", marginBottom: 14 }} data-testid="current-subscription">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
            <div>
              <p style={{ margin: "0 0 4px", fontSize: 20, fontWeight: 700 }}>{sub.label}</p>
              <p style={{ margin: 0, fontSize: 13, color: "var(--ink-soft)" }}>
                ₹{(sub.amount_paise / 100).toLocaleString("en-IN")} a month{gstCharged ? " incl. GST" : ""}
              </p>
            </div>
            <span className={`pill ${standing ? "rejected" : sub.cancel_at_period_end ? "reviewing" : "approved"}`}>
              {standing ? "Payment failed" : sub.cancel_at_period_end ? "Cancelling" : !sub.paid_count ? "Setting up" : "Active"}
            </span>
          </div>
          <p style={{ margin: "12px 0 0", fontSize: 13 }} data-testid="renewal-line">
            {standing
              ? "This month's renewal hasn't been paid yet — see above."
              : !sub.paid_count
              ? "We're waiting for the first payment to be confirmed. Your plan switches on as soon as it is."
              : sub.cancel_at_period_end
                ? `Cancelled — you keep ${sub.label} until ${fmtDate(sub.current_end)}. You won't be charged again.`
                : `Next renewal on ${fmtDate(sub.current_end)}.`}
          </p>
          {!sub.cancel_at_period_end && (
            <button className="btn secondary" style={{ marginTop: 14 }} disabled={busy !== null}
                    onClick={onCancel} data-testid="cancel-subscription">
              {busy === "cancel" ? "Cancelling…" : "Cancel plan"}
            </button>
          )}
        </div>
      )}

      {!live && sub && ENDED.includes(sub.status) && (
        <p style={{ fontSize: 13, color: "var(--ink-soft)", margin: "0 0 12px" }} data-testid="ended-subscription">
          Your {sub.label} subscription ended{sub.ends_at ? ` on ${fmtDate(sub.ends_at)}` : ""}.
        </p>
      )}

      {showOptions && (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 14 }}>
            {data.options.map((opt) => (
              <div key={opt.tier} className="card" data-testid={`plan-option-${opt.tier}`}>
                <p style={{ margin: "0 0 4px", fontSize: 15, fontWeight: 700 }}>{opt.label}</p>
                <p style={{ margin: "0 0 2px", fontSize: 20, fontWeight: 700 }}>
                  {formatListPrice(opt.list_paise, gstCharged)}<span style={{ fontSize: 13, fontWeight: 400 }}>/mo</span>
                </p>
                {gstCharged && (
                  <p style={{ margin: 0, fontSize: 12, color: "var(--ink-soft)" }}>
                    ₹{(opt.amount_paise / 100).toLocaleString("en-IN")} charged monthly, incl. GST
                  </p>
                )}
                <ul style={{ margin: "12px 0 14px", paddingLeft: 18, fontSize: 13 }}>
                  {opt.credits_per_month != null && (
                    <li>{opt.credits_per_month.toLocaleString("en-IN")} credits every month</li>
                  )}
                  {opt.seats != null && <li>Up to {opt.seats} people</li>}
                </ul>
                <button className="btn" disabled={busy !== null}
                        onClick={() => checkout(opt.tier, opt.label, () => api.createSubscription(opt.tier, orgId, buyer))}>
                  {busy === opt.tier ? "Opening…" : `Subscribe to ${opt.label}`}
                </button>
              </div>
            ))}
          </div>
          <p style={{ fontSize: 12, color: "var(--ink-soft)", margin: "10px 0 0" }}>
            Paid monthly by UPI Autopay, card or e-mandate. Cancel any time — you keep the plan until the end of the
            month you've paid for. Unused plan credits carry over for one extra month. Need Scale, Pro or annual
            billing? <a href="/contact">Talk to us</a>.
          </p>
        </>
      )}

      {note && <div style={{ color: "var(--rupee-deep)", fontSize: 13, marginTop: 10 }} data-testid="subscription-note">{note}</div>}
      {error && <div style={{ color: "var(--flame)", fontSize: 12, marginTop: 10 }} data-testid="subscription-error">{error}</div>}
    </div>
  );
}

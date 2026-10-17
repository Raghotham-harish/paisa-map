import { useState } from "react";
import { AddonKind, AddonView, api, ApiError, BuyerDetails, SubscriptionsResponse } from "../lib/api";
import { openSubscriptionCheckout } from "../lib/razorpay";
import { BUYER_ERRORS } from "./BillingDetails";
import { fmtDate } from "./SubscriptionPanel";

const rupees = (paise: number) => `₹${Math.round(paise / 100).toLocaleString("en-IN")}`;

const WHY_NOT: Record<string, string> = {
  no_paid_plan: "Extras can be added once your monthly plan's first payment has gone through.",
  plan_cancelling: "Your plan is set to end, so extras can't be added. Restart the plan to add more.",
  unpaid_balance: "Fix the failed plan payment first (see Plan above), then you can add extras.",
  assisted_tier: "On this plan, extra seats and companies are arranged with PaisaMap directly. Get in touch and we'll add them.",
};

const ERRORS: Record<string, string> = {
  gateway_error: "Razorpay didn't respond — try again in a minute.",
  not_configured: "Payments aren't set up on this server.",
  already_cancelling: "That one is already set to stop — refresh the page.",
  not_live: "That one has already ended — refresh the page.",
};

function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    const reason = e.body?.reason as string | undefined;
    return BUYER_ERRORS[e.message] || (reason && WHY_NOT[reason]) || ERRORS[e.message]
      || e.body?.detail || "Something went wrong — try again.";
  }
  return "Something went wrong — try again.";
}

const noun = (kind: AddonKind, n: number) =>
  kind === "seat" ? (n === 1 ? "seat" : "seats") : (n === 1 ? "company" : "companies");

/** P5 round 3: buy extra seats / companies on top of the plan. Each purchase is its own small
 *  monthly payment (Razorpay can't change the amount of a UPI Autopay mandate), renewing on its
 *  own date; stopping one keeps it until the end of the month already paid for. */
export function ExtrasPanel(props: {
  data: SubscriptionsResponse;
  orgId: number | null;
  buyer: BuyerDetails;
  gstCharged: boolean;
  email?: string;
  onChanged: () => void;
}) {
  const { data, orgId, buyer, gstCharged, email, onChanged } = props;
  const offer = data.addon_offer;
  const addons = data.addons ?? [];
  const lim = data.limits;
  const [qty, setQty] = useState<Record<AddonKind, number>>({ seat: 1, company: 1 });
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  // Hidden entirely while the feature is off or limits aren't enforced: there is nothing to buy.
  if (!offer || ["not_enabled", "limits_off", "schema_not_applied"].includes(offer.reason ?? "")) {
    if (addons.length === 0) return null;
  }
  const gst = gstCharged ? " + GST" : "";
  const kinds = (["seat", "company"] as AddonKind[]).filter((k) => offer?.prices[k] != null);

  const onBuy = async (kind: AddonKind) => {
    const n = qty[kind];
    setBusy(`buy-${kind}`);
    setError(null);
    setNote(null);
    try {
      const co = await api.createAddon(kind, n, orgId, buyer);
      await openSubscriptionCheckout({
        key: co.razorpay_key_id,
        subscription_id: co.razorpay_subscription_id,
        description: `${n} extra ${noun(kind, n)} — monthly`,
        prefillEmail: email,
        onSuccess: async (resp) => {
          try {
            const r = await api.verifySubscription(resp);
            setNote(r.status === "active"
              ? `Done — ${n} extra ${noun(kind, n)} added. You can invite people now.`
              : "Payment received — we're waiting for the bank to confirm it. The extras switch on in a few minutes.");
          } catch {
            setError("Your payment went through but we couldn't confirm it yet. Refresh in a minute — you won't be charged twice.");
          } finally {
            setBusy(null);
            onChanged();
          }
        },
        onDismiss: () => { setBusy(null); onChanged(); },
      });
    } catch (e) {
      setError(errorText(e));
      setBusy(null);
    }
  };

  const onStop = async (a: AddonView) => {
    const msg = a.paid
      ? `Stop the ${a.quantity} extra ${noun(a.kind, a.quantity)}? They stay until ${fmtDate(a.current_end)} and won't renew. Nobody is removed, but you won't be able to add anyone while you're over your limit.`
      : `Cancel the ${a.quantity} extra ${noun(a.kind, a.quantity)}? Nothing has been charged for them yet.`;
    if (!window.confirm(msg)) return;
    setBusy(`stop-${a.id}`);
    setError(null);
    try {
      await api.cancelAddon(a.id);
      onChanged();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="card" style={{ marginBottom: 28 }} data-testid="extras-panel">
      <p className="kicker" style={{ marginBottom: 8 }}>Extra seats and companies</p>
      {lim && (
        <p style={{ margin: "0 0 12px", fontSize: 13 }} data-testid="extras-usage">
          Seats: <strong>{lim.used_seats}</strong> of {lim.seats ?? "unlimited"} in use
          {lim.used_pending_invites > 0 ? ` (${lim.used_pending_invites} pending invite${lim.used_pending_invites === 1 ? "" : "s"})` : ""}
          {" · "}Companies: <strong>{lim.used_companies}</strong> of {lim.companies ?? "unlimited"}
        </p>
      )}
      {error && <div style={{ color: "var(--flame)", fontSize: 12, marginBottom: 10 }}>{error}</div>}
      {note && <div style={{ color: "var(--rupee)", fontSize: 12, marginBottom: 10 }}>{note}</div>}

      {addons.length > 0 && (
        <div style={{ marginBottom: 14 }}>
          {addons.map((a) => (
            <div key={a.id} style={{ display: "flex", alignItems: "center", gap: 10, padding: "6px 0", fontSize: 13, flexWrap: "wrap" }}
                 data-testid="extras-row">
              <span style={{ fontWeight: 600 }}>+{a.quantity} {noun(a.kind, a.quantity)}</span>
              <span style={{ color: "var(--ink-soft)" }}>{rupees(a.amount_paise)}/month</span>
              <span style={{ color: "var(--ink-soft)" }}>
                {!a.paid ? "waiting for the first payment"
                  : a.status === "halted" ? "payment failed — not counting"
                  : a.past_due ? "payment retrying"
                  : a.cancel_at_period_end || a.ends_at ? `stops ${fmtDate(a.ends_at || a.current_end)}`
                  : `renews ${fmtDate(a.current_end)}`}
              </span>
              {!a.cancel_at_period_end && !a.ends_at && (
                <button className="btn secondary" style={{ marginLeft: "auto" }} disabled={busy !== null}
                        onClick={() => onStop(a)}>
                  {busy === `stop-${a.id}` ? "Stopping…" : "Stop"}
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {offer && !offer.available && offer.reason && WHY_NOT[offer.reason] && (
        <p style={{ margin: 0, fontSize: 13, color: "var(--ink-soft)" }} data-testid="extras-why-not">{WHY_NOT[offer.reason]}</p>
      )}
      {offer?.available && kinds.map((kind) => (
        <div key={kind} style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 8, flexWrap: "wrap" }}>
          <label style={{ fontSize: 13 }}>
            Add{" "}
            <input type="number" min={1} max={offer.max_quantity} value={qty[kind]}
                   aria-label={`Number of extra ${noun(kind, 2)}`}
                   onChange={(e) => setQty({ ...qty, [kind]: Math.max(1, Math.min(offer.max_quantity, Number(e.target.value) || 1)) })}
                   style={{ width: 56 }} />
            {" "}{noun(kind, qty[kind])} at {rupees(offer.prices[kind]!)}/month each{gst}
          </label>
          <button className="btn secondary" disabled={busy !== null} onClick={() => onBuy(kind)}>
            {busy === `buy-${kind}` ? "Opening…" : `Add for ${rupees(offer.prices[kind]! * qty[kind])}/month${gst}`}
          </button>
        </div>
      ))}
      {offer?.available && (
        <p style={{ margin: "10px 0 0", fontSize: 12, color: "var(--ink-soft)" }}>
          Each purchase is its own monthly payment, starting today. Stop it any time; it stays until the end of the month you've paid for.
        </p>
      )}
    </div>
  );
}

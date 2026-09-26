import { BuyerDetails } from "../lib/api";

export const EMPTY_BUYER: BuyerDetails = { name: null, gstin: null, state_code: null, address: null };

// What the server's 400 codes mean, in words a buyer can act on.
export const BUYER_ERRORS: Record<string, string> = {
  invalid_gstin: "That GSTIN doesn't look right — check it for a typo.",
  gstin_state_mismatch: "The state you picked doesn't match the GSTIN (its first two digits are the state).",
  gstin_needs_name: "Add the business's legal name — the invoice is issued to the name registered with the GSTIN.",
  invalid_state: "Pick a state from the list.",
  invalid_billing_name: "The name is too long (200 characters at most).",
  invalid_billing_address: "The address is too long (500 characters at most).",
};

/** Optional "who is this invoice for" details. Sent with the next purchase and
 *  reused after that, so a business enters its GSTIN once. */
export function BillingDetails({
  value,
  onChange,
  states,
}: {
  value: BuyerDetails;
  onChange: (v: BuyerDetails) => void;
  states: Record<string, string>;
}) {
  const set = (key: keyof BuyerDetails, v: string) => {
    const next = { ...value, [key]: v === "" ? null : v };
    if (key === "gstin" && v.length >= 2 && states[v.slice(0, 2)]) next.state_code = v.slice(0, 2);
    onChange(next);
  };
  const gstinLocked = !!value.gstin && value.gstin.length >= 2 && !!states[value.gstin.slice(0, 2)];
  return (
    <details style={{ marginBottom: 14 }} data-testid="billing-details" open={!!(value.gstin || value.name)}>
      <summary style={{ cursor: "pointer", fontSize: 13, fontWeight: 600 }}>
        GST invoice details <span style={{ fontWeight: 400, color: "var(--ink-soft)" }}>(optional)</span>
      </summary>
      <p style={{ fontSize: 12, color: "var(--ink-soft)", margin: "8px 0 10px" }}>
        Add your GSTIN to claim input tax credit. Leave it blank if you're buying as an individual. We'll remember
        these for this company.
      </p>
      <div className="field-grid">
        <label>
          Legal name
          <input type="text" value={value.name ?? ""} maxLength={200} onChange={(e) => set("name", e.target.value)}
                 placeholder="As registered for GST" aria-label="Legal name" />
        </label>
        <label>
          GSTIN
          <input type="text" value={value.gstin ?? ""} maxLength={15} onChange={(e) => set("gstin", e.target.value.toUpperCase().trim())}
                 placeholder="29ABCDE1234F1Z5" aria-label="GSTIN" style={{ fontFamily: "monospace" }} />
        </label>
        <label>
          State
          <select value={value.state_code ?? ""} disabled={gstinLocked} onChange={(e) => set("state_code", e.target.value)}
                  aria-label="State">
            <option value="">Not given</option>
            {Object.entries(states).sort((a, b) => a[1].localeCompare(b[1])).map(([code, name]) => (
              <option key={code} value={code}>{name}</option>
            ))}
          </select>
        </label>
        <label style={{ gridColumn: "1 / -1" }}>
          Billing address
          <input type="text" value={value.address ?? ""} maxLength={500} onChange={(e) => set("address", e.target.value)}
                 aria-label="Billing address" />
        </label>
      </div>
    </details>
  );
}

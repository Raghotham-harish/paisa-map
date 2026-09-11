import { ReactNode } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";

/**
 * Wraps a cross-page CTA (forecast→store-data, wizard→connections) so the
 * destination can offer a way back — N2 in the dashboard audit: "no path
 * back to the forecast you were building". Captures the current location as
 * `return_to`/`return_label` on the way out; <ReturnBanner> reads them back
 * on the destination page.
 */
export function ReturnToLink({
  to, fromLabel, children, className,
}: { to: string; fromLabel: string; children: ReactNode; className?: string }) {
  const location = useLocation();
  const from = `${location.pathname}${location.search}`;
  const params = new URLSearchParams();
  params.set("return_to", from);
  params.set("return_label", fromLabel);
  const separator = to.includes("?") ? "&" : "?";
  return (
    <Link className={className} to={`${to}${separator}${params.toString()}`}>
      {children}
    </Link>
  );
}

/** "← Back to your forecast" — renders only when the page was reached via a <ReturnToLink>. */
export function ReturnBanner() {
  const [params] = useSearchParams();
  const to = params.get("return_to");
  const label = params.get("return_label");
  if (!to || !label) return null;
  return (
    <Link className="return-banner" to={to}>
      <i className="ti ti-arrow-left" aria-hidden="true" /> Back to {label}
    </Link>
  );
}

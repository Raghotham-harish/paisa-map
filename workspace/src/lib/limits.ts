import { ApiError } from "./api";

const rupees = (paise: number) => `₹${Math.round(paise / 100).toLocaleString("en-IN")}`;

/** P5: plain-language text for a 402 seat_limit / company_limit refusal, or
 *  null for any other error. Seats are counted across the whole paying
 *  account, and a pending invite holds one until it's accepted or cancelled. */
export function limitMessage(e: unknown): string | null {
  if (!(e instanceof ApiError)) return null;
  const b = e.body || {};
  const extra = typeof b.extra_paise === "number" ? ` or add one for ${rupees(b.extra_paise)}/month + GST` : "";
  if (b.error === "seat_limit") {
    const over = typeof b.would_use === "number" ? ` Linking it would bring that to ${b.would_use}.` : "";
    return `Your plan includes ${b.limit} seat${b.limit === 1 ? "" : "s"} across your account and ${b.used} are in use (pending invites hold a seat).${over} Remove someone, cancel an invite, move up a plan${extra}.`;
  }
  if (b.error === "company_limit") {
    return `Your plan includes ${b.limit} compan${b.limit === 1 ? "y" : "ies"} and you have ${b.used}. Move up a plan${extra}.`;
  }
  return null;
}

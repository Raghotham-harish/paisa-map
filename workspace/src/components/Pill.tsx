import { CSSProperties, ReactNode } from "react";

// Same 6-tone vocabulary `.pill` already uses everywhere (approved / rejected
// / reviewing / shortlist / delta-pos / delta-neg) — mapped once here instead
// of picking an icon at each of the ~25 call sites, since every call site
// already reduces its own status to one of these tones via a lookup table.
const TONE_ICON: Record<string, string> = {
  approved: "ti ti-check",
  rejected: "ti ti-x",
  reviewing: "ti ti-clock",
  shortlist: "ti ti-circle-dot",
  "delta-pos": "ti ti-trending-up",
  "delta-neg": "ti ti-trending-down",
};

/**
 * Status pill with a tone-matched icon, so status reads at a glance instead
 * of by background colour alone. `tone` outside the known vocabulary (e.g.
 * the plan-tier badges, which are identity not status) renders with no icon
 * unless one is passed explicitly.
 */
export function Pill({
  tone,
  children,
  icon,
  title,
  style,
}: {
  tone: string;
  children: ReactNode;
  /** Override the tone's default icon; pass `null` to force no icon. */
  icon?: string | null;
  title?: string;
  style?: CSSProperties;
}) {
  const resolved = icon === null ? undefined : icon ?? TONE_ICON[tone];
  return (
    <span className={`pill ${tone}`} title={title} style={style}>
      {resolved && <i className={resolved} />}
      {children}
    </span>
  );
}

import { ReactNode } from "react";

/** Small labelled fact — "12 locations", "forecast 3 days ago" — with an optional icon and tone. */
export function StatChip({
  icon,
  children,
  tone = "default",
}: {
  icon?: string;
  children: ReactNode;
  tone?: "default" | "pos" | "neg" | "warn";
}) {
  return (
    <span className={`stat-chip tone-${tone}`}>
      {icon && <i className={icon} />}
      {children}
    </span>
  );
}

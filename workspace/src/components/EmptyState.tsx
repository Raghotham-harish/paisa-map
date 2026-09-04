import { Link } from "react-router-dom";

export type EmptyStateAction = {
  label: string;
  /** In-SPA route (react-router Link). */
  to?: string;
  /** Cross-app link — full browser nav, e.g. back to the public map at "/". */
  href?: string;
  onClick?: () => void;
  variant?: "primary" | "secondary";
};

function ActionButton({ action }: { action: EmptyStateAction }) {
  const cls = `btn${action.variant === "secondary" ? " secondary" : ""}`;
  if (action.to) return <Link className={cls} to={action.to}>{action.label}</Link>;
  if (action.href) return <a className={cls} href={action.href}>{action.label}</a>;
  return <button className={cls} onClick={action.onClick}>{action.label}</button>;
}

/**
 * Shared empty-state widget: every list/data view in the workspace renders
 * this instead of a bare "nothing here" line. `dependency` is for the case
 * where the primary action can't be taken yet (e.g. no project exists to
 * attach a report to) — it explains what's missing and the action resolves it.
 */
export function EmptyState({
  icon,
  illustration,
  title,
  description,
  dependency,
  primaryAction,
  secondaryAction,
  bare,
}: {
  /** Emoji fallback — use only until a real illustration exists for this state. */
  icon?: string;
  /** Imported unDraw SVG (see DESIGN.md § Empty-state illustrations). Takes precedence over `icon`. */
  illustration?: string;
  title: string;
  description?: string;
  dependency?: string;
  primaryAction?: EmptyStateAction;
  secondaryAction?: EmptyStateAction;
  /** Skip the card box (background/border) when already nested inside a `.card`. */
  bare?: boolean;
}) {
  return (
    <div className={`empty-state${bare ? " bare" : ""}`}>
      {illustration ? (
        <img className="illustration" src={illustration} alt="" aria-hidden="true" />
      ) : icon ? (
        <div className="icon">{icon}</div>
      ) : null}
      <div className="title">{title}</div>
      {description && <p className="desc">{description}</p>}
      {dependency && <div className="dependency">{dependency}</div>}
      {(primaryAction || secondaryAction) && (
        <div className="actions">
          {primaryAction && <ActionButton action={{ variant: "primary", ...primaryAction }} />}
          {secondaryAction && <ActionButton action={{ variant: "secondary", ...secondaryAction }} />}
        </div>
      )}
    </div>
  );
}

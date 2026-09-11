import { ReactNode } from "react";

/**
 * Shared list/row primitives — replaces the old `.project-list` vs `.list`
 * split (they'd drifted into near-duplicate CSS). One row shape, fixed slots:
 * title / subtitle / meta chips / trailing (actions, dates, pills) / an
 * optional full-width footer (notes input, share link…) / an optional
 * expanded panel (inline edit) toggled by clicking the row.
 */

export function DataList({ children }: { children: ReactNode }) {
  return <ul className="data-list">{children}</ul>;
}

export function DataRow({
  leading,
  title,
  subtitle,
  chips,
  trailing,
  footer,
  expanded,
  onToggle,
}: {
  /** Fixed-size visual at the row's start — e.g. a per-project map thumbnail. */
  leading?: ReactNode;
  title: ReactNode;
  subtitle?: ReactNode;
  /** Small meta facts rendered as a chip row — e.g. business type, target segment. */
  chips?: ReactNode[];
  /** Right-aligned content on the main row — dates, pills, buttons. */
  trailing?: ReactNode;
  /** Full-width content below the main row, always visible (notes input, share link…). */
  footer?: ReactNode;
  /** Content revealed when the row is toggled open (inline edit panel). */
  expanded?: ReactNode;
  onToggle?: () => void;
}) {
  return (
    <li className="data-row">
      <div className={`data-row-top${onToggle ? " clickable" : ""}`} onClick={onToggle}>
        {leading && (
          <div className="data-row-leading" onClick={(e) => onToggle && e.stopPropagation()}>
            {leading}
          </div>
        )}
        <div className="data-row-main">
          <div className="data-row-title">{title}</div>
          {subtitle && <div className="data-row-subtitle">{subtitle}</div>}
          {chips && chips.length > 0 && (
            <div className="data-row-chips">
              {chips.map((c, i) => (
                <span className="data-row-chip" key={i}>{c}</span>
              ))}
            </div>
          )}
        </div>
        {trailing && (
          <div className="data-row-trailing" onClick={(e) => onToggle && e.stopPropagation()}>
            {trailing}
          </div>
        )}
      </div>
      {footer}
      {expanded && (
        <div className="data-row-expanded" onClick={(e) => e.stopPropagation()}>
          {expanded}
        </div>
      )}
    </li>
  );
}

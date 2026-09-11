import { ReactNode } from "react";

/**
 * "N selected → bulk actions" bar, in the same white/border/shadow language
 * as the map's own action bar and compare basket — but docked inline above
 * a list instead of floating over the canvas, and a rounded-rect (not full
 * pill) radius so it stays legible once content wraps to a second line on
 * narrow screens. Renders nothing while nothing is selected.
 */
export function ActionBar({
  count,
  itemLabel = "selected",
  onClear,
  children,
}: {
  count: number;
  itemLabel?: string;
  onClear: () => void;
  children: ReactNode;
}) {
  if (count === 0) return null;
  return (
    <div className="action-bar">
      <span className="action-bar-count">{count} {itemLabel}</span>
      <div className="action-bar-buttons">{children}</div>
      <button className="action-bar-clear" onClick={onClear} aria-label="Clear selection">
        &times;
      </button>
    </div>
  );
}

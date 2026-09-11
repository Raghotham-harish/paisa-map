/** Placeholder rows shown while a `<DataList>` loads — kills the layout jump when data lands. */
export function RowSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <ul className="data-list" aria-hidden="true">
      {Array.from({ length: rows }).map((_, i) => (
        <li className="data-row skeleton-row" key={i}>
          <div className="skeleton-line skeleton-line-title" />
          <div className="skeleton-line skeleton-line-subtitle" />
        </li>
      ))}
    </ul>
  );
}

/** Placeholder for a single `.card` block (stat tiles, chart cards) while it loads. */
export function CardSkeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="card skeleton-card" aria-hidden="true">
      {Array.from({ length: lines }).map((_, i) => (
        <div className="skeleton-line" key={i} style={{ width: i === lines - 1 ? "55%" : "85%" }} />
      ))}
    </div>
  );
}

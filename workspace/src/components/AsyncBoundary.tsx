import { ReactNode } from "react";
import { RowSkeleton } from "./Skeleton";

/**
 * Shared loading/error/empty wrapper for a data view. Three states resolve
 * in order — loading, error, empty — before falling through to children, so
 * a list's filters/header (rendered by the caller, outside this component)
 * never disappear along with its body (E1 in the dashboard audit).
 */
export function AsyncBoundary({
  loading,
  error,
  onRetry,
  empty,
  emptyState,
  skeleton,
  children,
}: {
  loading: boolean;
  error?: string | null;
  onRetry?: () => void;
  empty?: boolean;
  emptyState?: ReactNode;
  skeleton?: ReactNode;
  children: ReactNode;
}) {
  if (loading) return <>{skeleton ?? <RowSkeleton />}</>;
  if (error) {
    return (
      <div className="async-error">
        <span>{error}</span>
        {onRetry && (
          <button className="btn secondary" onClick={onRetry}>
            Try again
          </button>
        )}
      </div>
    );
  }
  if (empty && emptyState) return <>{emptyState}</>;
  return <>{children}</>;
}

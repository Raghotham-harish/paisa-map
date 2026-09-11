import { useEffect, useRef, useState } from "react";

interface Pending {
  id: number;
  label: string;
  commit: () => void;
}

/**
 * Shared "remove → 5s undo window → commit" pattern for destructive actions
 * (C2 in the dashboard audit: Projects/SavedLocations/etc fired a delete on
 * one click with no confirm and no undo). The row disappears from the list
 * immediately (via `isPending`, which the caller filters render on) and the
 * actual API call is deferred until the window lapses, so a misclick is
 * free to reverse. Starting a second delete commits any still-pending one
 * immediately, so at most one undo is ever in flight.
 */
export function usePendingDelete() {
  const [pending, setPending] = useState<Pending | null>(null);
  const pendingRef = useRef<Pending | null>(null);
  const timerRef = useRef<number | null>(null);

  useEffect(() => () => {
    if (timerRef.current) window.clearTimeout(timerRef.current);
  }, []);

  const clearTimer = () => {
    if (timerRef.current) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  };

  const remove = (id: number, label: string, commit: () => void) => {
    if (pendingRef.current) {
      clearTimer();
      pendingRef.current.commit();
    }
    const next: Pending = { id, label, commit };
    pendingRef.current = next;
    setPending(next);
    timerRef.current = window.setTimeout(() => {
      pendingRef.current = null;
      timerRef.current = null;
      setPending(null);
      commit();
    }, 5000);
  };

  const undo = () => {
    clearTimer();
    pendingRef.current = null;
    setPending(null);
  };

  const isPending = (id: number) => pending?.id === id;

  return { pending, remove, undo, isPending };
}

/** Fixed-position toast stack paired with usePendingDelete — "Project deleted — Undo". Stacks when more than one is active (independent id-spaces, e.g. a location delete and an upload delete both mid-window). */
export function UndoToastStack({ toasts }: { toasts: { key: string; label: string; onUndo: () => void }[] }) {
  if (toasts.length === 0) return null;
  return (
    <div className="undo-toast-stack">
      {toasts.map((t) => (
        <div className="undo-toast" role="status" key={t.key}>
          <span>{t.label}</span>
          <button className="btn secondary" onClick={t.onUndo}>Undo</button>
        </div>
      ))}
    </div>
  );
}

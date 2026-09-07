import { useEffect, useMemo, useRef, useState } from "react";

export interface Option {
  value: string;
  label: string;
  meta?: string;
}

function useOutsideClose(ref: React.RefObject<HTMLElement>, onClose: () => void) {
  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [ref, onClose]);
}

interface CommonProps {
  options: Option[];
  allowCustom?: boolean;
  placeholder?: string;
  disabled?: boolean;
}

/** Searchable multi-select. Type text not in the list and press Enter to add it
 *  as a custom value (when `allowCustom`). Selected values render as removable
 *  chips. */
export function MultiSelect({
  options,
  value,
  onChange,
  allowCustom = false,
  placeholder = "Search…",
  disabled = false,
  max,
  suggest,
}: CommonProps & {
  value: string[];
  onChange: (next: string[]) => void;
  max?: number;
  /** Optional "did you mean…" lookup, tried when the typed text matches no
   *  option by substring but allowCustom would otherwise let it through as
   *  free text (e.g. a catalog fuzzy-match for a custom signal name). */
  suggest?: (query: string) => Option | undefined;
}) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [highlight, setHighlight] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  useOutsideClose(rootRef, () => setOpen(false));

  const byValue = useMemo(() => new Map(options.map((o) => [o.value, o])), [options]);
  const labelFor = (v: string) => byValue.get(v)?.label ?? v;

  const q = query.trim().toLowerCase();
  const filtered = options.filter(
    (o) =>
      !value.includes(o.value) &&
      (!q || o.label.toLowerCase().includes(q) || o.value.toLowerCase().includes(q)),
  );
  const exactExists =
    options.some((o) => o.label.toLowerCase() === q) ||
    value.some((v) => labelFor(v).toLowerCase() === q);
  const showAdd = allowCustom && q.length > 0 && !exactExists;
  // Only offer a fuzzy suggestion when a literal substring search found
  // nothing — if the option is already visible in `filtered`, let the user
  // just click it rather than second-guessing an obvious match.
  const suggestion = showAdd && filtered.length === 0 ? suggest?.(q) : undefined;

  const rows: ({ kind: "opt"; opt: Option } | { kind: "suggest"; opt: Option } | { kind: "add" })[] = [
    ...filtered.map((opt) => ({ kind: "opt" as const, opt })),
    ...(suggestion ? [{ kind: "suggest" as const, opt: suggestion }] : []),
    ...(showAdd ? [{ kind: "add" as const }] : []),
  ];

  const atMax = max != null && value.length >= max;

  const add = (v: string) => {
    const t = v.trim();
    if (!t || value.includes(t) || atMax) return;
    onChange([...value, t]);
    setQuery("");
    setHighlight(0);
  };
  const remove = (v: string) => onChange(value.filter((x) => x !== v));

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setOpen(true);
      setHighlight((h) => Math.min(h + 1, Math.max(rows.length - 1, 0)));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) => Math.max(h - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const row = rows[highlight];
      if (row?.kind === "opt" || row?.kind === "suggest") add(row.opt.value);
      else if (showAdd) add(query);
    } else if (e.key === "Backspace" && !query && value.length) {
      remove(value[value.length - 1]);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

  return (
    <div className={`ms ${disabled ? "ms-disabled" : ""}`} ref={rootRef}>
      <div className="ms-control" onClick={() => !disabled && setOpen(true)}>
        {value.map((v) => (
          <span className="ms-chip" key={v}>
            {labelFor(v)}
            <button
              type="button"
              aria-label={`Remove ${labelFor(v)}`}
              onClick={(e) => {
                e.stopPropagation();
                remove(v);
              }}
            >
              <svg width="10" height="10" viewBox="0 0 10 10" stroke="currentColor" strokeWidth="1.6">
                <path d="M2 2l6 6M8 2l-6 6" />
              </svg>
            </button>
          </span>
        ))}
        <input
          className="ms-input"
          value={query}
          disabled={disabled}
          placeholder={value.length === 0 ? placeholder : atMax ? "" : "Add…"}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
            setHighlight(0);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
        />
      </div>
      {open && !disabled && (rows.length > 0 || query) && (
        <div className="ms-menu">
          {rows.length === 0 && <div className="ms-empty">No matches</div>}
          {rows.map((row, i) =>
            row.kind === "opt" ? (
              <div
                key={row.opt.value}
                className={`ms-row ${i === highlight ? "hl" : ""}`}
                onMouseEnter={() => setHighlight(i)}
                onMouseDown={(e) => {
                  e.preventDefault();
                  add(row.opt.value);
                }}
              >
                <span>{row.opt.label}</span>
                {row.opt.meta && <span className="ms-meta">{row.opt.meta}</span>}
              </div>
            ) : row.kind === "suggest" ? (
              <div
                key="__suggest"
                className={`ms-row ms-suggest ${i === highlight ? "hl" : ""}`}
                onMouseEnter={() => setHighlight(i)}
                onMouseDown={(e) => {
                  e.preventDefault();
                  add(row.opt.value);
                }}
              >
                <span>
                  Did you mean <b>&ldquo;{row.opt.label}&rdquo;</b>?
                </span>
                {row.opt.meta && <span className="ms-meta">{row.opt.meta}</span>}
              </div>
            ) : (
              <div
                key="__add"
                className={`ms-row ms-add ${i === highlight ? "hl" : ""}`}
                onMouseEnter={() => setHighlight(i)}
                onMouseDown={(e) => {
                  e.preventDefault();
                  add(query);
                }}
              >
                <span>
                  Add <b>&ldquo;{query.trim()}&rdquo;</b> as a custom signal
                </span>
                <span className="ms-meta">press &crarr;</span>
              </div>
            ),
          )}
        </div>
      )}
    </div>
  );
}

/** Single-select variant — same search + type-to-add affordance, one value. */
export function SingleSelect({
  options,
  value,
  onChange,
  allowCustom = false,
  placeholder = "Select…",
  disabled = false,
}: CommonProps & {
  value: string;
  onChange: (next: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [highlight, setHighlight] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  useOutsideClose(rootRef, () => {
    setOpen(false);
    setQuery("");
  });

  const byValue = useMemo(() => new Map(options.map((o) => [o.value, o])), [options]);
  const labelFor = (v: string) => byValue.get(v)?.label ?? v;

  const q = query.trim().toLowerCase();
  const filtered = options.filter(
    (o) => !q || o.label.toLowerCase().includes(q) || o.value.toLowerCase().includes(q),
  );
  const exactExists = options.some((o) => o.label.toLowerCase() === q);
  const showAdd = allowCustom && q.length > 0 && !exactExists;
  const rows: ({ kind: "opt"; opt: Option } | { kind: "add" })[] = [
    ...filtered.map((opt) => ({ kind: "opt" as const, opt })),
    ...(showAdd ? [{ kind: "add" as const }] : []),
  ];

  const pick = (v: string) => {
    onChange(v.trim());
    setQuery("");
    setOpen(false);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setOpen(true);
      setHighlight((h) => Math.min(h + 1, Math.max(rows.length - 1, 0)));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) => Math.max(h - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const row = rows[highlight];
      if (row?.kind === "opt") pick(row.opt.value);
      else if (showAdd) pick(query);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

  return (
    <div className={`ms ms-single ${disabled ? "ms-disabled" : ""}`} ref={rootRef}>
      <div className="ms-control" onClick={() => !disabled && setOpen((o) => !o)}>
        <input
          className="ms-input"
          value={open ? query : ""}
          disabled={disabled}
          placeholder={value ? labelFor(value) : placeholder}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
            setHighlight(0);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
        />
        <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.6">
          <path d="M2 4l4 4 4-4" />
        </svg>
      </div>
      {open && !disabled && (
        <div className="ms-menu">
          {rows.length === 0 && <div className="ms-empty">No matches</div>}
          {rows.map((row, i) =>
            row.kind === "opt" ? (
              <div
                key={row.opt.value}
                className={`ms-row ${i === highlight ? "hl" : ""} ${row.opt.value === value ? "sel" : ""}`}
                onMouseEnter={() => setHighlight(i)}
                onMouseDown={(e) => {
                  e.preventDefault();
                  pick(row.opt.value);
                }}
              >
                <span>{row.opt.label}</span>
                {row.opt.meta && <span className="ms-meta">{row.opt.meta}</span>}
              </div>
            ) : (
              <div
                key="__add"
                className={`ms-row ms-add ${i === highlight ? "hl" : ""}`}
                onMouseEnter={() => setHighlight(i)}
                onMouseDown={(e) => {
                  e.preventDefault();
                  pick(query);
                }}
              >
                <span>
                  Add <b>&ldquo;{query.trim()}&rdquo;</b>
                </span>
                <span className="ms-meta">press &crarr;</span>
              </div>
            ),
          )}
        </div>
      )}
    </div>
  );
}

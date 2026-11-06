// Searchable, alphabetised dropdown (addresses the "everything is an unsorted
// list" problem). One component backs every entity picker — recipe/item/machine
// selectors, the canvas "Add machine" palette, system membership — so long lists
// stay usable: type to filter, arrow keys to move, Enter to pick.
//
// The popup is rendered in a fixed-position layer anchored to the trigger, so it
// is never clipped by a panel's own `overflow: auto`.

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

export interface ComboOption {
  value: string;
  label: string;
  /** Optional secondary text (e.g. an id or category) shown dimmed. */
  hint?: string;
  /** Optional swatch color shown as a leading dot. */
  dot?: string;
}

export interface ComboboxProps {
  value: string;
  onChange: (value: string) => void;
  options: ComboOption[];
  placeholder?: string;
  /** Keep the given order instead of sorting labels A→Z. */
  unsorted?: boolean;
  disabled?: boolean;
  /** Compact trigger height (used in dense rows). */
  dense?: boolean;
}

function sortOptions(options: ComboOption[], unsorted?: boolean): ComboOption[] {
  if (unsorted) return options;
  return [...options].sort((a, b) =>
    a.label.localeCompare(b.label, undefined, { numeric: true, sensitivity: "base" }),
  );
}

export function Combobox({
  value,
  onChange,
  options,
  placeholder = "select…",
  unsorted,
  disabled,
  dense,
}: ComboboxProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const [rect, setRect] = useState<DOMRect | null>(null);

  const sorted = useMemo(() => sortOptions(options, unsorted), [options, unsorted]);
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return sorted;
    return sorted.filter(
      (o) =>
        o.label.toLowerCase().includes(q) ||
        (o.hint ?? "").toLowerCase().includes(q) ||
        o.value.toLowerCase().includes(q),
    );
  }, [sorted, query]);

  const selected = options.find((o) => o.value === value);

  const place = useCallback(() => {
    const el = rootRef.current;
    if (el) setRect(el.getBoundingClientRect());
  }, []);

  useLayoutEffect(() => {
    if (!open) return;
    place();
    const onScroll = () => place();
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onScroll);
    };
  }, [open, place]);

  useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
    setActive(0);
  }, [open]);

  // Close on outside click.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current?.contains(e.target as Node)) return;
      const pop = document.getElementById("combo-pop-layer");
      if (pop?.contains(e.target as Node)) return;
      setOpen(false);
      setQuery("");
    };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [open]);

  const pick = (v: string) => {
    onChange(v);
    setOpen(false);
    setQuery("");
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => Math.min(a + 1, filtered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(a - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const opt = filtered[active];
      if (opt) pick(opt.value);
    } else if (e.key === "Escape") {
      setOpen(false);
      setQuery("");
    }
  };

  return (
    <div className="combo" ref={rootRef}>
      <button
        type="button"
        className={`combo-trigger field-input mono ${dense ? "dense" : ""}`}
        disabled={disabled}
        onClick={() => !disabled && setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        {selected?.dot && (
          <span className="combo-dot" style={{ background: selected.dot }} />
        )}
        <span className={`combo-value ${selected ? "" : "placeholder"}`}>
          {selected ? selected.label : placeholder}
        </span>
        <span className="combo-caret" aria-hidden>
          ▾
        </span>
      </button>

      {open && rect &&
        createPortal(
        <div
          id="combo-pop-layer"
          className="combo-pop"
          style={{
            position: "fixed",
            top: rect.bottom + 4,
            left: rect.left,
            width: Math.max(rect.width, 240),
          }}
        >
          <input
            ref={inputRef}
            className="combo-search mono"
            value={query}
            placeholder="search…"
            onChange={(e) => {
              setQuery(e.target.value);
              setActive(0);
            }}
            onKeyDown={onKeyDown}
          />
          <div className="combo-list" role="listbox">
            {filtered.length === 0 && <div className="combo-empty mono">no matches</div>}
            {filtered.map((o, i) => (
              <button
                key={o.value}
                type="button"
                role="option"
                aria-selected={o.value === value}
                className={`combo-opt ${o.value === value ? "selected" : ""} ${
                  i === active ? "active" : ""
                }`}
                onMouseEnter={() => setActive(i)}
                onClick={() => pick(o.value)}
              >
                {o.dot && <span className="combo-dot" style={{ background: o.dot }} />}
                <span className="combo-opt-label">{o.label}</span>
                {o.hint && <span className="combo-opt-hint mono">{o.hint}</span>}
              </button>
            ))}
          </div>
        </div>,
          document.body,
        )}
    </div>
  );
}

// Inspector field primitives (WORKFLOW.md P7).
//
// The load-bearing rule here is sparse-field discipline: an *optional* field
// that is absent renders as "unset" with an affordance to add it — never as a
// populated `0`. Setting it writes the field; clearing it removes the key
// entirely (back to absent), so the "unset ≠ zero" contract the model enforces
// survives every round-trip through the editor.
//
// A genuinely *inapplicable* field (an extract priority on an `in`-only pipe
// end, efficiency on a non-fuel machine) is not rendered at all — that gating
// lives in the inspectors, which simply don't mount the row.

import { useEffect, useRef, useState, type ReactNode } from "react";

/** The label text plus an optional unit shown verbatim (not upper-cased). */
function LabelText({ label, unit }: { label: string; unit?: string }) {
  return (
    <>
      {label}
      {unit != null && <span className="field-unit">{unit}</span>}
    </>
  );
}

/** A labeled inspector row. `unit` renders next to the label without the
 * label's uppercase transform, so a unit like `mB/t` never becomes `MB/T`. */
export function Field({
  label,
  unit,
  hint,
  children,
}: {
  label: string;
  unit?: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="field">
      <span className="field-label mono">
        <LabelText label={label} unit={unit} />
      </span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  );
}

/** A non-editable value display (ids, references, derived facts). */
export function StaticValue({ value, dim }: { value: string; dim?: boolean }) {
  return <span className={`field-static mono ${dim ? "dim" : ""}`}>{value}</span>;
}

export function TextInput({
  value,
  onChange,
  placeholder,
  mono,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  mono?: boolean;
}) {
  // Locally buffered so the caret survives the async World round-trip: the input
  // shows `local` (updated synchronously with each keystroke), and only syncs
  // from `value` when it changes from the *outside* (undo, rename), not when the
  // host echoes back exactly what we just emitted. Fixes the caret-jump bug (#12).
  const [local, setLocal] = useState(value);
  const lastEmit = useRef(value);
  useEffect(() => {
    if (value !== lastEmit.current) {
      setLocal(value);
      lastEmit.current = value;
    }
  }, [value]);
  return (
    <input
      className={`field-input ${mono ? "mono" : ""}`}
      type="text"
      value={local}
      placeholder={placeholder}
      onChange={(e) => {
        const v = e.target.value;
        setLocal(v);
        lastEmit.current = v;
        onChange(v);
      }}
    />
  );
}

export function NumberInput({
  value,
  onChange,
  min,
  max,
  step = "any",
}: {
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number | "any";
}) {
  return (
    <input
      className="field-input mono"
      type="number"
      value={Number.isFinite(value) ? value : ""}
      min={min}
      max={max}
      step={step}
      onChange={(e) => {
        const n = e.target.valueAsNumber;
        if (!Number.isNaN(n)) onChange(n);
      }}
    />
  );
}

export interface Option {
  value: string;
  label: string;
}

export function Select({
  value,
  onChange,
  options,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  options: Option[];
  placeholder?: string;
}) {
  return (
    <select
      className="field-input mono"
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      {placeholder != null && <option value="">{placeholder}</option>}
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

export function Checkbox({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
}) {
  return (
    <label className="field-check">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span>{label}</span>
    </label>
  );
}

/**
 * An optional field: when present it shows the editor plus a "clear" control
 * that unsets it; when absent it shows just an "add" button that sets a default.
 */
export function Optional({
  label,
  unit,
  present,
  onAdd,
  onClear,
  hint,
  children,
}: {
  label: string;
  unit?: string;
  present: boolean;
  onAdd: () => void;
  onClear: () => void;
  hint?: string;
  children: ReactNode;
}) {
  if (!present) {
    return (
      <div className="field field-optional-empty">
        <span className="field-label mono">
          <LabelText label={label} unit={unit} />
        </span>
        <button type="button" className="unset-btn" onClick={onAdd}>
          <span className="unset-tag mono">unset</span>
          <span className="unset-add">+ set</span>
        </button>
        {hint && <span className="field-hint">{hint}</span>}
      </div>
    );
  }
  return (
    <div className="field">
      <span className="field-label mono">
        <LabelText label={label} unit={unit} />
        <button
          type="button"
          className="clear-btn"
          title={`Unset ${label}`}
          onClick={onClear}
        >
          ×
        </button>
      </span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </div>
  );
}

/** An optional numeric field — the common case, bundled. */
export function OptionalNumber({
  label,
  unit,
  value,
  onSet,
  onClear,
  fallback = 0,
  min,
  max,
  step,
  hint,
}: {
  label: string;
  unit?: string;
  value: number | undefined;
  onSet: (v: number) => void;
  onClear: () => void;
  fallback?: number;
  min?: number;
  max?: number;
  step?: number | "any";
  hint?: string;
}) {
  return (
    <Optional
      label={label}
      unit={unit}
      present={value != null}
      onAdd={() => onSet(fallback)}
      onClear={onClear}
      hint={hint}
    >
      <NumberInput
        value={value ?? fallback}
        onChange={onSet}
        min={min}
        max={max}
        step={step}
      />
    </Optional>
  );
}

// --- immutable field helpers --------------------------------------------- //

/** A shallow copy of `obj` with `key` set to `value`. */
export function withField<T extends object, K extends keyof T>(
  obj: T,
  key: K,
  value: T[K],
): T {
  return { ...obj, [key]: value };
}

/** A shallow copy of `obj` with `key` removed (back to absent). */
export function withoutField<T extends object, K extends keyof T>(
  obj: T,
  key: K,
): T {
  const next = { ...obj };
  delete next[key];
  return next;
}

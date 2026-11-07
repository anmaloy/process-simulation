// Recipe inspector (WORKFLOW.md P7). Input/output stacks over a production time.
// Energy is hybrid now (decision #2): a recipe may carry its own EU/tick draw,
// which overrides the machine's default. A stack's resource is a concrete item
// *or* a tag (a group like `c:gears/iron`), and may carry a probability
// (expected chance; blank = always). Item/tag pickers are searchable combos.

import { useEffect, useState } from "react";
import type { Recipe, Stack, World } from "../bridge";
import {
  Field,
  NumberInput,
  Optional,
  OptionalNumber,
  StaticValue,
  TextInput,
  withField,
  withoutField,
} from "./fields";
import { Combobox, EditableId, type ComboOption } from "../ui";
import { categoryColor } from "../canvas";

/** The flow resource a stack points at (a concrete item id, else its tag). */
function resourceOf(s: Stack): string {
  return s.itemId ?? s.tag ?? "";
}

function itemOptions(world: World): ComboOption[] {
  return (world.items ?? []).map((i) => ({
    value: i.id,
    // A tag reads with a leading # so it's distinguishable from a concrete item.
    label: `${i.isTag ? "# " : ""}${i.name ?? i.id}`,
    dot: categoryColor(i.category),
  }));
}

/** A compact, optional probability input: blank clears it (absent = always). */
function ProbabilityInput({
  value,
  onChange,
}: {
  value: number | undefined;
  onChange: (v: number | undefined) => void;
}) {
  const [text, setText] = useState(value != null ? String(value) : "");
  const [focused, setFocused] = useState(false);
  useEffect(() => {
    if (!focused) setText(value != null ? String(value) : "");
  }, [value, focused]);
  return (
    <input
      className="field-input mono stack-prob"
      type="text"
      inputMode="decimal"
      value={text}
      placeholder="p"
      title="probability (blank = always; 0 = catalyst)"
      onFocus={() => setFocused(true)}
      onChange={(e) => {
        const s = e.target.value;
        setText(s);
        if (s.trim() === "") {
          onChange(undefined);
          return;
        }
        const n = Number(s);
        if (Number.isFinite(n)) onChange(n);
      }}
      onBlur={(e) => {
        setFocused(false);
        const s = e.target.value.trim();
        if (s === "") {
          onChange(undefined);
          setText("");
          return;
        }
        const n = Number(s);
        if (Number.isFinite(n)) {
          onChange(n);
          setText(String(n));
        } else {
          setText(value != null ? String(value) : "");
        }
      }}
    />
  );
}

function StackList({
  label,
  stacks,
  world,
  onChange,
}: {
  label: string;
  stacks: Stack[] | undefined;
  world: World;
  onChange: (next: Stack[] | undefined) => void;
}) {
  const rows = stacks ?? [];
  const opts = itemOptions(world);
  const byId = new Map((world.items ?? []).map((i) => [i.id, i]));
  const firstItem = world.items?.[0];
  const unitOf = (resourceId: string) => {
    const cat = byId.get(resourceId)?.category;
    return cat === "liquid" ? "mB" : cat === "energy" ? "EU" : "×";
  };

  const setRow = (i: number, patch: Partial<Stack>) => {
    const next = rows.map((s, j) => (j === i ? { ...s, ...patch } : s));
    onChange(next);
  };
  // Picking a resource chooses the right field: a tag item sets `tag` (clearing
  // itemId) and vice versa, so exactly one is ever set.
  const pickResource = (i: number, resourceId: string) =>
    byId.get(resourceId)?.isTag
      ? setRow(i, { tag: resourceId, itemId: undefined })
      : setRow(i, { itemId: resourceId, tag: undefined });
  const addRow = () => {
    const first = firstItem;
    const seed: Stack = first?.isTag
      ? { tag: first.id, quantity: 1 }
      : { itemId: first?.id ?? "", quantity: 1 };
    onChange([...rows, seed]);
  };
  const removeRow = (i: number) => {
    const next = rows.filter((_, j) => j !== i);
    onChange(next.length ? next : undefined);
  };

  return (
    <div className="field stack-list">
      <span className="field-label mono">{label}</span>
      {rows.length === 0 && <span className="stack-empty mono">none</span>}
      {rows.map((s, i) => (
        <div className="stack-row" key={i}>
          <Combobox
            value={resourceOf(s)}
            onChange={(v) => pickResource(i, v)}
            options={opts}
            placeholder={opts.length ? "item / tag…" : "no items"}
            dense
          />
          <NumberInput
            value={s.quantity}
            onChange={(v) => setRow(i, { quantity: v })}
            min={0}
          />
          <span className="stack-unit mono">{unitOf(resourceOf(s))}</span>
          <ProbabilityInput
            value={s.probability}
            onChange={(v) => setRow(i, { probability: v })}
          />
          <button
            type="button"
            className="row-del"
            title="Remove"
            onClick={() => removeRow(i)}
          >
            ×
          </button>
        </div>
      ))}
      <button
        type="button"
        className="row-add"
        onClick={addRow}
        disabled={opts.length === 0}
      >
        + add stack
      </button>
    </div>
  );
}

export function RecipeInspector({
  recipe,
  world,
  onChange,
  onRename,
}: {
  recipe: Recipe;
  world: World;
  onChange: (next: Recipe) => void;
  onRename: (newId: string) => void;
}) {
  const setName = (v: string) =>
    onChange(
      v.trim() === ""
        ? withoutField(recipe, "name")
        : withField(recipe, "name", v),
    );

  const cycles = recipe.timeSeconds > 0 ? 1 / recipe.timeSeconds : 0;

  return (
    <div className="inspector-body">
      <Field label="id" hint="derived from the name; edit to rename everywhere">
        <EditableId
          id={recipe.id}
          name={recipe.name}
          kind="recipe"
          world={world}
          onRename={onRename}
        />
      </Field>
      <Field label="name">
        <TextInput value={recipe.name ?? ""} onChange={setName} placeholder="(unnamed)" />
      </Field>
      <Field label="time (s)" hint="production time per cycle">
        <NumberInput
          value={recipe.timeSeconds}
          onChange={(v) => onChange(withField(recipe, "timeSeconds", v))}
          min={0}
        />
      </Field>
      <Field label="rate">
        <StaticValue value={`${cycles ? cycles.toPrecision(3) : "0"} cycles/s`} dim />
      </Field>
      <OptionalNumber
        label="energy"
        unit="EU/t"
        value={recipe.euPerTick}
        onSet={(v) => onChange(withField(recipe, "euPerTick", v))}
        onClear={() => onChange(withoutField(recipe, "euPerTick"))}
        fallback={2}
        min={0}
        hint="EU/tick the machine draws for this recipe; overrides the machine default"
      />
      <Optional
        label="machine type"
        present={recipe.machineType != null}
        onAdd={() => onChange(withField(recipe, "machineType", ""))}
        onClear={() => onChange(withoutField(recipe, "machineType"))}
        hint="the machine kind that runs it (e.g. macerator)"
      >
        <TextInput
          value={recipe.machineType ?? ""}
          onChange={(v) => onChange(withField(recipe, "machineType", v))}
          placeholder="e.g. macerator"
        />
      </Optional>
      <StackList
        label="inputs"
        stacks={recipe.inputs}
        world={world}
        onChange={(v) =>
          onChange(v ? withField(recipe, "inputs", v) : withoutField(recipe, "inputs"))
        }
      />
      <StackList
        label="outputs"
        stacks={recipe.outputs}
        world={world}
        onChange={(v) =>
          onChange(v ? withField(recipe, "outputs", v) : withoutField(recipe, "outputs"))
        }
      />
    </div>
  );
}

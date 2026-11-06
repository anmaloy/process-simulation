// Recipe inspector (WORKFLOW.md P7). Input/output stacks over a production time.
// Recipes carry no energy term — energy is a machine property now (the machine
// draws a defined amount while running). Item pickers are searchable combos.

import type { Recipe, Stack, World } from "../bridge";
import { Field, NumberInput, StaticValue, TextInput, withField, withoutField } from "./fields";
import { Combobox, EditableId, type ComboOption } from "../ui";
import { categoryColor } from "../canvas";

function itemOptions(world: World): ComboOption[] {
  return (world.items ?? []).map((i) => ({
    value: i.id,
    label: i.name ?? i.id,
    hint: i.id,
    dot: categoryColor(i.category),
  }));
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
  const firstItem = world.items?.[0]?.id ?? "";

  const setRow = (i: number, patch: Partial<Stack>) => {
    const next = rows.map((s, j) => (j === i ? { ...s, ...patch } : s));
    onChange(next);
  };
  const addRow = () => onChange([...rows, { itemId: firstItem, quantity: 1 }]);
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
            value={s.itemId}
            onChange={(v) => setRow(i, { itemId: v })}
            options={opts}
            placeholder={opts.length ? "item…" : "no items"}
            dense
          />
          <NumberInput value={s.quantity} onChange={(v) => setRow(i, { quantity: v })} min={0} />
          <button type="button" className="row-del" title="Remove" onClick={() => removeRow(i)}>
            ×
          </button>
        </div>
      ))}
      <button type="button" className="row-add" onClick={addRow} disabled={opts.length === 0}>
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
    onChange(v.trim() === "" ? withoutField(recipe, "name") : withField(recipe, "name", v));

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

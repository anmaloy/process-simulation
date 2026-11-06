// Recipe inspector (WORKFLOW.md P7). Input/output stacks over a production
// time, with optional energy production/consumption. Stacks, name, and both
// energy terms are optional — absent stays absent (an empty stack list drops
// the key; an unset energy term shows as addable, never 0).

import type { Recipe, Stack, World } from "../bridge";
import {
  Field,
  NumberInput,
  OptionalNumber,
  Select,
  StaticValue,
  TextInput,
  withField,
  withoutField,
} from "./fields";

function itemOptions(world: World) {
  return (world.items ?? []).map((i) => ({ value: i.id, label: i.name ?? i.id }));
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
          <Select
            value={s.itemId}
            onChange={(v) => setRow(i, { itemId: v })}
            options={opts}
            placeholder={opts.length ? undefined : "no items"}
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
}: {
  recipe: Recipe;
  world: World;
  onChange: (next: Recipe) => void;
}) {
  const setName = (v: string) =>
    onChange(v.trim() === "" ? withoutField(recipe, "name") : withField(recipe, "name", v));

  return (
    <div className="inspector-body">
      <Field label="id">
        <StaticValue value={recipe.id} />
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
      <OptionalNumber
        label="energy consumption"
        value={recipe.energyConsumption}
        onSet={(v) => onChange(withField(recipe, "energyConsumption", v))}
        onClear={() => onChange(withoutField(recipe, "energyConsumption"))}
        min={0}
        hint="overrides the machine base when set"
      />
      <OptionalNumber
        label="energy production"
        value={recipe.energyProduction}
        onSet={(v) => onChange(withField(recipe, "energyProduction", v))}
        onClear={() => onChange(withoutField(recipe, "energyProduction"))}
        min={0}
        hint="overrides the machine base when set"
      />
    </div>
  );
}

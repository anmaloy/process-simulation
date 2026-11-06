// MachineDef inspector (WORKFLOW.md P7). A machine template: energy behavior,
// an optional fuel-burning mode, slot layout, allowed recipes, overclock bounds.
//
// Applicability gating (not mere optional-ness): a non-fuel def shows neither
// efficiency nor max fluid rate — those rows only mount when fuelBurning is on,
// and turning it off clears them. Everything else optional reads unset/addable.

import type { Category, Direction, MachineDef, SlotGroup, World } from "../bridge";
import {
  Checkbox,
  Field,
  NumberInput,
  OptionalNumber,
  Select,
  StaticValue,
  TextInput,
  withField,
  withoutField,
} from "./fields";

const CATEGORY_OPTIONS = [
  { value: "solid", label: "solid" },
  { value: "liquid", label: "liquid" },
  { value: "energy", label: "energy" },
];
const DIRECTION_OPTIONS = [
  { value: "in", label: "in" },
  { value: "out", label: "out" },
];

function SlotList({
  slots,
  onChange,
}: {
  slots: SlotGroup[] | undefined;
  onChange: (next: SlotGroup[]) => void;
}) {
  const rows = slots ?? [];
  const setRow = (i: number, patch: Partial<SlotGroup>) =>
    onChange(rows.map((s, j) => (j === i ? { ...s, ...patch } : s)));
  const addRow = () =>
    onChange([...rows, { category: "solid", direction: "in", slots: 1, stackSize: 64 }]);
  const removeRow = (i: number) => onChange(rows.filter((_, j) => j !== i));

  return (
    <div className="field stack-list">
      <span className="field-label mono">slots (category × direction)</span>
      {rows.length === 0 && <span className="stack-empty mono">none</span>}
      {rows.map((s, i) => (
        <div className="slot-row" key={i}>
          <Select
            value={s.category}
            onChange={(v) => setRow(i, { category: v as Category })}
            options={CATEGORY_OPTIONS}
          />
          <Select
            value={s.direction}
            onChange={(v) => setRow(i, { direction: v as Direction })}
            options={DIRECTION_OPTIONS}
          />
          <NumberInput value={s.slots} onChange={(v) => setRow(i, { slots: v })} min={0} />
          <span className="slot-x mono">×</span>
          <NumberInput value={s.stackSize} onChange={(v) => setRow(i, { stackSize: v })} min={0} />
          <button type="button" className="row-del" title="Remove" onClick={() => removeRow(i)}>
            ×
          </button>
        </div>
      ))}
      <button type="button" className="row-add" onClick={addRow}>
        + add slot group
      </button>
    </div>
  );
}

function AllowedRecipes({
  def,
  world,
  onChange,
}: {
  def: MachineDef;
  world: World;
  onChange: (next: MachineDef) => void;
}) {
  const recipes = world.recipes ?? [];
  const allowed = new Set(def.allowedRecipeIds ?? []);
  const toggle = (id: string, on: boolean) => {
    const next = new Set(allowed);
    if (on) next.add(id);
    else next.delete(id);
    const arr = [...next];
    onChange(
      arr.length ? withField(def, "allowedRecipeIds", arr) : withoutField(def, "allowedRecipeIds"),
    );
  };
  return (
    <div className="field">
      <span className="field-label mono">allowed recipes</span>
      {recipes.length === 0 && <span className="stack-empty mono">no recipes</span>}
      <div className="check-list">
        {recipes.map((r) => (
          <Checkbox
            key={r.id}
            checked={allowed.has(r.id)}
            onChange={(on) => toggle(r.id, on)}
            label={r.name ?? r.id}
          />
        ))}
      </div>
      <span className="field-hint">empty = every recipe allowed</span>
    </div>
  );
}

export function MachineDefInspector({
  def,
  world,
  onChange,
}: {
  def: MachineDef;
  world: World;
  onChange: (next: MachineDef) => void;
}) {
  const setName = (v: string) =>
    onChange(v.trim() === "" ? withoutField(def, "name") : withField(def, "name", v));

  const toggleFuel = (on: boolean) => {
    if (on) onChange(withField(def, "fuelBurning", true));
    else {
      // Non-fuel defs omit efficiency and max fluid rate — clear them too.
      let next = withoutField(def, "fuelBurning");
      next = withoutField(next, "efficiency");
      next = withoutField(next, "maxFluidRate");
      onChange(next);
    }
  };

  return (
    <div className="inspector-body">
      <Field label="id">
        <StaticValue value={def.id} />
      </Field>
      <Field label="name">
        <TextInput value={def.name ?? ""} onChange={setName} placeholder="(unnamed)" />
      </Field>

      <OptionalNumber
        label="base energy consumption"
        value={def.baseEnergyConsumption}
        onSet={(v) => onChange(withField(def, "baseEnergyConsumption", v))}
        onClear={() => onChange(withoutField(def, "baseEnergyConsumption"))}
        min={0}
      />
      <OptionalNumber
        label="base energy production"
        value={def.baseEnergyProduction}
        onSet={(v) => onChange(withField(def, "baseEnergyProduction", v))}
        onClear={() => onChange(withoutField(def, "baseEnergyProduction"))}
        min={0}
      />

      <Field label="fuel-burning">
        <Checkbox
          checked={def.fuelBurning === true}
          onChange={toggleFuel}
          label="converts fuel → output fluid"
        />
      </Field>
      {def.fuelBurning === true && (
        <>
          <OptionalNumber
            label="efficiency (waste w)"
            value={def.efficiency}
            onSet={(v) => onChange(withField(def, "efficiency", v))}
            onClear={() => onChange(withoutField(def, "efficiency"))}
            min={0}
            max={1}
            step={0.05}
            hint="0 = lossless (η=1); 0.8 = Large Steam Boiler"
          />
          <OptionalNumber
            label="max fluid rate (R)"
            value={def.maxFluidRate}
            onSet={(v) => onChange(withField(def, "maxFluidRate", v))}
            onClear={() => onChange(withoutField(def, "maxFluidRate"))}
            min={0}
          />
        </>
      )}

      <AllowedRecipes def={def} world={world} onChange={onChange} />
      <SlotList
        slots={def.slots}
        onChange={(v) => onChange(withField(def, "slots", v))}
      />

      <OptionalNumber
        label="overclock min"
        value={def.overclockMin}
        onSet={(v) => onChange(withField(def, "overclockMin", v))}
        onClear={() => onChange(withoutField(def, "overclockMin"))}
        min={0}
      />
      <OptionalNumber
        label="overclock max"
        value={def.overclockMax}
        onSet={(v) => onChange(withField(def, "overclockMax", v))}
        onClear={() => onChange(withoutField(def, "overclockMax"))}
        min={0}
      />
    </div>
  );
}

// MachineDef inspector (WORKFLOW.md P7), reworked for the carrier-tiers energy
// model (#5). A machine's "energy source" says how it is powered:
//
//   none     — passive (no power needed)
//   electric — draws base energy (EU/s) off the grid
//   liquid   — consumes a liquid carrier (e.g. steam)
//   fuel     — burns a fuel item (one with stored eu)
//
// A liquid/fuel machine is either a plain consumer (draws its source to run a
// recipe) or a converter (turns its source into an output carrier — fuel→steam,
// steam→electricity). Storage defs are buffers. Every energy field is cleared
// when it stops applying, so the shape stays sparse.

import type {
  Category,
  Direction,
  EnergySource,
  Item,
  MachineDef,
  SlotGroup,
  World,
} from "../bridge";
import {
  Checkbox,
  Field,
  NumberInput,
  OptionalNumber,
  Select,
  TextInput,
  withField,
  withoutField,
} from "./fields";
import { Combobox, EditableId, type ComboOption } from "../ui";
import { categoryColor } from "../canvas";

const CATEGORY_OPTIONS = [
  { value: "solid", label: "solid" },
  { value: "liquid", label: "liquid" },
  { value: "energy", label: "energy" },
];
const DIRECTION_OPTIONS = [
  { value: "in", label: "in" },
  { value: "out", label: "out" },
];
const SOURCE_OPTIONS = [
  { value: "", label: "none (passive)" },
  { value: "electric", label: "electric (grid)" },
  { value: "liquid", label: "liquid (e.g. steam)" },
  { value: "fuel", label: "fuel item" },
];

/** mB for liquids, EU for energy, items for solids. */
const UNIT: Record<Category, string> = { solid: "items", liquid: "mB", energy: "EU" };

function itemOpts(items: Item[]): ComboOption[] {
  return items.map((i) => ({
    value: i.id,
    label: i.name ?? i.id,
    hint: i.id,
    dot: categoryColor(i.category),
  }));
}

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
      <span className="field-label mono">slots (buffer per category × direction)</span>
      {rows.length === 0 && <span className="stack-empty mono">none</span>}
      {rows.map((s, i) => {
        const unit = UNIT[s.category];
        const total = (s.slots || 0) * (s.stackSize || 0);
        return (
          <div className="slot-group" key={i}>
            <div className="slot-row">
              <span
                className="slot-cat-dot"
                style={{ background: categoryColor(s.category) }}
              />
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
              <button
                type="button"
                className="row-del"
                title="Remove slot group"
                onClick={() => removeRow(i)}
              >
                ×
              </button>
            </div>
            <div className="slot-row slot-size-row">
              <NumberInput value={s.slots} onChange={(v) => setRow(i, { slots: v })} min={0} />
              <span className="slot-x mono">slots ×</span>
              <NumberInput
                value={s.stackSize}
                onChange={(v) => setRow(i, { stackSize: v })}
                min={0}
              />
              <span className="slot-unit mono">{unit}</span>
              <span className="slot-total mono">= {total} {unit}</span>
            </div>
          </div>
        );
      })}
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
  const allowed = def.allowedRecipeIds ?? [];
  const byId = new Map(recipes.map((r) => [r.id, r]));

  const set = (ids: string[]) =>
    onChange(
      ids.length
        ? withField(def, "allowedRecipeIds", ids)
        : withoutField(def, "allowedRecipeIds"),
    );
  const add = (id: string) => {
    if (id && !allowed.includes(id)) set([...allowed, id]);
  };
  const remove = (id: string) => set(allowed.filter((x) => x !== id));

  const addable: ComboOption[] = recipes
    .filter((r) => !allowed.includes(r.id))
    .map((r) => ({ value: r.id, label: r.name ?? r.id, hint: r.id }));

  return (
    <div className="field">
      <span className="field-label mono">allowed recipes</span>
      {recipes.length === 0 && <span className="stack-empty mono">no recipes</span>}
      {allowed.length > 0 && (
        <div className="chip-list">
          {allowed.map((id) => (
            <span key={id} className="chip mono">
              {byId.get(id)?.name ?? id}
              <button type="button" className="chip-x" title="Remove" onClick={() => remove(id)}>
                ×
              </button>
            </span>
          ))}
        </div>
      )}
      {addable.length > 0 && (
        <Combobox value="" onChange={add} options={addable} placeholder="+ allow a recipe…" dense />
      )}
      <span className="field-hint">empty = every recipe allowed</span>
    </div>
  );
}

export function MachineDefInspector({
  def,
  world,
  onChange,
  onRename,
}: {
  def: MachineDef;
  world: World;
  onChange: (next: MachineDef) => void;
  onRename: (newId: string) => void;
}) {
  const items = world.items ?? [];
  const setName = (v: string) =>
    onChange(v.trim() === "" ? withoutField(def, "name") : withField(def, "name", v));

  const source = def.energySource;
  const isConverter = def.outputItemId != null || def.maxFluidRate != null;

  const energyItems = items.filter((i) => i.category === "energy");
  // Items valid as a source for the current energy kind.
  const sourceItems =
    source === "fuel"
      ? items.filter((i) => i.eu != null)
      : source === "electric"
        ? energyItems
        : items.filter((i) => i.category === "liquid");
  // A converter's output is a liquid (steam) or energy (electricity) carrier.
  const outputItems = items.filter((i) => i.category === "liquid" || i.category === "energy");

  // Switch energy source, clearing every field the new source no longer uses.
  const setSource = (next: string) => {
    let d: MachineDef = { ...def };
    for (const k of [
      "energySource",
      "sourceItemId",
      "baseEnergyConsumption",
      "outputItemId",
      "efficiency",
      "maxFluidRate",
    ] as const) {
      d = withoutField(d, k);
    }
    if (next === "electric") {
      d = withField(d, "energySource", "electric");
      d = withField(d, "baseEnergyConsumption", 20);
      // Default the power carrier to the first energy item, so the draw routes
      // through electric connections (and is transfer-limited) out of the box.
      if (energyItems[0]) d = withField(d, "sourceItemId", energyItems[0].id);
    } else if (next === "liquid" || next === "fuel") {
      d = withField(d, "energySource", next as EnergySource);
      d = withField(d, "baseEnergyConsumption", 20);
    }
    onChange(d);
  };

  // Flip a liquid/fuel machine between consumer and converter.
  const setConverter = (on: boolean) => {
    let d: MachineDef = { ...def };
    if (on) {
      d = withoutField(d, "baseEnergyConsumption");
      d = withField(d, "maxFluidRate", 256);
      d = withField(d, "outputItemId", outputItems[0]?.id ?? "");
    } else {
      d = withoutField(d, "maxFluidRate");
      d = withoutField(d, "outputItemId");
      d = withoutField(d, "efficiency");
      d = withField(d, "baseEnergyConsumption", 20);
    }
    onChange(d);
  };

  const toggleStorage = (on: boolean) => {
    if (on)
      onChange(withField(def, "storage", { category: "solid" as Category, capacity: 1600 }));
    else onChange(withoutField(def, "storage"));
  };

  return (
    <div className="inspector-body">
      <Field label="id" hint="derived from the name; edit to rename everywhere">
        <EditableId
          id={def.id}
          name={def.name}
          kind="machineDef"
          world={world}
          onRename={onRename}
        />
      </Field>
      <Field label="name">
        <TextInput value={def.name ?? ""} onChange={setName} placeholder="(unnamed)" />
      </Field>

      <Field label="energy source" hint="how the machine is powered while it runs">
        <Select value={source ?? ""} onChange={setSource} options={SOURCE_OPTIONS} />
      </Field>

      {source === "electric" && (
        <>
          <Field label="power carrier" hint="routed over electric connections (transfer-limited)">
            <Combobox
              value={def.sourceItemId ?? ""}
              onChange={(v) => onChange(withField(def, "sourceItemId", v))}
              options={itemOpts(energyItems)}
              placeholder={energyItems.length ? "pick electricity…" : "create an energy item"}
            />
          </Field>
          <OptionalNumber
            label="energy draw (EU/s)"
            value={def.baseEnergyConsumption}
            onSet={(v) => onChange(withField(def, "baseEnergyConsumption", v))}
            onClear={() => onChange(withoutField(def, "baseEnergyConsumption"))}
            fallback={20}
            min={0}
            hint="drawn while running"
          />
        </>
      )}

      {(source === "liquid" || source === "fuel") && (
        <>
          <Field label={source === "fuel" ? "fuel item" : "source liquid"}>
            <Combobox
              value={def.sourceItemId ?? ""}
              onChange={(v) => onChange(withField(def, "sourceItemId", v))}
              options={itemOpts(sourceItems)}
              placeholder={sourceItems.length ? "pick a carrier…" : "no matching items"}
            />
          </Field>
          <Field label="converter">
            <Checkbox
              checked={isConverter}
              onChange={setConverter}
              label="turns its source into an output carrier"
            />
          </Field>
          {isConverter ? (
            <>
              <Field label="output carrier" hint="the liquid or electricity it produces">
                <Combobox
                  value={def.outputItemId ?? ""}
                  onChange={(v) => onChange(withField(def, "outputItemId", v))}
                  options={itemOpts(outputItems)}
                  placeholder={outputItems.length ? "pick output…" : "no liquid/energy items"}
                />
              </Field>
              <OptionalNumber
                label="max output rate (R)"
                value={def.maxFluidRate}
                onSet={(v) => onChange(withField(def, "maxFluidRate", v))}
                onClear={() => onChange(withoutField(def, "maxFluidRate"))}
                fallback={256}
                min={0}
                hint="peak output per second at full draw"
              />
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
            </>
          ) : (
            <OptionalNumber
              label="energy draw (EU/s)"
              value={def.baseEnergyConsumption}
              onSet={(v) => onChange(withField(def, "baseEnergyConsumption", v))}
              onClear={() => onChange(withoutField(def, "baseEnergyConsumption"))}
              fallback={20}
              min={0}
              hint="EU-equivalent of the source drawn while running"
            />
          )}
        </>
      )}

      <Field label="storage buffer">
        <Checkbox
          checked={def.storage != null}
          onChange={toggleStorage}
          label="this machine is a buffer (transparent passthrough)"
        />
      </Field>
      {def.storage != null && (
        <div className="storage-fields">
          <Field label="stores">
            <Select
              value={def.storage.category}
              onChange={(v) =>
                onChange(withField(def, "storage", { ...def.storage!, category: v as Category }))
              }
              options={CATEGORY_OPTIONS}
            />
          </Field>
          <Field label={`capacity (${UNIT[def.storage.category]})`}>
            <NumberInput
              value={def.storage.capacity}
              onChange={(v) =>
                onChange(withField(def, "storage", { ...def.storage!, capacity: v }))
              }
              min={0}
            />
          </Field>
        </div>
      )}

      <AllowedRecipes def={def} world={world} onChange={onChange} />
      <SlotList slots={def.slots} onChange={(v) => onChange(withField(def, "slots", v))} />

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

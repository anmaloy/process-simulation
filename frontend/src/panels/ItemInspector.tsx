// Item inspector (WORKFLOW.md P7). An item is a tracked resource: id + name +
// category, plus an *optional* stored-energy `eu` that marks it as a fuel.
// Items without `eu` are not fuels, so `eu` renders as unset/addable — never 0.

import type { Category, Item, World } from "../bridge";
import { Field, Optional, NumberInput, Select, TextInput, withField, withoutField } from "./fields";
import { EditableId } from "../ui";

const CATEGORY_OPTIONS = [
  { value: "solid", label: "solid" },
  { value: "liquid", label: "liquid" },
  { value: "energy", label: "energy" },
];

export function ItemInspector({
  item,
  world,
  onChange,
  onRename,
}: {
  item: Item;
  world: World;
  onChange: (next: Item) => void;
  onRename: (newId: string) => void;
}) {
  return (
    <div className="inspector-body">
      <Field label="id" hint="derived from the name; edit to rename everywhere">
        <EditableId id={item.id} name={item.name} kind="item" world={world} onRename={onRename} />
      </Field>
      <Field label="name">
        <TextInput value={item.name ?? ""} onChange={(v) => onChange(withField(item, "name", v))} />
      </Field>
      <Field label="category">
        <Select
          value={item.category}
          onChange={(v) => onChange(withField(item, "category", v as Category))}
          options={CATEGORY_OPTIONS}
        />
      </Field>
      <Optional
        label="fuel energy (eu)"
        present={item.eu != null}
        onAdd={() => onChange(withField(item, "eu", 1000))}
        onClear={() => onChange(withoutField(item, "eu"))}
        hint="stored EU per unit; set only for fuels"
      >
        <NumberInput
          value={item.eu ?? 0}
          onChange={(v) => onChange(withField(item, "eu", v))}
          min={0}
        />
      </Optional>
    </div>
  );
}

// MachineNode inspector (WORKFLOW.md P7). A placed machine: its def (fixed here),
// an optional bound recipe, overclock, and parallel count. All three are
// optional — unset reads as absent, not 0/1 — though the engine treats an absent
// overclock/count as the neutral 1. Position (x/y) is owned by the canvas and
// not editable here.

import type { MachineNode, World } from "../bridge";
import {
  Field,
  Optional,
  OptionalNumber,
  StaticValue,
  withField,
  withoutField,
} from "./fields";
import { Combobox } from "../ui";

export function NodeInspector({
  node,
  world,
  onChange,
}: {
  node: MachineNode;
  world: World;
  onChange: (next: MachineNode) => void;
}) {
  const def = world.machineDefs?.find((d) => d.id === node.machineDefId);
  // Recipe choices: the def's allow-list if it has one, else every recipe.
  const recipePool =
    def?.allowedRecipeIds && def.allowedRecipeIds.length > 0
      ? (world.recipes ?? []).filter((r) => def.allowedRecipeIds!.includes(r.id))
      : world.recipes ?? [];
  const recipeOptions = recipePool.map((r) => ({
    value: r.id,
    label: r.name ?? r.id,
  }));

  const bounds: string[] = [];
  if (def?.overclockMin != null) bounds.push(`min ${def.overclockMin}`);
  if (def?.overclockMax != null) bounds.push(`max ${def.overclockMax}`);

  return (
    <div className="inspector-body">
      <Field label="id">
        <StaticValue value={node.id} />
      </Field>
      <Field label="machine def">
        <StaticValue value={def?.name ?? node.machineDefId} />
      </Field>

      <Optional
        label="recipe"
        present={node.recipeId != null}
        onAdd={() => onChange(withField(node, "recipeId", recipePool[0]?.id ?? ""))}
        onClear={() => onChange(withoutField(node, "recipeId"))}
        hint={recipeOptions.length === 0 ? "no recipes available for this def" : undefined}
      >
        <Combobox
          value={node.recipeId ?? ""}
          onChange={(v) => onChange(withField(node, "recipeId", v))}
          options={recipeOptions}
          placeholder={recipeOptions.length ? "pick a recipe…" : "none"}
        />
      </Optional>

      <OptionalNumber
        label="overclock"
        value={node.overclock}
        onSet={(v) => onChange(withField(node, "overclock", v))}
        onClear={() => onChange(withoutField(node, "overclock"))}
        fallback={1}
        min={def?.overclockMin}
        max={def?.overclockMax}
        step={0.25}
        hint={bounds.length ? `bounds: ${bounds.join(", ")}` : "default 1"}
      />
      <OptionalNumber
        label="parallel count"
        value={node.count}
        onSet={(v) => onChange(withField(node, "count", v))}
        onClear={() => onChange(withoutField(node, "count"))}
        fallback={1}
        min={1}
        step={1}
        hint="default 1"
      />
    </div>
  );
}

// MachineNode inspector (WORKFLOW.md P7). A placed machine: its def (fixed here),
// a bound recipe, overclock, and parallel count.
//
// Recipe selection is **input/output-driven** (consolidation): a machine type
// like the macerator has many near-duplicate recipes ("Iron Dust" ×7) that only
// differ by their ingredient, so a flat dropdown is useless. Instead you pick an
// input and/or an output resource the machine's recipes actually use, and the
// matching recipe(s) appear — usually exactly one. Binding shows the resolved
// inputs → outputs so the auto-filled other side is visible. The options are the
// real resources of the def's allowed recipes (concrete items and tags alike).

import { useMemo, useState } from "react";
import type { MachineNode, Recipe, Stack, World } from "../bridge";
import {
  Checkbox,
  Field,
  OptionalNumber,
  StaticValue,
  withField,
  withoutField,
} from "./fields";
import { Combobox, type ComboOption } from "../ui";
import { categoryColor } from "../canvas";

/** The flow resource a stack points at (a concrete item id, else its tag). */
function resourceOf(s: Stack): string | undefined {
  return s.itemId ?? s.tag ?? undefined;
}

/** Membership-aware match: a chosen concrete item satisfies a tag that lists it. */
function satisfies(
  recipeResourceId: string,
  chosenId: string,
  itemsById: Map<string, { members?: string[] }>,
): boolean {
  if (recipeResourceId === chosenId) return true;
  const members = itemsById.get(recipeResourceId)?.members;
  return members ? members.includes(chosenId) : false;
}

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

  // Recipe pool: the def's allow-list if it has one, else every recipe.
  const pool = useMemo(() => {
    const allowed = def?.allowedRecipeIds;
    const all = world.recipes ?? [];
    return allowed && allowed.length > 0
      ? all.filter((r) => allowed.includes(r.id))
      : all;
  }, [def, world.recipes]);

  const itemsById = useMemo(
    () => new Map((world.items ?? []).map((i) => [i.id, i])),
    [world.items],
  );
  const resourceName = (id: string) => {
    const it = itemsById.get(id);
    if (it) return `${it.isTag ? "# " : ""}${it.name ?? it.id}`;
    // A tag with no backing item: show its last path segment.
    const seg = id.includes(":") ? id.split(":")[1] : id;
    return `# ${seg}`;
  };
  const resourceDot = (id: string) => {
    const cat = itemsById.get(id)?.category;
    return cat ? categoryColor(cat) : "var(--ink-faint)";
  };

  const bound = node.recipeId ? pool.find((r) => r.id === node.recipeId) : undefined;
  // If a recipe is bound, default to the resolved view; otherwise open the finder.
  const [changing, setChanging] = useState(false);

  const bounds: string[] = [];
  if (def?.overclockMin != null) bounds.push(`min ${def.overclockMin}`);
  if (def?.overclockMax != null) bounds.push(`max ${def.overclockMax}`);

  const pick = (id: string) => {
    onChange(withField(node, "recipeId", id));
    setChanging(false);
  };
  const clear = () => {
    onChange(withoutField(node, "recipeId"));
    setChanging(false);
  };

  return (
    <div className="inspector-body">
      <Field label="id">
        <StaticValue value={node.id} />
      </Field>
      <Field label="machine def">
        <StaticValue value={def?.name ?? node.machineDefId} />
      </Field>

      <div className="field recipe-pick">
        <span className="field-label mono">
          recipe
          {bound && !changing && (
            <button
              type="button"
              className="clear-btn"
              title="Change recipe"
              onClick={() => setChanging(true)}
            >
              change
            </button>
          )}
        </span>

        {bound && !changing ? (
          <RecipeSummary recipe={bound} resourceName={resourceName} resourceDot={resourceDot} />
        ) : pool.length === 0 ? (
          <span className="stack-empty mono">no recipes available for this machine</span>
        ) : (
          <RecipeFinder
            pool={pool}
            boundId={node.recipeId}
            itemsById={itemsById}
            resourceName={resourceName}
            resourceDot={resourceDot}
            onPick={pick}
          />
        )}

        {node.recipeId != null && (
          <button type="button" className="row-del recipe-unset" onClick={clear}>
            × unset recipe
          </button>
        )}
      </div>

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

      <div className="field">
        <span className="field-label mono">boundary</span>
        <Checkbox
          checked={node.assumeInputs ?? false}
          onChange={(v) =>
            onChange(v ? withField(node, "assumeInputs", true) : withoutField(node, "assumeInputs"))
          }
          label="manual insertion — assume inputs available"
        />
        <Checkbox
          checked={node.assumeOutputs ?? false}
          onChange={(v) =>
            onChange(v ? withField(node, "assumeOutputs", true) : withoutField(node, "assumeOutputs"))
          }
          label="manual extraction — assume outputs removed"
        />
        <span className="field-hint">
          a machine runs only when its inputs are supplied and its outputs are taken; a boundary
          machine is fed or emptied by hand
        </span>
      </div>
    </div>
  );
}

/** Read-only inputs → outputs for the bound recipe. */
function RecipeSummary({
  recipe,
  resourceName,
  resourceDot,
}: {
  recipe: Recipe;
  resourceName: (id: string) => string;
  resourceDot: (id: string) => string;
}) {
  const chips = (stacks: Stack[] | undefined) => {
    const rows = stacks ?? [];
    if (rows.length === 0) return <span className="stack-empty mono">none</span>;
    return rows.map((s, i) => {
      const id = resourceOf(s);
      if (!id) return null;
      return (
        <span className="recipe-chip mono" key={`${id}:${i}`}>
          <span className="dot" style={{ background: resourceDot(id) }} />
          {s.quantity}× {resourceName(id)}
          {s.probability != null && <span className="recipe-chip-p"> @{s.probability}</span>}
        </span>
      );
    });
  };
  return (
    <div className="recipe-summary">
      {recipe.name && <div className="recipe-summary-name mono">{recipe.name}</div>}
      <div className="recipe-io">
        <span className="recipe-io-label mono">in</span>
        <div className="recipe-chips">{chips(recipe.inputs)}</div>
      </div>
      <div className="recipe-io">
        <span className="recipe-io-label mono">out</span>
        <div className="recipe-chips">{chips(recipe.outputs)}</div>
      </div>
    </div>
  );
}

/** Input/output-driven recipe narrowing. */
function RecipeFinder({
  pool,
  boundId,
  itemsById,
  resourceName,
  resourceDot,
  onPick,
}: {
  pool: Recipe[];
  boundId: string | undefined;
  itemsById: Map<string, { members?: string[] }>;
  resourceName: (id: string) => string;
  resourceDot: (id: string) => string;
  onPick: (id: string) => void;
}) {
  const [input, setInput] = useState("");
  const [output, setOutput] = useState("");

  // Distinct input / output resources across the pool, as combo options. A tag
  // resource also contributes its member items, so you can reach a tag recipe by
  // naming a concrete item it accepts (e.g. "Iron Ore" → a c:ores/iron recipe).
  const options = (pickSide: "inputs" | "outputs"): ComboOption[] => {
    const ids = new Set<string>();
    for (const r of pool) {
      for (const s of r[pickSide] ?? []) {
        const id = resourceOf(s);
        if (!id) continue;
        ids.add(id);
        for (const m of itemsById.get(id)?.members ?? []) ids.add(m);
      }
    }
    return [...ids]
      .map((id) => ({ value: id, label: resourceName(id), dot: resourceDot(id) }))
      .sort((a, b) => a.label.localeCompare(b.label, undefined, { sensitivity: "base" }));
  };
  const inputOpts = useMemo(() => options("inputs"), [pool]); // eslint-disable-line react-hooks/exhaustive-deps
  const outputOpts = useMemo(() => options("outputs"), [pool]); // eslint-disable-line react-hooks/exhaustive-deps

  const has = (r: Recipe, side: "inputs" | "outputs", id: string) =>
    (r[side] ?? []).some((s) => {
      const rid = resourceOf(s);
      return rid != null && satisfies(rid, id, itemsById);
    });

  const matches = useMemo(() => {
    if (!input && !output) return [];
    return pool.filter(
      (r) => (!input || has(r, "inputs", input)) && (!output || has(r, "outputs", output)),
    );
  }, [pool, input, output]);

  return (
    <div className="recipe-finder">
      <div className="recipe-finder-row">
        <span className="recipe-io-label mono">from</span>
        <Combobox
          value={input}
          onChange={setInput}
          options={inputOpts}
          placeholder={inputOpts.length ? "an input…" : "no inputs"}
          dense
        />
        {input && (
          <button type="button" className="row-del" title="Clear input" onClick={() => setInput("")}>
            ×
          </button>
        )}
      </div>
      <div className="recipe-finder-row">
        <span className="recipe-io-label mono">make</span>
        <Combobox
          value={output}
          onChange={setOutput}
          options={outputOpts}
          placeholder={outputOpts.length ? "an output…" : "no outputs"}
          dense
        />
        {output && (
          <button type="button" className="row-del" title="Clear output" onClick={() => setOutput("")}>
            ×
          </button>
        )}
      </div>

      {!input && !output ? (
        <span className="field-hint">pick an input or output to find the recipe</span>
      ) : matches.length === 0 ? (
        <span className="stack-empty mono">no recipe matches</span>
      ) : (
        <div className="recipe-matches">
          <span className="field-hint">
            {matches.length === 1 ? "1 match — click to bind" : `${matches.length} matches`}
          </span>
          {matches.slice(0, 40).map((r) => (
            <button
              type="button"
              key={r.id}
              className={`recipe-match ${r.id === boundId ? "bound" : ""}`}
              onClick={() => onPick(r.id)}
            >
              <span className="recipe-match-io mono">
                {sideSummary(r.inputs, resourceName)} → {sideSummary(r.outputs, resourceName)}
              </span>
            </button>
          ))}
          {matches.length > 40 && (
            <span className="field-hint">+{matches.length - 40} more — narrow further</span>
          )}
        </div>
      )}
    </div>
  );
}

/** One-line "q× Name, …" summary of a stack list for a match row. */
function sideSummary(stacks: Stack[] | undefined, resourceName: (id: string) => string): string {
  const rows = stacks ?? [];
  if (rows.length === 0) return "∅";
  return rows
    .map((s) => {
      const id = resourceOf(s);
      return id ? `${s.quantity}× ${resourceName(id)}` : "?";
    })
    .join(", ");
}

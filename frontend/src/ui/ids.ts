// Dynamic ids derived from names (#3). An entity's id reflects its name
// (`"Steam Quarry"` → `steam_quarry`), set when created and re-synced on demand
// — and because other entities reference ids, a rename rewrites every reference
// so the World stays consistent.

import type { EntityKind, World } from "../bridge";

/** Slugify a display name into a stable id token. */
export function slugify(name: string): string {
  const slug = name
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .replace(/_+/g, "_");
  return slug || "item";
}

const KIND_FIELD: Record<EntityKind, keyof World> = {
  item: "items",
  recipe: "recipes",
  machineDef: "machineDefs",
  node: "nodes",
  pipe: "pipes",
  system: "systems",
};

/** All ids currently used by entities of `kind`. */
export function idsOfKind(world: World, kind: EntityKind): Set<string> {
  const list = (world[KIND_FIELD[kind]] ?? []) as { id: string }[];
  return new Set(list.map((e) => e.id));
}

/** `base`, or `base_2`, `base_3`… — the first not already taken. */
export function uniqueId(base: string, taken: Set<string>): string {
  if (!taken.has(base)) return base;
  for (let i = 2; ; i++) {
    const candidate = `${base}_${i}`;
    if (!taken.has(candidate)) return candidate;
  }
}

/** A fresh id derived from a name, unique among `kind`'s existing ids. */
export function idFromName(world: World, kind: EntityKind, name: string): string {
  const taken = idsOfKind(world, kind);
  return uniqueId(slugify(name), taken);
}

/**
 * Rename an entity's id across the whole World, rewriting every reference to it.
 * Only the library kinds (item / recipe / machineDef) are renamed this way —
 * nodes, pipes and systems keep their generated ids. Returns a new World.
 */
export function renameEntity(
  world: World,
  kind: EntityKind,
  oldId: string,
  newId: string,
): World {
  if (oldId === newId) return world;
  const w: World = structuredClone(world);

  const rename = <T extends { id: string }>(list: T[] | undefined) =>
    (list ?? []).map((e) => (e.id === oldId ? { ...e, id: newId } : e));

  if (kind === "item") {
    w.items = rename(w.items);
    for (const r of w.recipes ?? []) {
      r.inputs = r.inputs?.map((s) => (s.itemId === oldId ? { ...s, itemId: newId } : s));
      r.outputs = r.outputs?.map((s) =>
        s.itemId === oldId ? { ...s, itemId: newId } : s,
      );
    }
    for (const d of w.machineDefs ?? []) {
      if (d.sourceItemId === oldId) d.sourceItemId = newId;
      if (d.outputItemId === oldId) d.outputItemId = newId;
    }
  } else if (kind === "recipe") {
    w.recipes = rename(w.recipes);
    for (const d of w.machineDefs ?? []) {
      if (d.allowedRecipeIds) {
        d.allowedRecipeIds = d.allowedRecipeIds.map((id) => (id === oldId ? newId : id));
      }
    }
    for (const n of w.nodes ?? []) if (n.recipeId === oldId) n.recipeId = newId;
  } else if (kind === "machineDef") {
    w.machineDefs = rename(w.machineDefs);
    for (const n of w.nodes ?? []) if (n.machineDefId === oldId) n.machineDefId = newId;
  }
  return w;
}

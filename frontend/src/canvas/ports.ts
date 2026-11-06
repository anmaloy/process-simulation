// Port derivation — a machine node's connectable ports come from its
// MachineDef slot layout, one port per (category × direction) group
// (WORKFLOW.md P6). `in` slots accept flow (React Flow *target* handles, drawn
// on the left); `out` slots emit it (*source* handles, on the right).
//
// The engine matches pipe flow by category, not by port name (it never reads
// the `port` field), so the port id here is purely a UI/addressing label. We
// encode it as `${category}:${direction}` — stable, human-readable, and enough
// to round-trip a pipe end's `port` through the World.

import type { Category, Direction, MachineDef } from "../bridge";

export interface Port {
  /** Stable handle id, e.g. "solid:in". */
  id: string;
  category: Category;
  direction: Direction;
  /** Buffer capacity this port group holds: slots × stackSize. */
  capacity: number;
}

export function portId(category: Category, direction: Direction): string {
  return `${category}:${direction}`;
}

/** Parse a `${category}:${direction}` handle id back into its parts. */
export function parsePortId(
  id: string | null | undefined,
): { category: Category; direction: Direction } | null {
  if (!id) return null;
  const [category, direction] = id.split(":");
  if (
    (category === "solid" || category === "liquid" || category === "energy") &&
    (direction === "in" || direction === "out")
  ) {
    return { category, direction };
  }
  return null;
}

/**
 * Ports a def exposes, in a stable render order (inputs first, then outputs;
 * each grouped by category). Collapses duplicate (category, direction) slot
 * groups into one port, summing their capacity.
 */
export function portsForDef(def: MachineDef | undefined): Port[] {
  if (!def?.slots?.length) return [];
  const merged = new Map<string, Port>();
  for (const g of def.slots) {
    const id = portId(g.category, g.direction);
    const capacity = (g.slots ?? 0) * (g.stackSize ?? 0);
    const existing = merged.get(id);
    if (existing) existing.capacity += capacity;
    else merged.set(id, { id, category: g.category, direction: g.direction, capacity });
  }
  const ports = [...merged.values()];
  const dirRank: Record<Direction, number> = { in: 0, out: 1 };
  const catRank: Record<Category, number> = { solid: 0, liquid: 1, energy: 2 };
  ports.sort(
    (a, b) =>
      dirRank[a.direction] - dirRank[b.direction] ||
      catRank[a.category] - catRank[b.category],
  );
  return ports;
}

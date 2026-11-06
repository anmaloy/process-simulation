// World ↔ React Flow mapping (WORKFLOW.md P6).
//
// The World is the source of truth; these helpers project it into the nodes and
// edges React Flow renders, and build the entity edits that flow back through
// the API when the user places a machine, wires a pipe, or moves a node. The
// engine's balance output (per-node status, per-pipe flow) is layered onto this
// in P8 via the optional `data` fields — absent here means "not yet computed."

import type { Edge, Node } from "@xyflow/react";
import type {
  Category,
  MachineDef,
  MachineNode,
  Pipe,
  Status,
  World,
} from "../bridge";
import { parsePortId, portId } from "./ports";

export const MACHINE_NODE_TYPE = "machine";
export const PIPE_EDGE_TYPE = "pipe";

/** What a rendered machine node carries for its custom view. */
export interface MachineNodeData extends Record<string, unknown> {
  node: MachineNode;
  def: MachineDef | undefined;
  /** Balance status (P8); omitted until a recompute supplies it. */
  status?: Status;
}

/** What a rendered pipe edge carries for its custom view. */
export interface PipeEdgeData extends Record<string, unknown> {
  pipe: Pipe;
  category: Category;
  /** Resolved steady-state flow (P8); drives thickness when present. */
  flow?: number;
  /** True when the downstream end is starved (P8); drives the dashed stroke. */
  shortfall?: boolean;
}

export type RfMachineNode = Node<MachineNodeData, typeof MACHINE_NODE_TYPE>;
export type RfPipeEdge = Edge<PipeEdgeData, typeof PIPE_EDGE_TYPE>;

// Fallback layout for nodes that carry no stored position (e.g. a freshly
// loaded project authored without coordinates): a deterministic grid so they
// never stack at the origin. Dragging persists real `x`/`y`, which then win.
const FALLBACK_COLS = 4;
const FALLBACK_DX = 240;
const FALLBACK_DY = 150;

function fallbackPos(index: number): { x: number; y: number } {
  return {
    x: (index % FALLBACK_COLS) * FALLBACK_DX,
    y: Math.floor(index / FALLBACK_COLS) * FALLBACK_DY,
  };
}

/** Project the World's machine nodes into React Flow nodes. */
export function worldToNodes(world: World): RfMachineNode[] {
  const defs = new Map((world.machineDefs ?? []).map((d) => [d.id, d]));
  return (world.nodes ?? []).map((n, i) => {
    const fb = fallbackPos(i);
    return {
      id: n.id,
      type: MACHINE_NODE_TYPE,
      position: { x: n.x ?? fb.x, y: n.y ?? fb.y },
      data: { node: n, def: defs.get(n.machineDefId) },
    };
  });
}

/** Project the World's pipes into React Flow edges. */
export function worldToEdges(world: World): RfPipeEdge[] {
  return (world.pipes ?? []).map((p) => ({
    id: p.id,
    type: PIPE_EDGE_TYPE,
    source: p.a.nodeId,
    target: p.b.nodeId,
    sourceHandle: p.a.port ?? portId(p.category, "out"),
    targetHandle: p.b.port ?? portId(p.category, "in"),
    data: { pipe: p, category: p.category },
  }));
}

/** First id of the form `${prefix}-N` not already taken in `existing`. */
export function freshId(prefix: string, existing: Iterable<string>): string {
  const taken = new Set(existing);
  if (!taken.has(prefix)) return prefix;
  for (let i = 1; ; i++) {
    const candidate = `${prefix}-${i}`;
    if (!taken.has(candidate)) return candidate;
  }
}

/** A new MachineNode for `def` at a canvas position, with a free id. */
export function newNode(
  def: MachineDef,
  position: { x: number; y: number },
  world: World,
): MachineNode {
  const id = freshId(def.id, (world.nodes ?? []).map((n) => n.id));
  return {
    id,
    machineDefId: def.id,
    x: Math.round(position.x),
    y: Math.round(position.y),
  };
}

/**
 * Build a Pipe from a React Flow connection, or null if the connection is not
 * valid (handles must be a matching-category out → in pair).
 */
export function pipeFromConnection(
  conn: {
    source?: string | null;
    target?: string | null;
    sourceHandle?: string | null;
    targetHandle?: string | null;
  },
  world: World,
): Pipe | null {
  if (!conn.source || !conn.target) return null;
  const from = parsePortId(conn.sourceHandle);
  const to = parsePortId(conn.targetHandle);
  if (!from || !to) return null;
  if (from.direction !== "out" || to.direction !== "in") return null;
  if (from.category !== to.category) return null;

  const id = freshId("pipe", (world.pipes ?? []).map((p) => p.id));
  return {
    id,
    category: from.category,
    movementCapacity: defaultMovementCapacity(from.category),
    overclock: 1,
    a: { nodeId: conn.source, role: "out", port: conn.sourceHandle ?? undefined },
    b: { nodeId: conn.target, role: "in", port: conn.targetHandle ?? undefined },
  };
}

/**
 * Default sustained throughput by category (#10): solids move at 16/s and can be
 * raised; liquids are effectively instantaneous; electric connections start at
 * 256 EU/tick = 5120 EU/s (20 ticks/s).
 */
export function defaultMovementCapacity(category: Category): number {
  switch (category) {
    case "solid":
      return 16;
    case "liquid":
      return 1e9; // instantaneous (buffered by pipe storage, not throttled)
    case "energy":
      return 5120; // 256 EU/t × 20 t/s
  }
}

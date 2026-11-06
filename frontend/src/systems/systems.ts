// Systems — grouping logic (WORKFLOW.md P9).
//
// A System groups member nodes (and nested systems) and may be *collapsed* into
// a single canvas node. Collapse is presentation only: pipes always reference
// the real member nodes, so the engine's balance is identical folded or
// expanded. These helpers compute what the canvas should draw — which nodes are
// hidden under a collapsed proxy, which systems render as nodes — and the
// handle-id scheme that lets a collapsed system stay connectable.

import type { Category, Direction, Pipe, System, World } from "../bridge";
import { parsePortId, portId } from "../canvas/ports";
import { freshId, worldToNodes } from "../canvas/graph";

// A collapsed system is rendered as a React Flow node whose id is the system id
// behind this prefix, so it can never collide with a machine-node id.
export const SYSTEM_RF_PREFIX = "sys:";
export const systemRfId = (id: string): string => SYSTEM_RF_PREFIX + id;
export const isSystemRfId = (rfId: string): boolean => rfId.startsWith(SYSTEM_RF_PREFIX);
export const systemIdFromRf = (rfId: string): string =>
  rfId.slice(SYSTEM_RF_PREFIX.length);

/** A handle on a collapsed system node, standing in for a member node's port. */
const BOUNDARY_PREFIX = "b:";
/** Encode `(memberNodeId, port)` as a system-node handle id. */
export function boundaryHandleId(nodeId: string, port: string): string {
  return `${BOUNDARY_PREFIX}${nodeId}:${port}`;
}
export function isBoundaryHandle(handle: string | null | undefined): boolean {
  return !!handle && handle.startsWith(BOUNDARY_PREFIX);
}
/**
 * Decode a boundary handle back to its member node + port. The port is always a
 * `category:direction` pair (two trailing colon-separated tokens), so a node id
 * containing colons still round-trips: parse the port from the right.
 */
export function decodeBoundaryHandle(
  handle: string | null | undefined,
): { nodeId: string; port: string } | null {
  if (!isBoundaryHandle(handle)) return null;
  const rest = handle!.slice(BOUNDARY_PREFIX.length).split(":");
  if (rest.length < 3) return null;
  const direction = rest.pop()!;
  const category = rest.pop()!;
  const nodeId = rest.join(":");
  if (!nodeId) return null;
  return { nodeId, port: `${category}:${direction}` };
}

/** A pipe end resolved to a real node + port + its category/direction. */
export interface DecodedEnd {
  nodeId: string;
  port: string;
  category: Category;
  direction: Direction;
}

/**
 * Resolve a React Flow (nodeId, handle) to the real node + port it addresses.
 * A machine handle is `category:direction` on its own node; a collapsed-system
 * handle encodes the underlying member node + port.
 */
export function decodeEnd(
  rfNodeId: string,
  handle: string | null | undefined,
): DecodedEnd | null {
  if (isBoundaryHandle(handle)) {
    const b = decodeBoundaryHandle(handle);
    if (!b) return null;
    const pp = parsePortId(b.port);
    if (!pp) return null;
    return { nodeId: b.nodeId, port: b.port, category: pp.category, direction: pp.direction };
  }
  const pp = parsePortId(handle);
  if (!pp) return null;
  return { nodeId: rfNodeId, port: handle!, category: pp.category, direction: pp.direction };
}

/** Flattened membership of a system: all real node ids + nested system ids. */
export function flattenSystem(
  world: World,
  systemId: string,
  seen: Set<string> = new Set(),
): { nodeIds: Set<string>; systemIds: Set<string> } {
  const out = { nodeIds: new Set<string>(), systemIds: new Set<string>() };
  if (seen.has(systemId)) return out;
  seen.add(systemId);
  const sys = (world.systems ?? []).find((s) => s.id === systemId);
  if (!sys) return out;
  for (const n of sys.memberNodeIds ?? []) out.nodeIds.add(n);
  for (const sid of sys.memberSystemIds ?? []) {
    out.systemIds.add(sid);
    const sub = flattenSystem(world, sid, seen);
    sub.nodeIds.forEach((x) => out.nodeIds.add(x));
    sub.systemIds.forEach((x) => out.systemIds.add(x));
  }
  return out;
}

/** What the canvas folds away for the current collapse state. */
export interface CollapseState {
  /** Collapsed systems drawn as single nodes (outermost ones only). */
  renderedSystems: System[];
  /** Member node ids hidden under a rendered collapsed system. */
  hiddenNodeIds: Set<string>;
  /** Node id → the rendered system that stands in for it. */
  proxyForNode: Map<string, string>;
}

export function collapseState(world: World): CollapseState {
  const collapsed = (world.systems ?? []).filter((s) => s.collapsed);
  const flat = new Map(collapsed.map((s) => [s.id, flattenSystem(world, s.id)]));
  // A collapsed system nested inside another collapsed system is not drawn —
  // its outermost collapsed ancestor stands in for it.
  const nested = new Set<string>();
  for (const s of collapsed) {
    for (const other of collapsed) {
      if (other.id !== s.id && flat.get(other.id)!.systemIds.has(s.id)) {
        nested.add(s.id);
      }
    }
  }
  const renderedSystems = collapsed.filter((s) => !nested.has(s.id));
  const hiddenNodeIds = new Set<string>();
  const proxyForNode = new Map<string, string>();
  for (const s of renderedSystems) {
    for (const n of flat.get(s.id)!.nodeIds) {
      hiddenNodeIds.add(n);
      if (!proxyForNode.has(n)) proxyForNode.set(n, s.id);
    }
  }
  return { renderedSystems, hiddenNodeIds, proxyForNode };
}

/** A free `system-N` id not already taken. */
export function freshSystemId(world: World): string {
  return freshId("system", (world.systems ?? []).map((s) => s.id));
}

/** The centroid of the given nodes' canvas positions (for a new group's node). */
export function centroidOf(world: World, nodeIds: string[]): { x: number; y: number } {
  const pos = new Map(worldToNodes(world).map((n) => [n.id, n.position]));
  const pts = nodeIds.map((id) => pos.get(id)).filter((p): p is { x: number; y: number } => !!p);
  if (pts.length === 0) return { x: 0, y: 0 };
  const sum = pts.reduce((a, p) => ({ x: a.x + p.x, y: a.y + p.y }), { x: 0, y: 0 });
  return { x: Math.round(sum.x / pts.length), y: Math.round(sum.y / pts.length) };
}

/** Default port id for a pipe end when the stored end carries none. */
export function endPort(port: string | undefined, category: Category, side: Direction): string {
  return port ?? portId(category, side);
}

/** A React Flow connection, as passed by `onConnect` / `isValidConnection`. */
export interface ConnectionLike {
  source?: string | null;
  target?: string | null;
  sourceHandle?: string | null;
  targetHandle?: string | null;
}

/** Whether a connection is a valid matching-category out → in pair. */
export function isValidConnection(conn: ConnectionLike): boolean {
  const from = decodeEnd(conn.source ?? "", conn.sourceHandle);
  const to = decodeEnd(conn.target ?? "", conn.targetHandle);
  return (
    !!from &&
    !!to &&
    from.direction === "out" &&
    to.direction === "in" &&
    from.category === to.category
  );
}

/**
 * Build a real node→node Pipe from a connection, decoding collapsed-system
 * handles back to the member node/port they stand for. Null if invalid.
 */
export function pipeFromConnection(conn: ConnectionLike, world: World): Pipe | null {
  const from = decodeEnd(conn.source ?? "", conn.sourceHandle);
  const to = decodeEnd(conn.target ?? "", conn.targetHandle);
  if (!from || !to) return null;
  if (from.direction !== "out" || to.direction !== "in") return null;
  if (from.category !== to.category) return null;
  const id = freshId("pipe", (world.pipes ?? []).map((p) => p.id));
  return {
    id,
    category: from.category,
    movementCapacity: 10,
    overclock: 1,
    a: { nodeId: from.nodeId, role: "out", port: from.port },
    b: { nodeId: to.nodeId, role: "in", port: to.port },
  };
}

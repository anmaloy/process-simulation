// Build the React Flow graph with collapsed systems folded (WORKFLOW.md P9).
//
// Takes the World (the single source of truth) plus the current balance report
// and per-system reports, and returns the nodes + edges to render: visible
// machine nodes, one node per outermost collapsed system, and pipes re-pointed
// so a pipe crossing a collapsed boundary attaches to the system node's handle.
// Pipes fully inside one collapsed system are dropped (internal). Balance status
// (P8) rides along: machine status from the report, per-pipe flow from
// pipeFlows, and a derived aggregate status/energy on each system node.

import type { Node } from "@xyflow/react";
import type {
  Category,
  Direction,
  ScopeReport,
  Status,
  System,
  World,
} from "../bridge";
import { parsePortId } from "../canvas/ports";
import {
  worldToEdges,
  worldToNodes,
  type PipeEdgeData,
  type RfMachineNode,
  type RfPipeEdge,
} from "../canvas/graph";
import {
  boundaryHandleId,
  collapseState,
  endPort,
  flattenSystem,
  systemRfId,
} from "./systems";

export const SYSTEM_NODE_TYPE = "system";

/** One connectable handle on a collapsed system node. */
export interface SysHandle {
  id: string;
  category: Category;
  direction: Direction;
  label: string;
}

/** What a rendered (collapsed) system node carries for its custom view. */
export interface SystemNodeData extends Record<string, unknown> {
  system: System;
  memberCount: number;
  /** Aggregate balance status (worst member), if a report is available. */
  status?: Status;
  /** Aggregate net energy for the system scope, if any member has a term. */
  netEnergy?: number;
  handles: SysHandle[];
}

export type RfSystemNode = Node<SystemNodeData, typeof SYSTEM_NODE_TYPE>;
export type RfAnyNode = RfMachineNode | RfSystemNode;

/** The worst member status in a system report (shortfall ≻ overflow ≻ ok). */
function aggregateStatus(report: ScopeReport | undefined): Status | undefined {
  if (!report) return undefined;
  const states = Object.values(report.balances).map((b) => b.status);
  if (states.includes("shortfall")) return "shortfall";
  if (states.includes("overflow")) return "overflow";
  if (states.includes("ok")) return "ok";
  return undefined;
}

export interface BuiltGraph {
  nodes: RfAnyNode[];
  edges: RfPipeEdge[];
}

export function buildCanvasGraph(
  world: World,
  report: ScopeReport | null,
  systemReports: Record<string, ScopeReport>,
): BuiltGraph {
  const cs = collapseState(world);
  const status = report?.balances ?? {};
  const pipeFlows = report?.pipeFlows ?? {};
  const duty = report?.nodeDuty ?? {};

  // Visible machine nodes (members hidden under a collapsed system drop out).
  const machineNodes: RfAnyNode[] = worldToNodes(world)
    .filter((n) => !cs.hiddenNodeIds.has(n.id))
    .map((n) => ({
      ...n,
      data: { ...n.data, status: status[n.id]?.status, duty: duty[n.id] },
    }));

  // Accumulate the handles each rendered system needs, from the pipes that
  // cross its boundary plus any explicitly exposed ports.
  const handleAcc = new Map<string, Map<string, SysHandle>>();
  for (const s of cs.renderedSystems) handleAcc.set(s.id, new Map());
  const exposedName = new Map<string, string>(); // `${sysId}|${nodeId}:${port}` → name
  for (const s of cs.renderedSystems) {
    for (const ep of s.exposedPorts ?? []) {
      if (!ep.port) continue;
      const pp = parsePortId(ep.port);
      if (!pp) continue;
      const hid = boundaryHandleId(ep.nodeId, ep.port);
      handleAcc.get(s.id)!.set(hid, {
        id: hid,
        category: pp.category,
        direction: pp.direction,
        label: ep.name ?? ep.nodeId,
      });
      if (ep.name) exposedName.set(`${s.id}|${ep.nodeId}:${ep.port}`, ep.name);
    }
  }

  const addBoundaryHandle = (
    sysId: string,
    nodeId: string,
    port: string,
    category: Category,
    side: Direction,
  ): void => {
    const hid = boundaryHandleId(nodeId, port);
    const bucket = handleAcc.get(sysId);
    if (!bucket || bucket.has(hid)) return;
    bucket.set(hid, {
      id: hid,
      category,
      direction: side,
      label: exposedName.get(`${sysId}|${nodeId}:${port}`) ?? nodeId,
    });
  };

  // Re-point edges across collapsed boundaries; drop fully-internal ones.
  const edges: RfPipeEdge[] = [];
  for (const base of worldToEdges(world)) {
    const pipe = base.data!.pipe;
    const category = base.data!.category;
    const aPort = endPort(pipe.a.port, category, "out");
    const bPort = endPort(pipe.b.port, category, "in");
    const aProxy = cs.proxyForNode.get(pipe.a.nodeId) ?? null;
    const bProxy = cs.proxyForNode.get(pipe.b.nodeId) ?? null;
    if (aProxy && aProxy === bProxy) continue; // internal to one system

    if (aProxy) addBoundaryHandle(aProxy, pipe.a.nodeId, aPort, category, "out");
    if (bProxy) addBoundaryHandle(bProxy, pipe.b.nodeId, bPort, category, "in");

    const pf = pipeFlows[pipe.id];
    const data: PipeEdgeData = {
      pipe,
      category,
      flow: pf?.flow,
      shortfall: pf?.shortfall,
    };
    edges.push({
      ...base,
      source: aProxy ? systemRfId(aProxy) : pipe.a.nodeId,
      target: bProxy ? systemRfId(bProxy) : pipe.b.nodeId,
      sourceHandle: aProxy ? boundaryHandleId(pipe.a.nodeId, aPort) : base.sourceHandle,
      targetHandle: bProxy ? boundaryHandleId(pipe.b.nodeId, bPort) : base.targetHandle,
      data,
    });
  }

  // One node per rendered collapsed system. A system with no explicit position
  // sits at the centroid of its (current) members, so a folded group always
  // appears where the work is rather than teleporting to the origin — otherwise
  // the members read as "disappeared" when they fold.
  const nodePos = new Map(worldToNodes(world).map((n) => [n.id, n.position]));
  const memberCentroid = (s: System): { x: number; y: number } => {
    const pts = [...flattenSystem(world, s.id).nodeIds]
      .map((id) => nodePos.get(id))
      .filter((p): p is { x: number; y: number } => !!p);
    if (pts.length === 0) return { x: 0, y: 0 };
    const sum = pts.reduce((a, p) => ({ x: a.x + p.x, y: a.y + p.y }), { x: 0, y: 0 });
    return { x: Math.round(sum.x / pts.length), y: Math.round(sum.y / pts.length) };
  };
  const systemNodes: RfAnyNode[] = cs.renderedSystems.map((s) => {
    const sr = systemReports[s.id];
    const fb = memberCentroid(s);
    const handles = [...handleAcc.get(s.id)!.values()];
    return {
      id: systemRfId(s.id),
      type: SYSTEM_NODE_TYPE,
      position: { x: s.x ?? fb.x, y: s.y ?? fb.y },
      data: {
        system: s,
        memberCount: flattenSystem(world, s.id).nodeIds.size,
        status: aggregateStatus(sr),
        netEnergy: sr?.netEnergy,
        handles,
      },
    };
  });

  return { nodes: [...machineNodes, ...systemNodes], edges };
}

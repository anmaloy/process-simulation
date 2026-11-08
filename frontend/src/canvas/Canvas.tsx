// The node-graph canvas (WORKFLOW.md P6).
//
// World is the source of truth: React Flow state is seeded from it and every
// structural gesture — place a machine, wire a pipe, move or delete a node —
// bubbles up as an entity edit the host persists through the P4 API, which
// returns the updated World and reseeds the graph. Positions live on the
// MachineNode (`x`/`y`), so a moved layout round-trips into a loadable project.
//
// Pan/zoom, the minimap, and controls are React Flow defaults. Custom nodes and
// edges carry the category color map and leave seams (status, flow) for the P8
// balance overlay.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  useReactFlow,
  type Connection,
  type EdgeTypes,
  type IsValidConnection,
  type NodeTypes,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import type { MachineDef, MachineNode, Pipe, ScopeReport, World } from "../bridge";
import { Combobox } from "../ui";
import { MachineNodeView } from "./MachineNodeView";
import { PipeEdgeView } from "./PipeEdgeView";
import { categoryColor } from "./colors";
import {
  DEF_DRAG_MIME,
  MACHINE_NODE_TYPE,
  PIPE_EDGE_TYPE,
  newNode,
  type RfPipeEdge,
} from "./graph";
import {
  SYSTEM_NODE_TYPE,
  SystemActionsContext,
  SystemNodeView,
  buildCanvasGraph,
  collapseState,
  isSystemRfId,
  isValidConnection as isValidSystemConnection,
  pipeFromConnection,
  systemIdFromRf,
  type RfAnyNode,
} from "../systems";

const nodeTypes: NodeTypes = {
  [MACHINE_NODE_TYPE]: MachineNodeView,
  [SYSTEM_NODE_TYPE]: SystemNodeView,
};
const edgeTypes: EdgeTypes = { [PIPE_EDGE_TYPE]: PipeEdgeView };

/** What the inspector edits — a single canvas element (node, pipe, or system). */
export type CanvasSelection = { kind: "node" | "pipe" | "system"; id: string } | null;

export interface CanvasProps {
  world: World;
  /** The selected canvas element, controlled by the host (null = cleared). */
  selection: CanvasSelection;
  /** Add a placed machine; the host assigns position and persists it. */
  onAddNode: (node: MachineNode) => void;
  /** Persist a node's moved position. */
  onMoveNode: (id: string, x: number, y: number) => void;
  /** Wire a new pipe. */
  onAddPipe: (pipe: Pipe) => void;
  /** Remove nodes (and, via React Flow, their incident pipes). */
  onRemoveNodes: (ids: string[]) => void;
  /** Remove pipes. */
  onRemovePipes: (ids: string[]) => void;
  /** Report a click selection (element or empty-pane clear) for the inspector. */
  onSelect: (sel: CanvasSelection) => void;
  /** Latest balance report (P8 seam); per-node status tints the node bar. */
  report?: ScopeReport | null;
  /** Per-system reports (P9); drive a collapsed system node's aggregate stats. */
  systemReports?: Record<string, ScopeReport>;
  /** Group the given member node ids into a new collapsed system. */
  onGroupNodes: (nodeIds: string[]) => void;
  /** Toggle a system's collapsed flag (fold ↔ expand). */
  onToggleCollapse: (systemId: string) => void;
  /** Persist a collapsed system node's moved position. */
  onMoveSystem: (systemId: string, x: number, y: number) => void;
  /** Remove systems (the grouping only; members stay). */
  onRemoveSystems: (ids: string[]) => void;
}

function CanvasInner(props: CanvasProps) {
  const {
    world,
    selection,
    onAddNode,
    onMoveNode,
    onAddPipe,
    onRemoveNodes,
    onRemovePipes,
    onSelect,
    report,
    systemReports,
    onGroupNodes,
    onToggleCollapse,
    onMoveSystem,
    onRemoveSystems,
  } = props;

  const [nodes, setNodes, onNodesChange] = useNodesState<RfAnyNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<RfPipeEdge>([]);
  // Machine node ids the user has box/shift-selected on the canvas (React Flow's
  // own multi-selection), tracked so ≥2 can be grouped into a system.
  const [multiSelected, setMultiSelected] = useState<string[]>([]);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const { screenToFlowPosition } = useReactFlow();

  // Reseed from the World whenever it (or the host selection / reports) changes.
  // `buildCanvasGraph` folds collapsed systems into single nodes and re-points
  // boundary pipes (P9); status + flow (P8) ride in from the reports. Selection
  // is host-controlled (click-driven), but a multi-selection the user is
  // box-dragging is preserved so the "Group" affordance survives a reseed.
  useEffect(() => {
    const { nodes: built, edges: builtEdges } = buildCanvasGraph(
      world,
      report ?? null,
      systemReports ?? {},
    );
    const multi = new Set(multiSelected);
    setNodes(
      built.map((n) => {
        const isSystem = n.type === SYSTEM_NODE_TYPE;
        const hostSelected = isSystem
          ? selection?.kind === "system" && selection.id === systemIdFromRf(n.id)
          : selection?.kind === "node" && selection.id === n.id;
        return { ...n, selected: hostSelected || (!isSystem && multi.has(n.id)) };
      }),
    );
    setEdges(
      builtEdges.map((e) => ({
        ...e,
        selected: selection?.kind === "pipe" && selection.id === e.id,
      })),
    );
    // multiSelected is intentionally excluded: a reseed from it would wipe the
    // very selection the user is dragging. It is re-applied via onSelectionChange.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [world, selection, report, systemReports, setNodes, setEdges]);

  const defs = useMemo(() => world.machineDefs ?? [], [world.machineDefs]);

  const handleConnect = useCallback(
    (conn: Connection) => {
      const pipe = pipeFromConnection(conn, world);
      if (pipe) onAddPipe(pipe);
    },
    [world, onAddPipe],
  );

  // Only allow out → in connections between matching categories (decoding
  // collapsed-system handles back to the member port they stand for).
  const isValidConnection = useCallback<IsValidConnection>(
    (conn) => isValidSystemConnection(conn),
    [],
  );

  const handleNodeDragStop = useCallback(
    (_e: unknown, node: { id: string; position: { x: number; y: number } }) => {
      const x = Math.round(node.position.x);
      const y = Math.round(node.position.y);
      if (isSystemRfId(node.id)) onMoveSystem(systemIdFromRf(node.id), x, y);
      else onMoveNode(node.id, x, y);
    },
    [onMoveNode, onMoveSystem],
  );

  const handleNodesDelete = useCallback(
    (deleted: { id: string }[]) => {
      const sys = deleted.filter((n) => isSystemRfId(n.id)).map((n) => systemIdFromRf(n.id));
      const machines = deleted.filter((n) => !isSystemRfId(n.id)).map((n) => n.id);
      if (sys.length) onRemoveSystems(sys);
      if (machines.length) onRemoveNodes(machines);
    },
    [onRemoveNodes, onRemoveSystems],
  );

  const handleEdgesDelete = useCallback(
    (deleted: { id: string }[]) => onRemovePipes(deleted.map((e) => e.id)),
    [onRemovePipes],
  );

  const handleNodeClick = useCallback(
    (_e: unknown, node: { id: string }) =>
      onSelect(
        isSystemRfId(node.id)
          ? { kind: "system", id: systemIdFromRf(node.id) }
          : { kind: "node", id: node.id },
      ),
    [onSelect],
  );
  const handleEdgeClick = useCallback(
    (_e: unknown, edge: { id: string }) => onSelect({ kind: "pipe", id: edge.id }),
    [onSelect],
  );
  const handlePaneClick = useCallback(() => {
    onSelect(null);
    // Also clear React Flow's own selection immediately, so a single background
    // click fully deselects (the host reseed alone can lag the glow) (#6).
    setNodes((ns) => ns.map((n) => (n.selected ? { ...n, selected: false } : n)));
    setEdges((es) => es.map((e) => (e.selected ? { ...e, selected: false } : e)));
    setMultiSelected([]);
  }, [onSelect, setNodes, setEdges]);

  // Track React Flow's own multi-selection of machine nodes for grouping.
  const handleSelectionChange = useCallback(
    ({ nodes: sel }: { nodes: { id: string }[] }) => {
      setMultiSelected(sel.filter((n) => !isSystemRfId(n.id)).map((n) => n.id));
    },
    [],
  );

  const systemActions = useMemo(
    () => ({ onExpand: onToggleCollapse }),
    [onToggleCollapse],
  );

  // Only visible (not folded-away) machine nodes can be grouped, so a stale
  // multi-selection of nodes that just became members doesn't re-offer "Group".
  const groupable = useMemo(() => {
    const hidden = collapseState(world).hiddenNodeIds;
    return multiSelected.filter((id) => !hidden.has(id));
  }, [multiSelected, world]);

  const addDef = useCallback(
    (def: MachineDef) => {
      const rect = wrapperRef.current?.getBoundingClientRect();
      const screen = rect
        ? { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 }
        : { x: window.innerWidth / 2, y: window.innerHeight / 2 };
      const base = screenToFlowPosition(screen);
      // Fan successive adds out from center so they never perfectly stack.
      const step = (world.nodes?.length ?? 0) % 6;
      const position = { x: base.x + step * 28, y: base.y + step * 28 };
      onAddNode(newNode(def, position, world));
    },
    [screenToFlowPosition, onAddNode, world],
  );

  // Drop a machine dragged from the sidebar at the cursor (#10).
  const handleDragOver = useCallback((e: React.DragEvent) => {
    if (e.dataTransfer.types.includes(DEF_DRAG_MIME)) {
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
    }
  }, []);
  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      const defId = e.dataTransfer.getData(DEF_DRAG_MIME);
      if (!defId) return;
      e.preventDefault();
      const def = (world.machineDefs ?? []).find((d) => d.id === defId);
      if (!def) return;
      const position = screenToFlowPosition({ x: e.clientX, y: e.clientY });
      onAddNode(newNode(def, position, world));
    },
    [screenToFlowPosition, onAddNode, world],
  );

  return (
    <div
      className="canvas-wrap"
      ref={wrapperRef}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    >
      <div className="canvas-palette">
        <span className="eyebrow quiet palette-label">Add machine</span>
        {defs.length === 0 ? (
          <span className="palette-empty mono">no machine defs</span>
        ) : (
          <div className="palette-combo">
            <Combobox
              value=""
              onChange={(id) => {
                const d = defs.find((x) => x.id === id);
                if (d) addDef(d);
              }}
              options={defs.map((d) => ({
                value: d.id,
                label: d.name ?? d.id,
                hint: d.storage ? "buffer" : undefined,
              }))}
              placeholder="+ place a machine…"
              dense
            />
          </div>
        )}
        {groupable.length >= 2 && (
          <button
            type="button"
            className="tool-btn palette-btn group-btn"
            onClick={() => onGroupNodes(groupable)}
            title="Group the selected machines into a system"
          >
            ▦ Group {groupable.length}
          </button>
        )}
      </div>
      <SystemActionsContext.Provider value={systemActions}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={handleConnect}
          isValidConnection={isValidConnection}
          onNodeDragStop={handleNodeDragStop}
          onNodesDelete={handleNodesDelete}
          onEdgesDelete={handleEdgesDelete}
          onNodeClick={handleNodeClick}
          onEdgeClick={handleEdgeClick}
          onPaneClick={handlePaneClick}
          onSelectionChange={handleSelectionChange}
          // `fitView` fits once, when the first measured node mounts. Cap its
          // zoom at 1: without this, placing the first machine on an empty
          // canvas fits that single node to `maxZoom` (2.5), blowing it up to
          // fill the pane. The fit never re-runs, so the next edit (or the
          // inspector opening and shrinking the pane) then pushes the oversized
          // nodes off-screen and they read as "disappeared". Fitting at ≤1:1
          // keeps placed nodes at a normal size and on-screen; fitting many
          // spread-out nodes still zooms out as needed (maxZoom only caps in).
          fitViewOptions={{ maxZoom: 1, padding: 0.3 }}
          minZoom={0.2}
          maxZoom={2.5}
          proOptions={{ hideAttribution: true }}
        >
          <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="var(--line)" />
          <MiniMap
            pannable
            zoomable
            nodeColor={(n) => {
              if (n.type === SYSTEM_NODE_TYPE) return "var(--s-expert)";
              const def = (n.data as { def?: MachineDef }).def;
              const cat = def?.slots?.[0]?.category;
              return cat ? categoryColor(cat) : "var(--line-2)";
            }}
            maskColor="rgba(11, 15, 23, 0.72)"
          />
          <Controls />
        </ReactFlow>
      </SystemActionsContext.Provider>
    </div>
  );
}

/** Canvas entry point — wraps the graph in a React Flow provider. */
export function Canvas(props: CanvasProps) {
  return (
    <ReactFlowProvider>
      <CanvasInner {...props} />
    </ReactFlowProvider>
  );
}

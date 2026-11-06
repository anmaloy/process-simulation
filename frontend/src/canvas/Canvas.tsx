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

import { useCallback, useEffect, useMemo, useRef } from "react";
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
  type OnSelectionChangeParams,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import type { MachineDef, MachineNode, Pipe, World } from "../bridge";
import { MachineNodeView } from "./MachineNodeView";
import { PipeEdgeView } from "./PipeEdgeView";
import { categoryColor } from "./colors";
import { parsePortId } from "./ports";
import {
  MACHINE_NODE_TYPE,
  PIPE_EDGE_TYPE,
  newNode,
  pipeFromConnection,
  worldToEdges,
  worldToNodes,
  type RfMachineNode,
  type RfPipeEdge,
} from "./graph";

const nodeTypes: NodeTypes = { [MACHINE_NODE_TYPE]: MachineNodeView };
const edgeTypes: EdgeTypes = { [PIPE_EDGE_TYPE]: PipeEdgeView };

export interface CanvasProps {
  world: World;
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
  /** Report the current selection (single entity or none) for the inspector (P7). */
  onSelect?: (sel: { kind: "node" | "pipe"; id: string } | null) => void;
}

function CanvasInner(props: CanvasProps) {
  const {
    world,
    onAddNode,
    onMoveNode,
    onAddPipe,
    onRemoveNodes,
    onRemovePipes,
    onSelect,
  } = props;

  const [nodes, setNodes, onNodesChange] = useNodesState<RfMachineNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<RfPipeEdge>([]);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const { screenToFlowPosition } = useReactFlow();

  // Reseed from the World whenever it changes, preserving the transient
  // selection highlight (which lives on React Flow state, not the World).
  useEffect(() => {
    setNodes((prev) => {
      const sel = new Set(prev.filter((n) => n.selected).map((n) => n.id));
      return worldToNodes(world).map((n) =>
        sel.has(n.id) ? { ...n, selected: true } : n,
      );
    });
    setEdges((prev) => {
      const sel = new Set(prev.filter((e) => e.selected).map((e) => e.id));
      return worldToEdges(world).map((e) =>
        sel.has(e.id) ? { ...e, selected: true } : e,
      );
    });
  }, [world, setNodes, setEdges]);

  const defs = useMemo(() => world.machineDefs ?? [], [world.machineDefs]);

  const handleConnect = useCallback(
    (conn: Connection) => {
      const pipe = pipeFromConnection(conn, world);
      if (pipe) onAddPipe(pipe);
    },
    [world, onAddPipe],
  );

  // Only allow out → in connections between matching categories.
  const isValidConnection = useCallback<IsValidConnection>((conn) => {
    const from = parsePortId(conn.sourceHandle);
    const to = parsePortId(conn.targetHandle);
    return (
      !!from &&
      !!to &&
      from.direction === "out" &&
      to.direction === "in" &&
      from.category === to.category
    );
  }, []);

  const handleNodeDragStop = useCallback(
    (_e: unknown, node: { id: string; position: { x: number; y: number } }) => {
      onMoveNode(node.id, Math.round(node.position.x), Math.round(node.position.y));
    },
    [onMoveNode],
  );

  const handleNodesDelete = useCallback(
    (deleted: { id: string }[]) => onRemoveNodes(deleted.map((n) => n.id)),
    [onRemoveNodes],
  );

  const handleEdgesDelete = useCallback(
    (deleted: { id: string }[]) => onRemovePipes(deleted.map((e) => e.id)),
    [onRemovePipes],
  );

  const handleSelectionChange = useCallback(
    (params: OnSelectionChangeParams) => {
      if (!onSelect) return;
      if (params.nodes.length === 1 && params.edges.length === 0) {
        onSelect({ kind: "node", id: params.nodes[0].id });
      } else if (params.edges.length === 1 && params.nodes.length === 0) {
        onSelect({ kind: "pipe", id: params.edges[0].id });
      } else {
        onSelect(null);
      }
    },
    [onSelect],
  );

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

  return (
    <div className="canvas-wrap" ref={wrapperRef}>
      <div className="canvas-palette">
        <span className="eyebrow quiet palette-label">Add machine</span>
        {defs.length === 0 && <span className="palette-empty mono">no machine defs</span>}
        {defs.map((d) => (
          <button
            key={d.id}
            type="button"
            className="tool-btn palette-btn"
            onClick={() => addDef(d)}
            title={`Place ${d.name ?? d.id}`}
          >
            + {d.name ?? d.id}
          </button>
        ))}
      </div>
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
        onSelectionChange={handleSelectionChange}
        fitView
        minZoom={0.2}
        maxZoom={2.5}
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="var(--line)" />
        <MiniMap
          pannable
          zoomable
          nodeColor={(n) => {
            const def = (n.data as { def?: MachineDef }).def;
            const cat = def?.slots?.[0]?.category;
            return cat ? categoryColor(cat) : "var(--line-2)";
          }}
          maskColor="rgba(11, 15, 23, 0.72)"
        />
        <Controls />
      </ReactFlow>
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

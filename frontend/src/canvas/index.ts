// Canvas barrel (WORKFLOW.md P6) — the node-graph surface and the public seams
// the rest of the frontend builds on (the category color map, port derivation,
// and the World↔React Flow mapping).

export { Canvas } from "./Canvas";
export type { CanvasProps } from "./Canvas";
export { CATEGORY_COLOR, categoryColor } from "./colors";
export { portsForDef, portId, parsePortId } from "./ports";
export type { Port } from "./ports";
export {
  worldToNodes,
  worldToEdges,
  newNode,
  pipeFromConnection,
  freshId,
  MACHINE_NODE_TYPE,
  PIPE_EDGE_TYPE,
} from "./graph";
export type {
  MachineNodeData,
  PipeEdgeData,
  RfMachineNode,
  RfPipeEdge,
} from "./graph";

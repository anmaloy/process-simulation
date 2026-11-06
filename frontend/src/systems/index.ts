// Systems — grouping (WORKFLOW.md P9): collapse/expand, nesting, and connecting
// a group as a single node, layered over the World without changing the engine.

export {
  SYSTEM_RF_PREFIX,
  systemRfId,
  isSystemRfId,
  systemIdFromRf,
  boundaryHandleId,
  isBoundaryHandle,
  decodeBoundaryHandle,
  decodeEnd,
  flattenSystem,
  collapseState,
  freshSystemId,
  centroidOf,
  isValidConnection,
  pipeFromConnection,
  endPort,
  type CollapseState,
  type DecodedEnd,
  type ConnectionLike,
} from "./systems";
export {
  SYSTEM_NODE_TYPE,
  buildCanvasGraph,
  type BuiltGraph,
  type RfSystemNode,
  type RfAnyNode,
  type SystemNodeData,
  type SysHandle,
} from "./build";
export { SystemNodeView } from "./SystemNodeView";
export { SystemActionsContext, type SystemActions } from "./context";

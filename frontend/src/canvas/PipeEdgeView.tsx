// Custom pipe edge (WORKFLOW.md P6) — an SVG bezier path colored by the pipe's
// category (from the single color map). Thickness scales with resolved flow and
// a starved pipe is dashed; both read from edge `data` the balance pass (P8)
// fills in, so until then every pipe renders at its base width, solid. The cyan
// glow is applied only when the edge is selected — the reserved live-element
// treatment.

import { BaseEdge, getBezierPath, type EdgeProps } from "@xyflow/react";
import { categoryColor } from "./colors";
import type { RfPipeEdge, PipeEdgeData } from "./graph";

const BASE_WIDTH = 1.6;
const MAX_WIDTH = 6;

/** Map a flow magnitude to a stroke width; undefined → base width. */
function flowWidth(flow: number | undefined): number {
  if (flow == null || flow <= 0) return BASE_WIDTH;
  // Gentle sqrt scaling so a wide range of rates stays legible.
  return Math.min(MAX_WIDTH, BASE_WIDTH + Math.sqrt(flow));
}

export function PipeEdgeView(props: EdgeProps<RfPipeEdge>) {
  const {
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    data,
    selected,
    markerEnd,
  } = props;

  const { category, flow, shortfall } = (data ?? {}) as PipeEdgeData;
  const [path] = getBezierPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
  });

  const color = selected ? "var(--spine)" : categoryColor(category);
  const style: React.CSSProperties = {
    stroke: color,
    strokeWidth: flowWidth(flow),
    strokeDasharray: shortfall ? "6 4" : undefined,
    filter: selected ? "drop-shadow(0 0 5px var(--spine-glow))" : undefined,
  };

  return <BaseEdge path={path} markerEnd={markerEnd} style={style} />;
}

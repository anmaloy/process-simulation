// Custom machine node (WORKFLOW.md P6) — a `div` body wearing the style tokens,
// with ports rendered as React Flow handles, one per (category × direction)
// group from the MachineDef slot layout. Input ports (target handles) sit on
// the left, output ports (source handles) on the right, each dotted in its
// category color (from the single color map). The cyan selected-glow is applied
// via the `.selected` class React Flow sets — reserved for the selected element.
//
// Balance status (P8) tints the left status bar; until a recompute supplies it
// the node reads neutral ("unpowered/absent" grey), never a fabricated OK.

import { Handle, Position, type NodeProps } from "@xyflow/react";
import { categoryColor } from "./colors";
import { portsForDef, type Port } from "./ports";
import type { MachineNodeData, RfMachineNode } from "./graph";
import { STATUS_COLOR, STATUS_FILL, STATUS_LABEL, statusColor } from "../overlay/status";

// Evenly distribute n handles down a side: vertical center of each slot, as a
// top percentage (one handle → 50%, two → 25%/75%, …).
function topPct(index: number, n: number): string {
  return `${((index + 0.5) / n) * 100}%`;
}

function PortRow({
  port,
  side,
  index,
  count,
}: {
  port: Port;
  side: "in" | "out";
  index: number;
  count: number;
}) {
  const isIn = side === "in";
  return (
    <>
      {/* React Flow handle: default absolute edge positioning (Position.Left /
          Right keeps its own centering transform so connection hit-testing and
          the drawn connection line both anchor correctly). */}
      <Handle
        id={port.id}
        type={isIn ? "target" : "source"}
        position={isIn ? Position.Left : Position.Right}
        className="port-handle"
        style={{ top: topPct(index, count), background: categoryColor(port.category) }}
      />
      <span
        className={`port-label mono ${isIn ? "port-label-in" : "port-label-out"}`}
        style={{ top: topPct(index, count) }}
      >
        {port.category}
      </span>
    </>
  );
}

export function MachineNodeView({ data }: NodeProps<RfMachineNode>) {
  const { node, def, status } = data as MachineNodeData;
  const ports = portsForDef(def);
  const ins = ports.filter((p) => p.direction === "in");
  const outs = ports.filter((p) => p.direction === "out");
  const title = def?.name ?? node.machineDefId;

  const meta: string[] = [];
  if (node.recipeId) meta.push(node.recipeId);
  if (node.overclock != null && node.overclock !== 1) meta.push(`×${node.overclock}`);
  if (node.count != null && node.count !== 1) meta.push(`∥${node.count}`);

  const rows = Math.max(ins.length, outs.length, 1);

  return (
    <div className="machine-node" data-status={status ?? "stalled"}>
      <span className="node-status" style={{ background: statusColor(status) }} />
      <div className="node-head">
        <span className="node-title">{title}</span>
        <span className="node-id mono">{node.id}</span>
      </div>
      {(meta.length > 0 || status) && (
        <div className="node-meta mono">
          {status && (
            <span
              className="node-status-chip"
              style={{ background: STATUS_FILL[status], color: STATUS_COLOR[status] }}
              title={`balance: ${STATUS_LABEL[status]}`}
            >
              {STATUS_LABEL[status]}
            </span>
          )}
          {meta.length > 0 && <span>{meta.join("  ")}</span>}
        </div>
      )}
      <div className="node-ports" style={{ height: rows * 22 }}>
        {ins.map((p, i) => (
          <PortRow key={p.id} port={p} side="in" index={i} count={ins.length} />
        ))}
        {outs.map((p, i) => (
          <PortRow key={p.id} port={p} side="out" index={i} count={outs.length} />
        ))}
      </div>
    </div>
  );
}

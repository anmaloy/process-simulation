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

const UNIT: Record<string, string> = { solid: "", liquid: " mB", energy: " EU" };

/** Short human form of a capacity, e.g. 64000 → "64k mB". */
function fmtCapacity(value: number, category: string): string {
  const unit = UNIT[category] ?? "";
  const abs = Math.abs(value);
  if (abs >= 1e9) return `${+(value / 1e9).toPrecision(3)}G${unit}`;
  if (abs >= 1e6) return `${+(value / 1e6).toPrecision(3)}M${unit}`;
  if (abs >= 1e3) return `${+(value / 1e3).toPrecision(3)}k${unit}`;
  return `${+value.toPrecision(3)}${unit}`;
}

export function MachineNodeView({ data }: NodeProps<RfMachineNode>) {
  const { node, def, status, duty } = data as MachineNodeData;
  const ports = portsForDef(def);
  const ins = ports.filter((p) => p.direction === "in");
  const outs = ports.filter((p) => p.direction === "out");
  const title = def?.name ?? node.machineDefId;

  const meta: string[] = [];
  if (node.recipeId) meta.push(node.recipeId);
  if (node.overclock != null && node.overclock !== 1) meta.push(`×${node.overclock}`);
  if (node.count != null && node.count !== 1) meta.push(`∥${node.count}`);

  const rows = Math.max(ins.length, outs.length, 1);
  const throttled = duty != null && duty < 0.999;

  return (
    <div className="machine-node" data-status={status ?? "stalled"}>
      <span className="node-status" style={{ background: statusColor(status) }} />
      <div className="node-head">
        <span className="node-title">{title}</span>
        <span className="node-id mono">{node.id}</span>
      </div>
      {def?.storage && (
        <div className="node-storage mono" title="buffer capacity (× parallel count)">
          ▭ {fmtCapacity(def.storage.capacity * (node.count ?? 1), def.storage.category)}
        </div>
      )}
      {(meta.length > 0 || status || throttled) && (
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
          {throttled && (
            <span
              className="node-duty-chip"
              title="running below full rate (starved input)"
            >
              {Math.round((duty ?? 0) * 100)}%
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

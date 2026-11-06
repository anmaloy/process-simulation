// Custom collapsed-system node (WORKFLOW.md P9) — a system folded into one
// connectable node. It wears the same tokens as a machine node, shows the
// aggregate balance (status chip + net energy) and member count, and exposes a
// handle per boundary/exposed port so pipes connect to it as a single unit.
// An expand control (via SystemActionsContext) unfolds it back to its members.
// Cyan selected-glow stays reserved for the selection.

import { useContext } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { categoryColor } from "../canvas/colors";
import { STATUS_COLOR, STATUS_FILL, STATUS_LABEL, statusColor } from "../overlay/status";
import { formatEnergy } from "../overlay/units";
import { SystemActionsContext } from "./context";
import type { RfSystemNode, SysHandle, SystemNodeData } from "./build";

function topPct(index: number, n: number): string {
  return `${((index + 0.5) / n) * 100}%`;
}

function PortHandle({
  handle,
  index,
  count,
}: {
  handle: SysHandle;
  index: number;
  count: number;
}) {
  const isIn = handle.direction === "in";
  return (
    <>
      <Handle
        id={handle.id}
        type={isIn ? "target" : "source"}
        position={isIn ? Position.Left : Position.Right}
        className="port-handle"
        style={{ top: topPct(index, count), background: categoryColor(handle.category) }}
      />
      <span
        className={`port-label mono ${isIn ? "port-label-in" : "port-label-out"}`}
        style={{ top: topPct(index, count) }}
        title={`${handle.category} · ${handle.label}`}
      >
        {handle.label}
      </span>
    </>
  );
}

export function SystemNodeView({ data }: NodeProps<RfSystemNode>) {
  const { system, memberCount, status, netEnergy, handles } = data as SystemNodeData;
  const { onExpand } = useContext(SystemActionsContext);
  const title = system.name ?? system.id;

  const ins = handles.filter((h) => h.direction === "in");
  const outs = handles.filter((h) => h.direction === "out");
  const rows = Math.max(ins.length, outs.length, 1);

  return (
    <div className="machine-node system-node" data-status={status ?? "stalled"}>
      <span className="node-status" style={{ background: statusColor(status) }} />
      <div className="node-head">
        <span className="node-title">
          <span className="system-glyph">▦</span> {title}
        </span>
        <button
          type="button"
          className="system-expand"
          title="Expand system"
          onClick={(e) => {
            e.stopPropagation();
            onExpand(system.id);
          }}
        >
          ⤢
        </button>
      </div>
      <div className="node-meta mono">
        {status && (
          <span
            className="node-status-chip"
            style={{ background: STATUS_FILL[status], color: STATUS_COLOR[status] }}
          >
            {STATUS_LABEL[status]}
          </span>
        )}
        <span className="system-count">
          {memberCount} node{memberCount === 1 ? "" : "s"}
        </span>
        {netEnergy != null && <span className="system-energy">{formatEnergy(netEnergy)}</span>}
      </div>
      {handles.length > 0 && (
        <div className="node-ports" style={{ height: rows * 22 }}>
          {ins.map((h, i) => (
            <PortHandle key={h.id} handle={h} index={i} count={ins.length} />
          ))}
          {outs.map((h, i) => (
            <PortHandle key={h.id} handle={h} index={i} count={outs.length} />
          ))}
        </div>
      )}
    </div>
  );
}

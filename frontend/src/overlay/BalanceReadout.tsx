// Per-scope balance readout (WORKFLOW.md P8) — a floating panel over the canvas
// that shows the engine's aggregated stats for the chosen scope: external I/O,
// the net energy balance, and the ranked bottleneck list (from P2d), plus the
// scope switcher (machine / process / system / world) that selects which report
// the host computes. Numbers are auto-scaled for readability (decision #5);
// nothing is fabricated — an absent report reads as a prompt, not zeros.

import { useMemo } from "react";
import type { Scope, ScopeReport, World } from "../bridge";
import { formatEnergy, formatRate } from "./units";
import { STATUS_COLOR } from "./status";

/** The scope options, in switcher order. `node` is labelled "machine". */
const SCOPES: { scope: Scope; label: string }[] = [
  { scope: "world", label: "World" },
  { scope: "process", label: "Process" },
  { scope: "node", label: "Machine" },
  { scope: "system", label: "System" },
];

export interface BalanceReadoutProps {
  world: World;
  report: ScopeReport | null;
  scope: Scope;
  onScopeChange: (scope: Scope) => void;
  /** The node a machine/process scope targets (the current canvas selection). */
  selectedNodeId: string | null;
  /** The system a system scope targets. */
  systemId: string | null;
  onSystemChange: (systemId: string) => void;
}

export function BalanceReadout(props: BalanceReadoutProps) {
  const { world, report, scope, onScopeChange, selectedNodeId, systemId } = props;

  const itemName = useMemo(() => {
    const m = new Map<string, string>();
    for (const it of world.items ?? []) m.set(it.id, it.name ?? it.id);
    return m;
  }, [world.items]);
  const defName = useMemo(() => {
    const m = new Map<string, string>();
    for (const d of world.machineDefs ?? []) m.set(d.id, d.name ?? d.id);
    return m;
  }, [world.machineDefs]);
  const nodeDefName = useMemo(() => {
    const m = new Map<string, string>();
    for (const n of world.nodes ?? []) m.set(n.id, defName.get(n.machineDefId) ?? n.id);
    return m;
  }, [world.nodes, defName]);

  const systems = world.systems ?? [];
  // Whether the chosen scope has a target to compute against.
  const needsNode = scope === "node" || scope === "process";
  const hasTarget =
    scope === "world" ||
    (needsNode && !!selectedNodeId) ||
    (scope === "system" && !!systemId);

  return (
    <section className="panel-glass readout-panel">
      <div className="readout-head">
        <span className="eyebrow">Balance</span>
        <div className="scope-switch" role="group" aria-label="Scope">
          {SCOPES.map(({ scope: s, label }) => {
            const disabled =
              (s === "system" && systems.length === 0) ||
              ((s === "node" || s === "process") && !selectedNodeId && s !== scope);
            return (
              <button
                key={s}
                type="button"
                className={`scope-btn ${scope === s ? "active" : ""}`}
                aria-pressed={scope === s}
                disabled={disabled}
                onClick={() => onScopeChange(s)}
              >
                {label}
              </button>
            );
          })}
        </div>
      </div>

      {scope === "system" && systems.length > 0 && (
        <select
          className="field-input mono scope-system-pick"
          value={systemId ?? ""}
          onChange={(e) => props.onSystemChange(e.target.value)}
        >
          <option value="" disabled>
            select a system…
          </option>
          {systems.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name ?? s.id}
            </option>
          ))}
        </select>
      )}

      {!hasTarget ? (
        <p className="readout-hint mono">
          {needsNode
            ? "Select a machine on the canvas to scope here."
            : "Select a system to scope here."}
        </p>
      ) : !report ? (
        <p className="readout-hint mono">Computing…</p>
      ) : (
        <ReadoutBody
          report={report}
          itemName={itemName}
          nodeDefName={nodeDefName}
        />
      )}
    </section>
  );
}

function ReadoutBody({
  report,
  itemName,
  nodeDefName,
}: {
  report: ScopeReport;
  itemName: Map<string, string>;
  nodeDefName: Map<string, string>;
}) {
  const inputs = Object.entries(report.externalInputs);
  const outputs = Object.entries(report.externalOutputs);
  const name = (id: string) => itemName.get(id) ?? id;

  return (
    <div className="readout-body">
      <div className="readout-row">
        <span className="readout-metric-label">Nodes</span>
        <span className="readout-metric mono">{report.nodeIds.length}</span>
      </div>
      <div className="readout-row">
        <span className="readout-metric-label">Net energy</span>
        <span
          className="readout-metric mono"
          style={{
            color:
              report.netEnergy == null
                ? "var(--ink-faint)"
                : report.netEnergy < 0
                  ? "var(--s-bad)"
                  : "var(--s-ref)",
          }}
        >
          {report.netEnergy == null ? "—" : formatEnergy(report.netEnergy)}
        </span>
      </div>

      <ItemList title="External inputs" entries={inputs} name={name} />
      <ItemList title="External outputs" entries={outputs} name={name} />

      <div className="readout-section">
        <span className="eyebrow quiet">Bottlenecks</span>
        {report.bottlenecks.length === 0 ? (
          <p className="readout-empty mono">none</p>
        ) : (
          <ul className="readout-list">
            {report.bottlenecks.slice(0, 6).map((b, i) => (
              <li key={`${b.nodeId}:${b.itemId}:${i}`} className="readout-leaf">
                <span
                  className="readout-dot"
                  style={{ background: STATUS_COLOR.shortfall }}
                />
                <span className="readout-leaf-main">
                  {nodeDefName.get(b.nodeId) ?? b.nodeId}
                  <span className="readout-leaf-sub mono"> · {name(b.itemId)}</span>
                </span>
                <span className="readout-leaf-val mono">
                  −{formatRate(b.shortfall)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function ItemList({
  title,
  entries,
  name,
}: {
  title: string;
  entries: [string, number][];
  name: (id: string) => string;
}) {
  return (
    <div className="readout-section">
      <span className="eyebrow quiet">{title}</span>
      {entries.length === 0 ? (
        <p className="readout-empty mono">none</p>
      ) : (
        <ul className="readout-list">
          {entries.map(([itemId, rate]) => (
            <li key={itemId} className="readout-leaf">
              <span className="readout-leaf-main">{name(itemId)}</span>
              <span className="readout-leaf-val mono">{formatRate(rate)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

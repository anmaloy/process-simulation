// System inspector (WORKFLOW.md P9). Edits a group: its name, collapsed state,
// its member nodes and nested systems, and the ports it exposes for external
// connection. Membership is the group's definition; collapse is presentation.
// Exposed ports let a collapsed system be connected to while folded — each
// names an internal member port. Every change routes through `apply_edit` like
// any entity. Nested-system options exclude choices that would form a cycle.

import type { MachineDef, System, SystemPort, World } from "../bridge";
import { flattenSystem } from "../systems";
import { portsForDef } from "../canvas/ports";
import { Checkbox, Field, Select, StaticValue, TextInput, withField } from "./fields";
import { Combobox, type ComboOption } from "../ui";

export function SystemInspector({
  system,
  world,
  onChange,
}: {
  system: System;
  world: World;
  onChange: (next: System) => void;
}) {
  const nodes = world.nodes ?? [];
  const systems = world.systems ?? [];
  const defById = new Map((world.machineDefs ?? []).map((d) => [d.id, d] as const));
  const nodeById = new Map(nodes.map((n) => [n.id, n] as const));
  const members = new Set(system.memberNodeIds ?? []);
  const memberSystems = new Set(system.memberSystemIds ?? []);

  const nodeLabel = (id: string) => {
    const n = nodeById.get(id);
    const def = n ? defById.get(n.machineDefId) : undefined;
    return def?.name ?? n?.machineDefId ?? id;
  };

  const toggleMember = (id: string, on: boolean) => {
    const next = new Set(members);
    if (on) next.add(id);
    else next.delete(id);
    onChange(withField(system, "memberNodeIds", [...next]));
  };

  const toggleSubSystem = (id: string, on: boolean) => {
    const next = new Set(memberSystems);
    if (on) next.add(id);
    else next.delete(id);
    onChange(withField(system, "memberSystemIds", [...next]));
  };

  // A candidate sub-system must not be self, nor one that already contains this
  // system (which would make a cycle).
  const subCandidates = systems.filter(
    (s) => s.id !== system.id && !flattenSystem(world, s.id).systemIds.has(system.id),
  );

  // Ports exposable: every (member node × its def ports). The value encodes
  // `${nodeId}::${portId}` so a single select can pick a member's port.
  const portOptions = [...members].flatMap((nodeId) => {
    const n = nodeById.get(nodeId);
    const def = n ? defById.get(n.machineDefId) : undefined;
    return portsForDef(def as MachineDef | undefined).map((p) => ({
      value: `${nodeId}::${p.id}`,
      label: `${nodeLabel(nodeId)} · ${p.id}`,
    }));
  });

  const setPorts = (ports: SystemPort[]) =>
    onChange(withField(system, "exposedPorts", ports));
  const exposed = system.exposedPorts ?? [];

  const addPort = () => {
    const first = portOptions[0];
    if (!first) return;
    const [nodeId, port] = first.value.split("::");
    setPorts([...exposed, { nodeId, port }]);
  };
  const setPortTarget = (i: number, value: string) => {
    const [nodeId, port] = value.split("::");
    setPorts(exposed.map((ep, j) => (j === i ? { ...ep, nodeId, port } : ep)));
  };
  const setPortName = (i: number, name: string) =>
    setPorts(
      exposed.map((ep, j) =>
        j === i ? { ...ep, name: name || undefined } : ep,
      ),
    );
  const removePort = (i: number) => setPorts(exposed.filter((_, j) => j !== i));

  return (
    <div className="inspector-body">
      <Field label="id">
        <StaticValue value={system.id} />
      </Field>
      <Field label="name">
        <TextInput
          value={system.name ?? ""}
          onChange={(v) => onChange(withField(system, "name", v))}
          placeholder="unnamed system"
        />
      </Field>

      <Field label="collapsed">
        <Checkbox
          checked={system.collapsed ?? false}
          onChange={(v) => onChange(withField(system, "collapsed", v))}
          label="fold members into one node"
        />
      </Field>

      <Field label={`member nodes (${members.size})`}>
        {nodes.length === 0 ? (
          <span className="stack-empty">no nodes in the world</span>
        ) : (
          <>
            {members.size > 0 && (
              <div className="chip-list">
                {[...members].map((id) => (
                  <span key={id} className="chip mono">
                    {nodeLabel(id)}
                    <button
                      type="button"
                      className="chip-x"
                      title="Remove member"
                      onClick={() => toggleMember(id, false)}
                    >
                      ×
                    </button>
                  </span>
                ))}
              </div>
            )}
            <Combobox
              value=""
              onChange={(id) => toggleMember(id, true)}
              options={
                nodes
                  .filter((n) => !members.has(n.id))
                  .map((n) => ({
                    value: n.id,
                    label: nodeLabel(n.id),
                  })) as ComboOption[]
              }
              placeholder="+ add a member node…"
              dense
            />
          </>
        )}
      </Field>

      {subCandidates.length > 0 && (
        <Field label={`nested systems (${memberSystems.size})`} hint="group systems within this one">
          <div className="check-list">
            {subCandidates.map((s) => (
              <Checkbox
                key={s.id}
                checked={memberSystems.has(s.id)}
                onChange={(on) => toggleSubSystem(s.id, on)}
                label={`${s.name ?? s.id}  ·  ${s.id}`}
              />
            ))}
          </div>
        </Field>
      )}

      <Field label={`exposed ports (${exposed.length})`} hint="ports connectable while collapsed">
        <div className="stack-list">
          {exposed.length === 0 && (
            <span className="stack-empty">none — the folded node has no ports to wire</span>
          )}
          {exposed.map((ep, i) => (
            <div key={i} className="exposed-row">
              <Select
                value={ep.port ? `${ep.nodeId}::${ep.port}` : ""}
                onChange={(v) => setPortTarget(i, v)}
                options={portOptions}
                placeholder="port…"
              />
              <input
                className="field-input"
                type="text"
                value={ep.name ?? ""}
                placeholder="label"
                onChange={(e) => setPortName(i, e.target.value)}
              />
              <button
                type="button"
                className="row-del"
                title="Remove exposed port"
                onClick={() => removePort(i)}
              >
                ×
              </button>
            </div>
          ))}
          <button
            type="button"
            className="row-add"
            onClick={addPort}
            disabled={portOptions.length === 0}
          >
            + expose a port
          </button>
        </div>
      </Field>
    </div>
  );
}

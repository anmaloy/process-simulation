// Pipe inspector (WORKFLOW.md P7). The graph edge carries one category between
// two machine ports: movement/storage capacity, overclock, and two ends each
// with a role and priorities.
//
// This is the canonical applicability case: an end's *insert* priority applies
// only when it inserts (role in or io); its *extract* priority only when it
// extracts (role out or io). An `in`-only end shows no extract priority at all —
// the row isn't mounted, and flipping a role clears the now-inapplicable term.
// Priorities are signed (−128..127), default 0, and optional (unset ≠ 0).

import type { Category, Pipe, PipeEnd, PipeEndRole, World } from "../bridge";
import { categoryColor } from "../canvas";
import {
  Field,
  NumberInput,
  OptionalNumber,
  Select,
  StaticValue,
  withField,
  withoutField,
} from "./fields";

const ROLE_OPTIONS = [
  { value: "in", label: "in (insert only)" },
  { value: "out", label: "out (extract only)" },
  { value: "io", label: "io (both)" },
];

const inserts = (role: PipeEndRole) => role === "in" || role === "io";
const extracts = (role: PipeEndRole) => role === "out" || role === "io";

function nodeLabel(world: World, nodeId: string): string {
  const node = world.nodes?.find((n) => n.id === nodeId);
  const def = world.machineDefs?.find((d) => d.id === node?.machineDefId);
  return def?.name ?? node?.machineDefId ?? nodeId;
}

function PipeEndEditor({
  title,
  end,
  world,
  onChange,
}: {
  title: string;
  end: PipeEnd;
  world: World;
  onChange: (next: PipeEnd) => void;
}) {
  const setRole = (role: PipeEndRole) => {
    let next = withField(end, "role", role);
    // Drop any priority the new role no longer applies.
    if (!inserts(role)) next = withoutField(next, "insertPriority");
    if (!extracts(role)) next = withoutField(next, "extractPriority");
    onChange(next);
  };

  return (
    <div className="pipe-end">
      <div className="pipe-end-head mono">
        {title} · <span className="dim">{nodeLabel(world, end.nodeId)}</span>
      </div>
      <Field label="node">
        <StaticValue value={end.nodeId} dim />
      </Field>
      {end.port && (
        <Field label="port">
          <StaticValue value={end.port} dim />
        </Field>
      )}
      <Field label="role">
        <Select value={end.role} onChange={(v) => setRole(v as PipeEndRole)} options={ROLE_OPTIONS} />
      </Field>
      {inserts(end.role) && (
        <OptionalNumber
          label="insert priority"
          value={end.insertPriority}
          onSet={(v) => onChange(withField(end, "insertPriority", v))}
          onClear={() => onChange(withoutField(end, "insertPriority"))}
          min={-128}
          max={127}
          step={1}
          hint="higher fills first"
        />
      )}
      {extracts(end.role) && (
        <OptionalNumber
          label="extract priority"
          value={end.extractPriority}
          onSet={(v) => onChange(withField(end, "extractPriority", v))}
          onClear={() => onChange(withoutField(end, "extractPriority"))}
          min={-128}
          max={127}
          step={1}
          hint="lower drains first"
        />
      )}
    </div>
  );
}

export function PipeInspector({
  pipe,
  world,
  onChange,
}: {
  pipe: Pipe;
  world: World;
  onChange: (next: Pipe) => void;
}) {
  return (
    <div className="inspector-body">
      <Field label="id">
        <StaticValue value={pipe.id} />
      </Field>
      <Field label="category">
        <span className="cat-static">
          <span className="legend-dot" style={{ background: categoryColor(pipe.category as Category) }} />
          <StaticValue value={pipe.category} />
        </span>
      </Field>
      <Field label="movement capacity" hint="caps sustained flow (× overclock)">
        <NumberInput
          value={pipe.movementCapacity}
          onChange={(v) => onChange(withField(pipe, "movementCapacity", v))}
          min={0}
        />
      </Field>
      <OptionalNumber
        label="storage capacity"
        value={pipe.storageCapacity}
        onSet={(v) => onChange(withField(pipe, "storageCapacity", v))}
        onClear={() => onChange(withoutField(pipe, "storageCapacity"))}
        min={0}
        hint="buffers transients only"
      />
      <OptionalNumber
        label="overclock"
        value={pipe.overclock}
        onSet={(v) => onChange(withField(pipe, "overclock", v))}
        onClear={() => onChange(withoutField(pipe, "overclock"))}
        fallback={1}
        min={0}
        step={0.25}
        hint="scales movement capacity"
      />

      <PipeEndEditor
        title="end A"
        end={pipe.a}
        world={world}
        onChange={(next) => onChange(withField(pipe, "a", next))}
      />
      <PipeEndEditor
        title="end B"
        end={pipe.b}
        world={world}
        onChange={(next) => onChange(withField(pipe, "b", next))}
      />
    </div>
  );
}

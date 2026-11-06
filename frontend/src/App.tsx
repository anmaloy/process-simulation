// App shell (WORKFLOW.md P5) hosting the canvas (P6) and the docked inspector
// (P7).
//
// The bridge connects once; the World it returns is the single source of truth.
// Two selection surfaces feed one inspector: canvas clicks select a node or
// pipe, rail lists select a library entity (item / recipe / machine def). Every
// field edit and every structural gesture goes through the P4 `apply_edit` API
// and is followed by a `recompute`, so edits persist and the balance refreshes.
// The returned World is stored and the canvas reseeds from it.

import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiClient, type ApiSource } from "./bridge";
import type {
  Edit,
  EntityKind,
  MachineNode,
  Pipe,
  RecomputeRequest,
  Scope,
  ScopeReport,
  World,
} from "./bridge";
import { Canvas, categoryColor, type CanvasSelection } from "./canvas";
import { Inspector, type InspectorSelection } from "./panels";
import { BalanceReadout, Legends } from "./overlay";
import { centroidOf, collapseState, freshSystemId } from "./systems";
import type { System } from "./bridge";
import "./canvas/canvas.css";
import "./panels/panels.css";
import "./overlay/overlay.css";

type Conn =
  | { phase: "connecting" }
  | { phase: "ready"; source: ApiSource }
  | { phase: "error"; message: string };

type Selection = InspectorSelection | null;

export function App() {
  const [conn, setConn] = useState<Conn>({ phase: "connecting" });
  const [client, setClient] = useState<ApiClient | null>(null);
  const [world, setWorld] = useState<World | null>(null);
  const [selection, setSelection] = useState<Selection>(null);
  const [report, setReport] = useState<ScopeReport | null>(null);
  // Per-collapsed-system reports, so a folded system node shows its own
  // aggregate stats (net energy, status) without expanding it.
  const [systemReports, setSystemReports] = useState<Record<string, ScopeReport>>({});
  // The scope the balance overlay reports on (default: the whole world). A
  // machine/process scope targets the selected node; a system scope targets a
  // chosen system.
  const [scope, setScope] = useState<Scope>("world");
  const [systemId, setSystemId] = useState<string | null>(null);

  const selectedNodeId = selection?.kind === "node" ? selection.id : null;

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const c = await ApiClient.connect();
        const w = await c.getWorld();
        if (!live) return;
        setClient(c);
        setWorld(w);
        setConn({ phase: "ready", source: c.source });
      } catch (e) {
        if (live) {
          setConn({
            phase: "error",
            message: e instanceof Error ? e.message : String(e),
          });
        }
      }
    })();
    return () => {
      live = false;
    };
  }, []);

  // Apply a sequence of edits and store the resulting World. The balance
  // recompute is driven by an effect keyed on the World + scope, so every edit
  // (which replaces the World) refreshes the overlay without duplicating the
  // recompute logic here.
  const applyEdits = useCallback(
    async (edits: Edit[]) => {
      if (!client || edits.length === 0) return;
      try {
        let next: World | null = null;
        for (const edit of edits) next = await client.applyEdit(edit);
        if (next) setWorld(next);
      } catch (e) {
        setConn({
          phase: "error",
          message: e instanceof Error ? e.message : String(e),
        });
      }
    },
    [client],
  );

  // The recompute request for the current scope + target, or null when the
  // chosen scope has no target yet (a machine/process scope with nothing
  // selected, or a system scope with no system picked).
  const recomputeReq = useMemo<RecomputeRequest | null>(() => {
    switch (scope) {
      case "world":
        return { scope: "world" };
      case "node":
        return selectedNodeId ? { scope: "node", nodeId: selectedNodeId } : null;
      case "process":
        return selectedNodeId ? { scope: "process", nodeId: selectedNodeId } : null;
      case "system":
        return systemId ? { scope: "system", systemId } : null;
      default:
        return null;
    }
  }, [scope, selectedNodeId, systemId]);
  // A primitive key so the recompute effect refires on a real target change but
  // not on an unrelated selection change (e.g. clicking a node in world scope).
  const reqKey = recomputeReq
    ? `${recomputeReq.scope}:${recomputeReq.nodeId ?? ""}:${recomputeReq.systemId ?? ""}`
    : "none";

  // Recompute whenever the World changes (after any edit) or the scope target
  // changes. An absent target clears the report so nothing is fabricated.
  useEffect(() => {
    if (!client) return;
    if (!recomputeReq) {
      setReport(null);
      return;
    }
    let live = true;
    client
      .recompute(recomputeReq)
      .then((r) => {
        if (live) setReport(r);
      })
      .catch(() => {
        if (live) setReport(null);
      });
    return () => {
      live = false;
    };
    // recomputeReq is captured via reqKey (a stable primitive); world drives the
    // post-edit refresh.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, world, reqKey]);

  // Compute a report per collapsed system so its folded node can show its own
  // aggregate stats. Runs on any World change; keyed on the set of rendered
  // (outermost) collapsed system ids.
  const collapsedKey = world
    ? collapseState(world)
        .renderedSystems.map((s) => s.id)
        .sort()
        .join(",")
    : "";
  useEffect(() => {
    if (!client || !world) {
      setSystemReports({});
      return;
    }
    const ids = collapseState(world).renderedSystems.map((s) => s.id);
    if (ids.length === 0) {
      setSystemReports({});
      return;
    }
    let live = true;
    Promise.all(
      ids.map((id) =>
        client
          .recompute({ scope: "system", systemId: id })
          .then((r) => [id, r] as const)
          .catch(() => null),
      ),
    ).then((pairs) => {
      if (!live) return;
      const next: Record<string, ScopeReport> = {};
      for (const pair of pairs) if (pair) next[pair[0]] = pair[1];
      setSystemReports(next);
    });
    return () => {
      live = false;
    };
    // collapsedKey captures which systems need a report; world drives refresh.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, world, collapsedKey]);

  // Selecting a system aims the system scope at it (convenience, so the readout
  // follows what you clicked without re-picking in the switcher).
  useEffect(() => {
    if (selection?.kind === "system") setSystemId(selection.id);
  }, [selection]);

  // Switching scope: default a system scope to the first system if none picked.
  const onScopeChange = useCallback(
    (next: Scope) => {
      if (next === "system" && !systemId) {
        const first = world?.systems?.[0]?.id ?? null;
        setSystemId(first);
      }
      setScope(next);
    },
    [systemId, world],
  );

  const upsert = (kind: EntityKind, entity: object): Edit => ({
    op: "upsert",
    kind,
    entity: entity as Record<string, unknown>,
  });
  const remove = (kind: EntityKind, id: string): Edit => ({ op: "remove", kind, id });

  const onAddNode = useCallback(
    (node: MachineNode) => applyEdits([upsert("node", node)]),
    [applyEdits],
  );

  const onMoveNode = useCallback(
    (id: string, x: number, y: number) => {
      const current = world?.nodes?.find((n) => n.id === id);
      if (!current) return;
      void applyEdits([upsert("node", { ...current, x, y })]);
    },
    [applyEdits, world],
  );

  const onAddPipe = useCallback(
    (pipe: Pipe) => applyEdits([upsert("pipe", pipe)]),
    [applyEdits],
  );

  const onRemoveNodes = useCallback(
    (ids: string[]) => {
      setSelection((s) => (s?.kind === "node" && ids.includes(s.id) ? null : s));
      return applyEdits(ids.map((id) => remove("node", id)));
    },
    [applyEdits],
  );

  const onRemovePipes = useCallback(
    (ids: string[]) => {
      setSelection((s) => (s?.kind === "pipe" && ids.includes(s.id) ? null : s));
      return applyEdits(ids.map((id) => remove("pipe", id)));
    },
    [applyEdits],
  );

  // Group selected machine nodes into a new, collapsed system placed at their
  // centroid; select it so its inspector opens.
  const onGroupNodes = useCallback(
    (nodeIds: string[]) => {
      if (!world || nodeIds.length === 0) return;
      const id = freshSystemId(world);
      const { x, y } = centroidOf(world, nodeIds);
      const system: System = {
        id,
        memberNodeIds: nodeIds,
        collapsed: true,
        x,
        y,
      };
      void applyEdits([upsert("system", system)]);
      setSelection({ kind: "system", id });
    },
    [applyEdits, world],
  );

  const onToggleCollapse = useCallback(
    (systemId: string) => {
      const sys = world?.systems?.find((s) => s.id === systemId);
      if (!sys) return;
      void applyEdits([upsert("system", { ...sys, collapsed: !sys.collapsed })]);
    },
    [applyEdits, world],
  );

  const onMoveSystem = useCallback(
    (systemId: string, x: number, y: number) => {
      const sys = world?.systems?.find((s) => s.id === systemId);
      if (!sys) return;
      void applyEdits([upsert("system", { ...sys, x, y })]);
    },
    [applyEdits, world],
  );

  const onRemoveSystems = useCallback(
    (ids: string[]) => {
      setSelection((s) => (s?.kind === "system" && ids.includes(s.id) ? null : s));
      return applyEdits(ids.map((id) => remove("system", id)));
    },
    [applyEdits],
  );

  // Inspector edit: upsert the entity and recompute.
  const onEntityChange = useCallback(
    (kind: EntityKind, entity: object) => applyEdits([upsert(kind, entity)]),
    [applyEdits],
  );

  const onEntityDelete = useCallback(
    (kind: EntityKind, id: string) => {
      setSelection(null);
      void applyEdits([remove(kind, id)]);
    },
    [applyEdits],
  );

  // The canvas knows node/pipe/system selections; narrow for it.
  const canvasSelection: CanvasSelection =
    selection &&
    (selection.kind === "node" || selection.kind === "pipe" || selection.kind === "system")
      ? { kind: selection.kind, id: selection.id }
      : null;

  return (
    <div className={`shell ${selection ? "has-inspector" : ""}`}>
      <aside className="rail">
        <div className="brand">
          <p className="eyebrow">Production Chain Planner</p>
          <h1>Process Simulation</h1>
          <div className="subtitle">rate-balance · node graph</div>
        </div>
        <div className="rail-scroll">
          <div className="rail-head row">
            <SourcePill conn={conn} />
          </div>
          <Library world={world} selection={selection} onSelect={setSelection} />
        </div>
      </aside>

      <main className="stage">
        <Stage
          conn={conn}
          world={world}
          selection={canvasSelection}
          report={report}
          systemReports={systemReports}
          scope={scope}
          onScopeChange={onScopeChange}
          selectedNodeId={selectedNodeId}
          systemId={systemId}
          onSystemChange={setSystemId}
          onAddNode={onAddNode}
          onMoveNode={onMoveNode}
          onAddPipe={onAddPipe}
          onRemoveNodes={onRemoveNodes}
          onRemovePipes={onRemovePipes}
          onGroupNodes={onGroupNodes}
          onToggleCollapse={onToggleCollapse}
          onMoveSystem={onMoveSystem}
          onRemoveSystems={onRemoveSystems}
          onSelect={(s) => setSelection(s)}
        />
      </main>

      {selection && world && (
        <div className="inspector-dock">
          <Inspector
            world={world}
            selection={selection}
            onChange={onEntityChange}
            onDelete={onEntityDelete}
            onClose={() => setSelection(null)}
          />
        </div>
      )}
    </div>
  );
}

function SourcePill({ conn }: { conn: Conn }) {
  if (conn.phase !== "ready") return null;
  const dev = conn.source === "dev";
  return (
    <span className={`pill ${dev ? "dev" : "live"}`} style={{ marginLeft: "auto" }}>
      {dev ? "dev fixture" : "pywebview"}
    </span>
  );
}

function Library({
  world,
  selection,
  onSelect,
}: {
  world: World | null;
  selection: Selection;
  onSelect: (s: Selection) => void;
}) {
  const defName = useMemo(() => {
    const m = new Map<string, string>();
    for (const d of world?.machineDefs ?? []) m.set(d.id, d.name ?? d.id);
    return m;
  }, [world]);

  if (!world) return <div className="leaf">—</div>;

  const isSel = (kind: EntityKind, id: string) =>
    selection?.kind === kind && selection.id === id;

  const CATEGORY_DOT: Record<string, string> = {
    solid: categoryColor("solid"),
    liquid: categoryColor("liquid"),
    energy: categoryColor("energy"),
  };

  return (
    <>
      <Section title="Nodes" empty={(world.nodes ?? []).length === 0}>
        {(world.nodes ?? []).map((n) => (
          <Leaf
            key={n.id}
            label={defName.get(n.machineDefId) ?? n.machineDefId}
            id={n.id}
            dot="var(--s-not)"
            selected={isSel("node", n.id)}
            onClick={() => onSelect({ kind: "node", id: n.id })}
          />
        ))}
      </Section>

      <Section title="Systems" empty={(world.systems ?? []).length === 0}>
        {(world.systems ?? []).map((s) => (
          <Leaf
            key={s.id}
            label={s.name ?? s.id}
            id={s.id}
            dot="var(--s-expert)"
            selected={isSel("system", s.id)}
            onClick={() => onSelect({ kind: "system", id: s.id })}
          />
        ))}
      </Section>

      <Section title="Items" empty={(world.items ?? []).length === 0}>
        {(world.items ?? []).map((it) => (
          <Leaf
            key={it.id}
            label={it.name ?? it.id}
            id={it.id}
            dot={CATEGORY_DOT[it.category] ?? "var(--ink-faint)"}
            selected={isSel("item", it.id)}
            onClick={() => onSelect({ kind: "item", id: it.id })}
          />
        ))}
      </Section>

      <Section title="Recipes" empty={(world.recipes ?? []).length === 0}>
        {(world.recipes ?? []).map((r) => (
          <Leaf
            key={r.id}
            label={r.name ?? r.id}
            id={r.id}
            dot="var(--subedge)"
            selected={isSel("recipe", r.id)}
            onClick={() => onSelect({ kind: "recipe", id: r.id })}
          />
        ))}
      </Section>

      <Section title="Machine defs" empty={(world.machineDefs ?? []).length === 0}>
        {(world.machineDefs ?? []).map((d) => (
          <Leaf
            key={d.id}
            label={d.name ?? d.id}
            id={d.id}
            dot="var(--s-teach)"
            selected={isSel("machineDef", d.id)}
            onClick={() => onSelect({ kind: "machineDef", id: d.id })}
          />
        ))}
      </Section>
    </>
  );
}

function Section({
  title,
  empty,
  children,
}: {
  title: string;
  empty: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="rail-section">
      <p className="eyebrow quiet">{title}</p>
      {empty ? <div className="leaf">none</div> : children}
    </div>
  );
}

function Leaf({
  label,
  id,
  dot,
  selected,
  onClick,
}: {
  label: string;
  id: string;
  dot: string;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <div className={`leaf clickable ${selected ? "selected" : ""}`} onClick={onClick}>
      <span className="dot" style={{ background: dot }} />
      <span>{label}</span>
      <span className="leaf-id">{id}</span>
    </div>
  );
}

interface StageProps {
  conn: Conn;
  world: World | null;
  selection: CanvasSelection;
  report: ScopeReport | null;
  systemReports: Record<string, ScopeReport>;
  scope: Scope;
  onScopeChange: (scope: Scope) => void;
  selectedNodeId: string | null;
  systemId: string | null;
  onSystemChange: (systemId: string) => void;
  onAddNode: (node: MachineNode) => void;
  onMoveNode: (id: string, x: number, y: number) => void;
  onAddPipe: (pipe: Pipe) => void;
  onRemoveNodes: (ids: string[]) => void;
  onRemovePipes: (ids: string[]) => void;
  onGroupNodes: (nodeIds: string[]) => void;
  onToggleCollapse: (systemId: string) => void;
  onMoveSystem: (systemId: string, x: number, y: number) => void;
  onRemoveSystems: (ids: string[]) => void;
  onSelect: (sel: CanvasSelection) => void;
}

function Stage(props: StageProps) {
  const { conn, world } = props;

  if (conn.phase === "connecting") {
    return (
      <section className="panel-glass readout">
        <p className="eyebrow">Bridge</p>
        <h2>Connecting…</h2>
        <p className="lede">Resolving the pywebview API surface.</p>
      </section>
    );
  }
  if (conn.phase === "error") {
    return (
      <section className="panel-glass readout">
        <div className="row">
          <p className="eyebrow" style={{ margin: 0 }}>
            Bridge
          </p>
          <span className="pill bad">error</span>
        </div>
        <h2>Could not reach the World</h2>
        <p className="lede mono">{conn.message}</p>
      </section>
    );
  }
  if (!world) return null;

  return (
    <>
      <Legends />
      <Canvas
        world={world}
        selection={props.selection}
        report={props.report}
        systemReports={props.systemReports}
        onAddNode={props.onAddNode}
        onMoveNode={props.onMoveNode}
        onAddPipe={props.onAddPipe}
        onRemoveNodes={props.onRemoveNodes}
        onRemovePipes={props.onRemovePipes}
        onGroupNodes={props.onGroupNodes}
        onToggleCollapse={props.onToggleCollapse}
        onMoveSystem={props.onMoveSystem}
        onRemoveSystems={props.onRemoveSystems}
        onSelect={props.onSelect}
      />
      <BalanceReadout
        world={world}
        report={props.report}
        scope={props.scope}
        onScopeChange={props.onScopeChange}
        selectedNodeId={props.selectedNodeId}
        systemId={props.systemId}
        onSystemChange={props.onSystemChange}
      />
    </>
  );
}

// App shell (WORKFLOW.md P5) hosting the canvas (P6) and the docked inspector
// (P7).
//
// The bridge connects once; the World it returns is the single source of truth.
// Two selection surfaces feed one inspector: canvas clicks select a node or
// pipe, rail lists select a library entity (item / recipe / machine def). Every
// field edit and every structural gesture goes through the P4 `apply_edit` API
// and is followed by a `recompute`, so edits persist and the balance refreshes.
// The returned World is stored and the canvas reseeds from it.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import { Canvas, categoryColor, DEF_DRAG_MIME, type CanvasSelection } from "./canvas";
import { Inspector, type InspectorSelection } from "./panels";
import { BalanceReadout, Legends } from "./overlay";
import { centroidOf, collapseState, freshSystemId } from "./systems";
import { idFromName, renameEntity } from "./ui";
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
  // Undo stack of prior World snapshots (#12 / Ctrl+Z). Bounded so it can't grow
  // without limit; the newest snapshot is last.
  const history = useRef<World[]>([]);
  const [canUndo, setCanUndo] = useState(false);

  const selectedNodeId = selection?.kind === "node" ? selection.id : null;

  const snapshot = useCallback((w: World | null) => {
    if (!w) return;
    const stack = history.current;
    stack.push(w);
    if (stack.length > 100) stack.shift();
    setCanUndo(stack.length > 0);
  }, []);

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
        snapshot(world);
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
    [client, world, snapshot],
  );

  // Replace the whole World (used by rename and undo), snapshotting first unless
  // the caller is restoring a snapshot itself.
  const replaceWorld = useCallback(
    async (next: World, record = true) => {
      if (!client) return;
      try {
        if (record) snapshot(world);
        const normalized = await client.setWorld(next);
        setWorld(normalized);
      } catch (e) {
        setConn({
          phase: "error",
          message: e instanceof Error ? e.message : String(e),
        });
      }
    },
    [client, world, snapshot],
  );

  const undo = useCallback(() => {
    const prev = history.current.pop();
    setCanUndo(history.current.length > 0);
    if (prev) void replaceWorld(prev, false);
  }, [replaceWorld]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.ctrlKey || e.metaKey;
      if (mod && !e.shiftKey && (e.key === "z" || e.key === "Z")) {
        const el = e.target as HTMLElement | null;
        const tag = el?.tagName;
        // Let text inputs keep their own native undo.
        if (tag === "INPUT" || tag === "TEXTAREA") return;
        e.preventDefault();
        undo();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [undo]);

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

  // Create a fresh library primitive (item / recipe / machine def) with a unique
  // id and the minimum valid fields, then select it so its inspector opens for
  // configuration. The engine tolerates an unreferenced new entity, so the
  // post-edit recompute stays safe. Nodes come from the canvas "Add machine"
  // palette and systems from grouping, so only the three library kinds are made
  // here.
  const onCreateEntity = useCallback(
    (kind: "item" | "recipe" | "machineDef") => {
      if (!world) return;
      const name =
        kind === "item" ? "New item" : kind === "recipe" ? "New recipe" : "New machine";
      const id = idFromName(world, kind, name);
      const entity =
        kind === "item"
          ? { id, name, category: "solid" as const }
          : kind === "recipe"
            ? { id, name, timeSeconds: 1 }
            : { id, name };
      void applyEdits([upsert(kind, entity)]);
      setSelection({ kind, id });
    },
    [applyEdits, world],
  );

  // Rename a library entity's id, rewriting every reference, then re-point the
  // selection at the new id so the inspector stays open on it (#3).
  const onRenameEntity = useCallback(
    (kind: EntityKind, oldId: string, newId: string) => {
      if (!world || oldId === newId) return;
      void replaceWorld(renameEntity(world, kind, oldId, newId));
      setSelection((s) => (s && s.kind === kind && s.id === oldId ? { kind, id: newId } : s));
    },
    [replaceWorld, world],
  );

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
            <button
              type="button"
              className="tool-btn undo-btn"
              onClick={undo}
              disabled={!canUndo}
              title="Undo (Ctrl+Z)"
            >
              ↶ Undo
            </button>
          </div>
          <Library
            world={world}
            selection={selection}
            onSelect={setSelection}
            onCreate={onCreateEntity}
          />
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
            onRename={onRenameEntity}
            onDelete={onEntityDelete}
            onClose={() => setSelection(null)}
          />
        </div>
      )}
    </div>
  );
}

function Library({
  world,
  selection,
  onSelect,
  onCreate,
}: {
  world: World | null;
  selection: Selection;
  onSelect: (s: Selection) => void;
  onCreate: (kind: "item" | "recipe" | "machineDef") => void;
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

  // Sort each rail list A→Z by its display label (#9).
  const cmp = (a: string, b: string) =>
    a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });
  const nodes = [...(world.nodes ?? [])].sort((a, b) =>
    cmp(defName.get(a.machineDefId) ?? a.machineDefId, defName.get(b.machineDefId) ?? b.machineDefId),
  );
  const systems = [...(world.systems ?? [])].sort((a, b) => cmp(a.name ?? a.id, b.name ?? b.id));
  const items = [...(world.items ?? [])].sort((a, b) => cmp(a.name ?? a.id, b.name ?? b.id));
  const recipes = [...(world.recipes ?? [])].sort((a, b) => cmp(a.name ?? a.id, b.name ?? b.id));
  const machineDefs = [...(world.machineDefs ?? [])].sort((a, b) =>
    cmp(a.name ?? a.id, b.name ?? b.id),
  );

  return (
    <>
      <Section title="Nodes" empty={(world.nodes ?? []).length === 0}>
        {nodes.map((n) => (
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
        {systems.map((s) => (
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

      <Section
        title="Items"
        empty={(world.items ?? []).length === 0}
        onAdd={() => onCreate("item")}
      >
        {items.map((it) => (
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

      <Section
        title="Recipes"
        empty={(world.recipes ?? []).length === 0}
        onAdd={() => onCreate("recipe")}
      >
        {recipes.map((r) => (
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

      <Section
        title="Machine defs"
        empty={(world.machineDefs ?? []).length === 0}
        onAdd={() => onCreate("machineDef")}
      >
        {machineDefs.map((d) => (
          <Leaf
            key={d.id}
            label={d.name ?? d.id}
            id={d.id}
            dot="var(--s-teach)"
            selected={isSel("machineDef", d.id)}
            onClick={() => onSelect({ kind: "machineDef", id: d.id })}
            dragDefId={d.id}
          />
        ))}
      </Section>
    </>
  );
}

function Section({
  title,
  empty,
  onAdd,
  children,
}: {
  title: string;
  empty: boolean;
  onAdd?: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="rail-section">
      <div className="rail-section-head">
        <p className="eyebrow quiet">{title}</p>
        {onAdd && (
          <button
            type="button"
            className="rail-add"
            title={`New ${title.toLowerCase().replace(/s$/, "")}`}
            onClick={onAdd}
          >
            +
          </button>
        )}
      </div>
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
  dragDefId,
}: {
  label: string;
  id: string;
  dot: string;
  selected: boolean;
  onClick: () => void;
  /** When set, the leaf is draggable and carries this def id onto the canvas. */
  dragDefId?: string;
}) {
  return (
    <div
      className={`leaf clickable ${selected ? "selected" : ""} ${dragDefId ? "draggable" : ""}`}
      onClick={onClick}
      draggable={dragDefId != null}
      onDragStart={
        dragDefId != null
          ? (e) => {
              e.dataTransfer.setData(DEF_DRAG_MIME, dragDefId);
              e.dataTransfer.effectAllowed = "copy";
            }
          : undefined
      }
    >
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
        onSelectNode={(id) => props.onSelect({ kind: "node", id })}
      />
    </>
  );
}

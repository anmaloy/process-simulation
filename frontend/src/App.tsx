// App shell (WORKFLOW.md P5) now hosting the node-graph canvas (P6).
//
// The bridge connects once; the World it returns is the single source of truth.
// The canvas renders it and raises structural edits — place a machine, wire a
// pipe, move or delete a node — which go through the P4 `apply_edit` API. That
// call returns the updated World, which we store and the canvas reseeds from,
// so a moved/wired layout round-trips through the API into a loadable project.
// Panels/inspectors (P7) and the balance overlay (P8) layer on next.

import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiClient, type ApiSource } from "./bridge";
import type { Edit, EntityKind, MachineNode, Pipe, World } from "./bridge";
import { Canvas, categoryColor } from "./canvas";
import "./canvas/canvas.css";

type Conn =
  | { phase: "connecting" }
  | { phase: "ready"; source: ApiSource }
  | { phase: "error"; message: string };

type Selection = { kind: "node" | "pipe"; id: string } | null;

export function App() {
  const [conn, setConn] = useState<Conn>({ phase: "connecting" });
  const [client, setClient] = useState<ApiClient | null>(null);
  const [world, setWorld] = useState<World | null>(null);
  const [selection, setSelection] = useState<Selection>(null);

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

  // Apply a sequence of edits through the API; store the final World.
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
    (ids: string[]) => applyEdits(ids.map((id) => remove("node", id))),
    [applyEdits],
  );

  const onRemovePipes = useCallback(
    (ids: string[]) => applyEdits(ids.map((id) => remove("pipe", id))),
    [applyEdits],
  );

  return (
    <div className="shell">
      <aside className="rail">
        <div className="brand">
          <p className="eyebrow">Production Chain Planner</p>
          <h1>Process Simulation</h1>
          <div className="subtitle">rate-balance · node graph</div>
        </div>
        <div className="rail-section">
          <div className="row" style={{ padding: "0 22px", margin: "0 0 12px" }}>
            <p className="eyebrow quiet" style={{ margin: 0 }}>
              Nodes
            </p>
            <SourcePill conn={conn} />
          </div>
          <NodeList world={world} selection={selection} />
        </div>
      </aside>
      <main className="stage">
        <Stage
          conn={conn}
          world={world}
          onAddNode={onAddNode}
          onMoveNode={onMoveNode}
          onAddPipe={onAddPipe}
          onRemoveNodes={onRemoveNodes}
          onRemovePipes={onRemovePipes}
          onSelect={setSelection}
        />
      </main>
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

function NodeList({ world, selection }: { world: World | null; selection: Selection }) {
  const defName = useMemo(() => {
    const m = new Map<string, string>();
    for (const d of world?.machineDefs ?? []) m.set(d.id, d.name ?? d.id);
    return m;
  }, [world]);

  if (!world) return <div className="leaf">—</div>;
  const nodes = world.nodes ?? [];
  if (nodes.length === 0) return <div className="leaf">no nodes</div>;
  return (
    <>
      {nodes.map((n) => {
        const active = selection?.kind === "node" && selection.id === n.id;
        return (
          <div
            className="leaf"
            key={n.id}
            style={
              active
                ? { borderLeftColor: "var(--spine)", background: "var(--panel)" }
                : undefined
            }
          >
            <span className="dot" style={{ background: "var(--s-not)" }} />
            <span>{defName.get(n.machineDefId) ?? n.machineDefId}</span>
            <span className="leaf-id">{n.id}</span>
          </div>
        );
      })}
    </>
  );
}

interface StageProps {
  conn: Conn;
  world: World | null;
  onAddNode: (node: MachineNode) => void;
  onMoveNode: (id: string, x: number, y: number) => void;
  onAddPipe: (pipe: Pipe) => void;
  onRemoveNodes: (ids: string[]) => void;
  onRemovePipes: (ids: string[]) => void;
  onSelect: (sel: Selection) => void;
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
      <CategoryLegend />
      <Canvas
        world={world}
        onAddNode={props.onAddNode}
        onMoveNode={props.onMoveNode}
        onAddPipe={props.onAddPipe}
        onRemoveNodes={props.onRemoveNodes}
        onRemovePipes={props.onRemovePipes}
        onSelect={props.onSelect}
      />
    </>
  );
}

function CategoryLegend() {
  const cats: { c: "solid" | "liquid" | "energy"; label: string }[] = [
    { c: "solid", label: "solid" },
    { c: "liquid", label: "liquid" },
    { c: "energy", label: "energy" },
  ];
  return (
    <div className="category-legend">
      {cats.map(({ c, label }) => (
        <span className="legend-item mono" key={c}>
          <span className="legend-dot" style={{ background: categoryColor(c) }} />
          {label}
        </span>
      ))}
    </div>
  );
}

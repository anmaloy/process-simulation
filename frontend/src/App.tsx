// App shell (WORKFLOW.md P5) — rail + stage wearing the style tokens, booting
// the bridge and round-tripping one real `load World` call. The canvas (P6),
// panels (P7), and balance overlay (P8) layer onto this shell later; for now
// the stage is an instrument-style readout proving the bridge works against
// both the pywebview host and the dev fixture.

import { useEffect, useMemo, useState } from "react";
import { ApiClient, type ApiSource } from "./bridge";
import type { World } from "./bridge";

type Conn =
  | { phase: "connecting" }
  | { phase: "ready"; source: ApiSource; world: World }
  | { phase: "error"; message: string };

const CATEGORY_DOT: Record<string, string> = {
  solid: "var(--s-teach)",
  liquid: "var(--s-indep)",
  energy: "var(--subedge)",
};

export function App() {
  const [conn, setConn] = useState<Conn>({ phase: "connecting" });

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const client = await ApiClient.connect();
        const world = await client.getWorld();
        if (live) setConn({ phase: "ready", source: client.source, world });
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

  return (
    <div className="shell">
      <aside className="rail">
        <div className="brand">
          <p className="eyebrow">Production Chain Planner</p>
          <h1>Process Simulation</h1>
          <div className="subtitle">rate-balance · node graph</div>
        </div>
        <div className="rail-section">
          <p className="eyebrow quiet">Nodes</p>
          <NodeList conn={conn} />
        </div>
      </aside>
      <main className="stage">
        <Readout conn={conn} />
      </main>
    </div>
  );
}

function NodeList({ conn }: { conn: Conn }) {
  const world = conn.phase === "ready" ? conn.world : null;
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
      {nodes.map((n) => (
        <div className="leaf" key={n.id}>
          <span className="dot" style={{ background: "var(--s-not)" }} />
          <span>{defName.get(n.machineDefId) ?? n.machineDefId}</span>
          <span className="leaf-id">{n.id}</span>
        </div>
      ))}
    </>
  );
}

function Readout({ conn }: { conn: Conn }) {
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
        <h2>Could not load the World</h2>
        <p className="lede mono">{conn.message}</p>
      </section>
    );
  }

  const { world, source } = conn;
  const counts: [string, number][] = [
    ["Items", world.items?.length ?? 0],
    ["Recipes", world.recipes?.length ?? 0],
    ["Machine defs", world.machineDefs?.length ?? 0],
    ["Nodes", world.nodes?.length ?? 0],
    ["Pipes", world.pipes?.length ?? 0],
    ["Systems", world.systems?.length ?? 0],
  ];

  return (
    <section className="panel-glass readout">
      <div className="row">
        <p className="eyebrow" style={{ margin: 0 }}>
          World loaded
        </p>
        <span className={`pill ${source === "pywebview" ? "live" : "dev"}`}>
          {source === "pywebview" ? "pywebview host" : "dev fixture"}
        </span>
      </div>
      <h2>{world.name ?? "Untitled World"}</h2>
      <p className="lede">
        The bridge round-tripped a <span className="mono">get_world</span> call
        over the P4 API contract. The categorized item palette and node list come
        straight from that payload.
      </p>

      <div className="metrics">
        {counts.map(([label, value]) => (
          <div className="metric" key={label}>
            <div className="label">{label}</div>
            <div className="value">{value}</div>
          </div>
        ))}
      </div>

      <div style={{ marginTop: 18 }}>
        <p className="eyebrow quiet">Items</p>
        <div className="row" style={{ flexWrap: "wrap", gap: 8, marginBottom: 0 }}>
          {(world.items ?? []).map((it) => (
            <span className="pill" key={it.id}>
              <span
                className="dot"
                style={{
                  width: 7,
                  height: 7,
                  borderRadius: "50%",
                  background: CATEGORY_DOT[it.category] ?? "var(--ink-faint)",
                  display: "inline-block",
                }}
              />
              {it.name}
            </span>
          ))}
        </div>
      </div>

      {source === "dev" && (
        <div className="note warning" style={{ marginTop: 18 }}>
          Running against the dev fixture — the rate engine and file IO live in
          the Python host, so recompute and library calls return stand-ins until
          the app runs in the webview.
        </div>
      )}
    </section>
  );
}

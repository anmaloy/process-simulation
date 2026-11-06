# Workflow — Build Order

What gets implemented, in what order, and when each piece is "done." This is the
plan of record for turning the reference (`CLAUDE.md`), spec, and style guide
(`STYLE_GUIDE.md`) into a working app. When reality diverges, update this file.

## Sequencing principle

The architecture rules dictate the order, not preference:

- **The engine is pure Python and must be testable headless** (no UI, no
  pywebview). So the critical path is built bottom-up and verified before any
  pixels exist: **model → engine → io → api → frontend**. Each lower layer is
  fully tested before the layer above depends on it.
- **The frontend only reaches logic through the thin JS API.** The API surface
  is frozen (name + JSON shape per call) before the frontend consumes it, so UI
  work never reaches around it into engine internals.
- **The domain decisions are settled** (see [Decisions](#decisions-resolved)),
  but keep the code that depends on them (flow resolution, the fuel→fluid
  conversion, color maps) isolated behind small seams so any later tweak is a
  local edit, not a rewrite.
- **Vertical slice early.** Phases 0–4 produce a headless, fully-tested engine
  that loads a project, computes balances, and saves — provable value with zero
  UI. Phases 5+ put a face on it.

Dependency chain (→ = "must precede"):

```
P0 scaffold → P1 model → P2 engine → P3 io → P4 api → P5 frontend shell
                                                    → P6 canvas → P7 panels
                                                                → P8 balance overlay
                                                    P9 systems (engine + UI, after P2/P7)
                                                    P10 app shell/packaging (after P4/P5)
```

---

## Phase 0 — Scaffold & toolchain

**Goal:** a repo you can install, run, and test. Replace the "nothing is wired
up" state with real, documented commands.

**Builds:**
- `backend/` package layout per `CLAUDE.md` (`process_sim/{engine,model,io}`,
  `api.py`, `app.py`) with empty modules and `__init__` files.
- Python dependency manifest (`pyproject.toml`): pin Python 3.11+, `pywebview`,
  `pytest` (+ `pytest-cov`), a lint/format tool (ruff).
- `frontend/` via Vite (`react-ts` template): `package.json`, `tsconfig`,
  `@xyflow/react`. Do not build UI yet — just prove `npm run dev`/`build` work.
- `tests/` with one trivial passing test to anchor the runner.
- Dev commands wired and documented in `CLAUDE.md` (replace "Project state"):
  install, `pytest`, `npm run dev`, `npm run build`, lint.
- `.gitignore` already covers Python/Node; verify it holds (`dist/`,
  `node_modules/`, `__pycache__`, `.venv`).

**Done when:** `pytest` runs green, `npm run build` produces a bundle, and the
documented commands in `CLAUDE.md` are the real ones (no invented entry points).

**Notes:** no engine logic here. Resist scaffolding UI components — the webview
has nothing to host until P4/P5.

---

## Phase 1 — Domain model

**Goal:** the data model as pure dataclasses, with JSON round-trip. This is the
vocabulary every later layer speaks.

**Builds (`model/`):**
- `Item` (category: `solid|liquid|energy`, optional stored-energy `eu` fuel
  value), `Stack` (`{item, quantity}`),
  `Recipe` (input/output stacks, `timeSeconds`, optional energy prod/consume),
  `MachineDef` (energy behavior, fuel-burning flag + `efficiency`/waste for
  converters, allowed recipes, slot layout per category×direction, stack sizes,
  overclock bounds), `MachineNode` (recipe,
  overclock, parallel count), `Pipe` (category, two ports, storage cap, movement
  cap, overclock, per-end role + insert/extract priority), `System` (member
  nodes, exposed ports), `World` (items, recipes, defs, nodes, pipes, systems).
- Identity/reference strategy: entities carry stable ids; nodes/pipes/recipes
  reference items and defs **by id**, not by nesting, so World serializes flat.
- **Sparse fields are genuinely optional** (`None`/absent), never defaulted to
  zero — the model must represent "unset." This is load-bearing for the engine
  (it skips absent terms) and the UI (it hides absent fields).

**Depends on:** P0.

**Done when:** every entity constructs, a hand-built `World` serializes to JSON
and deserializes back identical (round-trip test), and unset optional fields
survive the round-trip as absent (not `0`). pytest covers each dataclass.

**Risks:** getting "unset ≠ zero" wrong here poisons every computation. Make the
round-trip test assert on absence explicitly.

---

## Phase 2 — Engine (the hard part)

**Goal:** headless rate-balance computation, fully tested. Four sub-steps, each
landed and tested before the next. All rates per second.

**P2a — Per-node computation.**
`cyclesPerSec = overclock / R.timeSeconds`;
`outputRate(s) = s.quantity * cyclesPerSec * count`; `inputDemand(s)` likewise.
Energy per node, **omitting unset terms**: a recipe energy term, if set,
**overrides** the machine-base term of the same kind (not additive) —
`consume = (recipe.energyConsumption or base) * overclock * count`, `produce`
likewise, `netEnergy = produce − consume`. **Fuel → fluid conversion:** a
fuel-burning node converts an input item's stored `eu` into an output fluid (a
liquid item — the engine doesn't name it; default content uses steam at
1 EU/unit). With max rate `R`, drawn fluid `D` (capped at `R`) and waste fraction
`w` (the machine's `efficiency`, default 0): `fluidOut = D`,
`fuelDrain = D + w·(R − D)`, `η = D / (D + w·(R − D))`. Default `w = 0` is
lossless 1:1; the Large Steam Boiler (default content) uses `w = 0.8`, `R = 256`.
Overclock is one linear scalar; clamp to def bounds for validation only.
*Done when:* rates and net energy are correct; a node with no base-energy field
contributes no energy term (not zero-substituted); a recipe term overrides base;
and boiler fuelDrain/fluidOut/η match hand calcs at `D = 0`, `D = R`, and a mid
load.

**P2b — Per-node balance.**
Per item: `shortfall = max(0, inputDemand − supplied)`,
`overflow = max(0, outputRate − demanded)`. Pure arithmetic given supplied/
demanded inputs.
*Done when:* balance table for a node matches hand calcs across OK/shortfall/
overflow cases.

**P2c — Pipe flow resolution — priority rules.** *Highest-risk module; isolate
it.* Each pipe flow is `min(upstream available, downstream demand, movementCap *
overclock)`; storage only buffers transients. Resolve contention per the
**asymmetric** priority rules (do not collapse into one rule):
- Insert contention: fill **descending** insert priority (higher first); equal →
  even split, batched by pipe movement capacity (speed).
- Extract contention: drain **ascending** extract priority (lower first); equal →
  even split.
- Self-loop guard: an `io` end won't re-insert what it pulled when insert < extract.
A converter's output fluid is just a liquid here — it flows under these same
rules, no special path.
*Done when:* all five canonical cases in `CLAUDE.md` pass as named tests
(furnace→two chests, triple even split, io filter no-reinsert, A-before-B drain,
chest@127 always first). Keep the comparator/batcher behind a seam (see gates).

**P2d — Aggregation / scopes.**
Scope = single machine | traced process | system | world. Per scope: sum
`netEnergy`; external inputs = items demanded but not supplied internally;
external outputs = items produced beyond internal demand; rank largest
shortfalls as bottlenecks. Full-buffer effects (full input pushes overflow
upstream; full output stalls the machine) handled here as steady-state states,
not a tick loop.
*Done when:* a multi-node chain reports correct external I/O, energy balance, and
bottleneck ranking; scope results compose (a world = sum of its processes).

**Depends on:** P1.

**Risks:** P2c (priority resolution) is the subtlest module — keep it swappable
and drive it entirely by the five canonical cases. This phase is the product —
budget accordingly and over-test it.

---

## Phase 3 — IO

**Goal:** project files and library exchange, built on P1's serialization.

**Builds (`io/`):** load/save a whole `World` to a single JSON file (the project
file); library import/export for reusable items/recipes/machine defs (a subset of
World). Schema versioning field so future format changes are detectable.

**Depends on:** P1 (uses the model's own JSON), ideally after P2 so fixtures are
real.

**Done when:** save→load a World is lossless (reuse P1's round-trip harness on
file I/O); a library exports and re-imports into a different World without id
collisions; a malformed/old-version file fails with a clear error, not a crash.

---

## Phase 4 — API surface (freeze the contract)

**Goal:** the thin pywebview JS API. This is the boundary; once frozen, the
frontend builds against it.

**Builds (`api.py`):** methods exposing load, save, get-World-as-JSON,
apply-edit, recompute-balances, import/export-library. **Marshaling only** — it
converts JSON↔model and calls engine/io; it holds no logic. Document each
method's name, arguments, and JSON return shape in `CLAUDE.md`.

**Depends on:** P2, P3.

**Done when:** every method is exercised by a Python-side test calling `api.py`
directly (no webview) with JSON in/out; the method list + shapes are documented
and treated as stable. A changed shape is a deliberate, documented contract bump.

**Notes:** keeping this thin is an architecture rule — if logic creeps in here,
move it down into engine/io.

> Milestone: end of P4 = a complete, headless, tested product. Loads a project,
> computes every scope's balance, saves. Everything after is presentation.

---

## Phase 5 — Frontend shell & bridge

**Goal:** the React app boots in the webview, wears the style system, and can
call the API.

**Builds (`frontend/`):**
- App shell: rail + stage grid (`300px 1fr`), background radial glow, from
  `STYLE_GUIDE.md` §10 starter.
- `styles/`: paste `:root` tokens verbatim (§2) as the **only** palette.
  **Bundle IBM Plex Mono + Space Grotesk as local `woff2` + `@font-face`** — no
  Google Fonts `@import` (offline-first).
- Bridge layer: a typed TS client over `window.pywebview.api` mirroring the P4
  method shapes; a dev fallback (static JSON fixture) so the UI runs in a plain
  browser (`npm run dev`) without the Python host.

**Depends on:** P4 (contract), P0 (Vite).

**Done when:** app renders the shell with correct tokens/fonts offline; the
bridge round-trips one real call (load World) in the webview and against the dev
fixture in a browser.

---

## Phase 6 — Canvas

**Goal:** the node graph — place, connect, pan/zoom.

**Builds (`canvas/`):** React Flow (`@xyflow/react`) with custom machine nodes
(`div` bodies), ports as handles (per category×direction from the MachineDef slot
layout), edges = pipes (SVG paths colored by category, thickness by flow, dashed
for shortfall). Minimap/pan/zoom from React Flow defaults. Node add/delete, edge
connect/delete, and persistence of positions into the World.

**Depends on:** P5.

**Done when:** a user can place machines, wire pipes, move/zoom, and the graph
state serializes through the API into a loadable project.

**Notes:** category colors (default, adjustable): solid → silver (`--s-teach`),
liquid → blue (`--s-indep`), energy → gold (`--subedge`); a converter's output
fluid rides the liquid color. Read from a single color-map constant, not
hardcoded per element.

---

## Phase 7 — Panels & inspectors

**Goal:** edit entity fields; **show only the fields an entity actually uses.**

**Builds (`panels/`):** inspectors/editors for item, recipe, machine def,
machine node, pipe end. Sparse-field discipline: a consumer machine shows no
energy-production field; an `in`-only pipe end shows no extract priority. Edits
go through the API and trigger recompute.

**Depends on:** P6 (selection), P4 (apply-edit).

**Done when:** selecting any element opens its inspector showing exactly its
present/applicable fields; edits persist and recompute; absent optional fields
render as "unset" (addable), never as `0`.

---

## Phase 8 — Balance visualization

**Goal:** make the engine's output legible on the canvas.

**Builds:** status styling on nodes/edges/ports — OK → green (`--s-ref`),
stalled/unpowered → grey (`--s-not`), shortfall/overflow per the decided mapping;
edge thickness by flow, dashed for shortfall; cyan glow (`--spine`) on the
selected/live element **only**. A legend and a per-scope readout panel (external
I/O, energy balance, bottleneck list from P2d). Scope switcher: machine / process
/ system / world.

**Depends on:** P7, P2d.

**Done when:** editing a chain updates status colors and the scope readout live;
the four scopes each display their aggregated stats correctly.

**Status: done.** `frontend/src/overlay/` holds the overlay. `status.ts` is the
single status color/label constant (node view + legends + readout read it);
`units.ts` is the display auto-scaling seam (decision #5). Nodes show status as a
bar + chip + faint ring; edges thicken by flow and dash when they feed a starved
node; cyan stays reserved for the selection. `BalanceReadout` carries the scope
switcher and the external-I/O / net-energy / bottleneck readout; recompute is
effect-driven in `App.tsx` (World or scope-target change → refresh; no target →
clear). The engine's `ScopeReport` gained a `pipeFlows` field (per-pipe flow +
starved flag) to drive the edge overlay — an additive contract bump mirrored in
the TS type and covered by pytest.

**Status colors (decided):** OK → green (`--s-ref`), shortfall → red (`--s-bad`,
added to the palette), overflow → yellow (`--s-learn`), stalled/unpowered → grey
(`--s-not`). Read them from a single color-map constant.

---

## Phase 9 — Systems (grouping)

**Goal:** user-defined groups treated as one unit.

**Builds:** engine support for a `System` as an aggregation scope with exposed
ports (lands with P2d, extended here); UI to select nodes → group, collapse/
expand, nest, and connect systems as single nodes. Aggregated stats on the
collapsed representation.

**Depends on:** P2d (aggregation), P7 (selection/editing).

**Done when:** a group collapses to one connectable node showing aggregate
stats; nesting works; expanding restores the members; balances are identical
collapsed vs expanded.

---

## Phase 10 — App shell & packaging

**Goal:** ship it as a single pywebview desktop process.

**Builds (`app.py`):** start pywebview, load the built frontend bundle from
disk (not a dev server), expose `api.py`. Offline asset check (fonts, scripts,
styles all local — no CDN). Cross-webview verification: `backdrop-filter` and
font smoothing on Chromium/WebView2 (Windows), WebKit (macOS/Linux); where
`backdrop-filter` is weak, fall back to solid translucent `--panel`. Package per
target OS.

**Depends on:** P4 (API), P5 (build output).

**Done when:** `python app.py` opens the app with the built frontend, all assets
load offline, and the glass effects either work or degrade cleanly on each target
webview.

---

## Phase 11 — Polish

Motion polish (§7), responsive breakpoint (820px drawer), app icon (§9), error
states, and perf on large graphs. Fold in any domain tweaks that surfaced during
the build via the isolated seams, updating `CLAUDE.md` + this file.

---

## Decisions (resolved)

The original open questions are all decided. Build to these. The seams still
matter so the EU model and color maps each live in one place.

| # | Question | Decision | Lands in |
|---|----------|----------|----------|
| 1 | Insert/extract priority asymmetry | Final as written — not revisable | P2c |
| 2 | Machine base vs recipe energy | Recipe term **overrides** base (per kind) | P2a |
| 3 | How EU is carried | Fuel item `eu` → an **output fluid** (a liquid item; steam in default content) → pipes under normal cap/priority | P1, P2a, P2c |
| 4 | Equal-priority round-robin batch | Batched by pipe movement capacity (existing param) | P2c |
| 5 | Units / display scaling | Store canonical units; UI auto-scales | P8 |
| — | Status colors | OK green, shortfall **red (`--s-bad`, added)**, overflow yellow, stalled/unpowered grey | P8 |
| — | Category colors | solid silver, liquid blue, energy gold — output fluids = liquid (adjustable) | P6 |

Isolate the fuel→fluid conversion (η, waste `w`) in one engine module, and both
color maps in one constant, so any later tweak stays local.

## Testing strategy

- **Engine/model/io (P1–P4):** pytest is the proof. The five canonical priority
  cases and the round-trip tests are the acceptance bar; no UI needed.
- **Fixtures:** build a few real `World` JSON fixtures during P2 (a furnace→chest
  chain, a powered multi-machine process) and reuse them in P3, P4, and the P5
  dev-fallback.
- **Frontend (P5+):** component tests added once the UI exists; the dev fixture
  keeps the frontend runnable without the Python host.

## Out of scope for v1

Tick-by-tick simulation with real backpressure, probabilistic recipe outputs
(use expected rates), multiplayer/cloud sync. Don't let these leak into P2.
</content>
</invoke>

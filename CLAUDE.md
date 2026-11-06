# Process Simulation — Production Chain Planner

A desktop app for designing and balancing production systems as node graphs.
Users define items, machines, recipes, and pipes, wire them into chains, and the
app reports supply vs. demand — shortfall, overflow, energy balance — for a
single machine, a connected process, a grouped system, or the whole world.

This file is the working reference for the project. The full functional spec was
provided separately and is not committed to the repo, so the parts that matter
for day-to-day work (the model, the formulas, the priority rules) are summarized
below. When the spec and this file disagree, treat the spec as authoritative and
update this file.

## Stack

- **Backend:** Python 3.11+. The rate-balance engine and data model are pure
  Python with no UI dependency.
- **Shell:** pywebview — the app runs as a single Python process hosting a system
  webview. No Electron/Chromium bundle and no Node server at runtime.
- **Frontend:** web UI built with Vite + React + TypeScript, rendered in the
  webview. Node canvas via React Flow (`@xyflow/react`), which provides pan/zoom,
  ports, edges, and a minimap out of the box.
- **Bridge:** the frontend calls the engine through pywebview's JS API
  (`window.pywebview.api`); World state crosses as JSON.
- **Persistence:** project files are JSON (the whole World), written by the Python
  side.
- **Tests:** pytest for the engine and model.

The styling target is edunode's CSS, which the webview renders directly — that is
the main reason for a web UI over a native toolkit (see `STYLE_GUIDE.md`).

## Project state

Phases 0–4 are done: the scaffold/toolchain (P0), the domain model with JSON
round-trip (P1, `model/`), the headless rate-balance engine (P2, `engine/`:
per-node rates + fuel→fluid conversion, per-node balance, asymmetric pipe-priority flow
resolution, and scope aggregation), project/library IO (P3, `io/`: versioned
project save/load and library export/import with id-collision handling), and the
pywebview JS API surface (P4, `api.py`: the frozen JSON bridge — see "API
surface" below) — all covered by pytest. This is the end-of-P4 milestone: a
complete, headless, tested product that loads a project, computes every scope's
balance, and saves. The frontend UI is still a stub — build it per the phase
order in `WORKFLOW.md`.

`io/` reads and writes **versioned envelopes**: every file is a JSON object with
`schemaVersion` (currently 1) and `kind` (`project` or `library`) wrapping its
payload. A project file nests the whole World under `"world"`; a library file
carries the reusable subset (items, recipes, machine defs) at top level.
`load_project`/`load_library` raise `MalformedFileError` (bad JSON, wrong kind,
garbled payload) or `UnsupportedVersionError` (version this build can't read) —
never an unhandled crash. `import_library` resolves id collisions by
`ConflictPolicy` (`rename` default, `skip`, `error`), rewriting intra-library
references, and never mutates the target World.

### API surface (P4 — frozen contract)

`api.py` exposes a single `Api` class; pywebview binds one long-lived instance
so the frontend calls each method as `window.pywebview.api.<method>(...)`.
Arguments arrive as plain JSON and return values serialize straight back. The
class is **marshaling only** — it converts JSON↔model and delegates to `engine/`
and `io/`; it holds no rate math, flow resolution, or file-format logic. The one
state it keeps is *the current World*; edits mutate it, reads and recomputes run
against it.

**Result envelope.** Every method returns `{"ok": true, "data": <payload>}` on
success or `{"ok": false, "error": {"type": "<ErrorName>", "message": "<str>"}}`
on an expected failure (malformed file, unreadable payload, bad request shape,
unknown entity kind, id collision under the `error` policy). Payloads are the
model's camelCase wire format.

| method | arguments | `data` payload |
|---|---|---|
| `get_world()` | — | World |
| `set_world(world)` | World dict | World (normalized) |
| `apply_edit(edit)` | `{op, kind, entity\|id}` | World after the edit |
| `load_project(path)` | path string | World |
| `save_project(path)` | path string | `{"path": str}` |
| `recompute(request)` | `{scope, nodeId?\|systemId?}` | ScopeReport |
| `export_library(options)` | `{name?, itemIds?, recipeIds?, machineDefIds?}` | Library envelope |
| `save_library(path, options)` | path string, options dict | `{"path": str}` |
| `load_library(path)` | path string | Library envelope |
| `import_library(library, on_conflict="rename")` | Library envelope, policy | `{world, idRemap, skippedIds}` |
| `import_library_file(path, on_conflict="rename")` | path string, policy | `{world, idRemap, skippedIds}` |

- **`edit`** is `{"op": "upsert", "kind": <kind>, "entity": {...}}` (replaces the
  entity of that id in place, else appends) or `{"op": "remove", "kind": <kind>,
  "id": <id>}`. `kind` ∈ `item`, `recipe`, `machineDef`, `node`, `pipe`,
  `system`. Editing recomputes nothing by itself — the frontend follows an edit
  with a `recompute` call.
- **`recompute` scopes**: `world` (default), `node` (`nodeId` — a single
  machine), `process` (`nodeId` — its connected component via `trace_process`),
  `system` (`systemId`). The payload is `ScopeReport.to_dict()` (external I/O,
  per-node balances, net energy, ranked bottlenecks).
- **`on_conflict`** is a `ConflictPolicy` value: `rename` (default), `skip`,
  `error`.

A changed shape here is a deliberate, documented contract bump — the P5 TS
bridge mirrors this table.

### Commands

Backend (run from `backend/`):

```bash
cd backend
python -m venv .venv && . .venv/bin/activate   # Python 3.11+
pip install -e '.[dev]'                         # install + dev tools
pytest                                          # run the test suite
ruff check . && ruff format --check .           # lint + format check
```

`pyproject.toml` sets `pythonpath = ["."]` and `testpaths = ["../tests"]`, so
`pytest` from `backend/` finds both the package and the `tests/` tree with no
extra flags.

Frontend (run from `frontend/`):

```bash
cd frontend
npm install        # install deps (Vite + React + TS + @xyflow/react)
npm run dev        # Vite dev server
npm run build      # type-check (tsc -b) + production bundle -> dist/
npm run lint       # tsc --noEmit type check
```

Layout (backend/tests/frontend scaffold exists now; the frontend `src/`
subdirs — `canvas`, `panels`, `styles` — land in Phases 5–7):

```
backend/
  process_sim/
    engine/      # rate-balance computation, pure Python
    model/       # dataclasses: Item, Recipe, MachineDef, MachineNode, Pipe, System, World
    io/          # load/save project files, library import/export
    api.py       # pywebview JS API surface — thin; marshals World to/from JSON
  app.py         # entry point: starts pywebview, loads the built frontend
frontend/        # web UI (Vite + React + TypeScript)
  src/
    canvas/      # React Flow graph: custom nodes, edges, ports
    panels/      # inspectors and editors (item / recipe / machine / pipe / node)
    styles/      # edunode tokens + component CSS
tests/           # pytest for engine/model; frontend tests added later
```

## Architecture rules

- **The engine does not import the UI or pywebview.** All rate/balance
  computation lives in `engine/` and `model/` and runs headless. The frontend
  reaches it only through the pywebview JS API, which serializes World state
  to/from JSON. Keep `api.py` thin — it marshals data, it does not hold logic.
  This keeps the hard part testable without a webview.
- **Offline-first.** The app makes no network calls at runtime. Bundle all assets
  (fonts, scripts, styles) locally; nothing loads from a CDN.
- **Recompute on change.** The model is steady-state, not a tick-by-tick
  simulation. Every machine and pipe runs continuously at its effective rate;
  on any edit, recompute balances. There is no event loop over discrete items.
- **Sparse, optional fields.** Most properties on items, recipes, machines, and
  pipes are optional. Unset means *absent*, not zero. Editors and inspectors show
  only the fields an entity actually uses — a consumer machine shows no
  energy-production field, an `in`-only pipe end shows no extract priority.
  Computation skips absent fields rather than substituting zero where that would
  mislead.

## Domain model

- **Item** — a tracked resource. Category is `solid`, `liquid`, or `energy`.
  Energy (unit EU) is a normal item so it can appear in recipes and balances.
  An item may optionally carry a stored-energy value `eu` — its fuel content in
  EU. A fuel with `eu = 1000` consumed at 2 EU/s lasts 500 s. Optional and
  selectable at item construction: items without `eu` are not fuels.
- **Stack** — an `{item, quantity}` pair, per recipe cycle unless noted.
- **Recipe** — input stacks → output stacks over a production time, with optional
  energy production/consumption.
- **MachineDef** — a template: energy behavior, allowed recipes, slot layout per
  category and direction, stack sizes, overclock bounds. A fuel-burning def is
  flagged as such and may set a conversion `efficiency` (waste fraction, see
  "Energy generation") and a `maxFluidRate` (`R`); non-fuel defs omit them.
  Nothing here names a specific fluid — which fluid a converter outputs is just
  one of its output items (content), not a model concept.
- **MachineNode** — a placed machine bound to a recipe, an overclock multiplier,
  and a parallel count.
- **Pipe** — the graph edge. Carries one category between two machine ports; has
  storage capacity, movement capacity, an overclock multiplier, and per-end
  roles and priorities.
- **System** — a user-defined group of nodes treated as one unit, with exposed
  ports and aggregated stats. Collapsible, nestable, connectable.
- **World** — all items, recipes, machine defs, nodes, pipes, and systems.

A slot is one buffer location holding one stack; `slots × stackSize` per category
and direction sets how much a machine can hold.

## Computation (all rates per second)

For a node running recipe `R` at `overclock` with parallel `count`:

```
cyclesPerSec   = overclock / R.timeSeconds
outputRate(s)  = s.quantity * cyclesPerSec * count   # per output stack
inputDemand(s) = s.quantity * cyclesPerSec * count   # per input stack
```

Energy per node — a recipe energy term, if set, **overrides** (replaces) the
machine-base term of the same kind; if neither is set, the term is absent (not
zero):

```
consume   = (recipe.energyConsumption  or  baseEnergyConsumption) * overclock * count
produce   = (recipe.energyProduction   or  baseEnergyProduction ) * overclock * count
netEnergy = produce - consume     # omit a side entirely when it has no term
```

### Energy generation (fuel → fluid)

Fuel-burning machines convert an item's stored `eu` into an **output fluid** — a
liquid item that then flows through pipes like any other fluid, obeying movement
capacity and priority, and energy consumers draw it. This is how EU is
"carried": as a fluid, not a direct lossless link. Which fluid, and its energy
density, are content — an `Item` with its own `eu`. (Default content's example
is steam, a liquid at 1 EU per unit, so 1000 EU of fuel → 1000 units, 1:1 — but
the engine never names it.)

With max rate `R`, fluid actually drawn `D` (capped at `R`), fuel energy `E`, and
efficiency `η` (rates per second; the tick form scales identically):

```
fluidOut     = D                   # output-fluid units
fuelDrain    = D / η               # EU of fuel consumed
burnTime     = E · η / D           # how long one fuel unit lasts
usableOutput = E · η               # total output from one fuel unit
```

Most machines are lossless: `η = 1`, so `fuelDrain = D` and conversion is exactly
1:1. A machine may set an **efficiency** parameter — a waste fraction `w` applied
to the heat it generates but does not deliver when the fluid isn't fully drawn:

```
η         = D / (D + w·(R − D))
fuelDrain = D + w·(R − D)          # = w·R + (1−w)·D, well-defined at D = 0
wasted    = w·(R − D)
```

Default `w = 0` (no waste, η = 1). A lossy converter — the default-content
**Large Steam Boiler** sets `w = 0.8`, `R = 256` — burns `w·R` fuel even at zero
draw (idles hot) and loses `w` of any unused surplus, so η reaches 1 only at full
load (`D = R`).

Pipe throughput: `movementCapacity * overclock` caps sustained flow. Storage
capacity only buffers transients.

Per-item balance at a node:

```
input:  shortfall = max(0, inputDemand - supplied)
output: overflow  = max(0, outputRate  - demanded)
```

Each pipe's flow is `min(upstream available, downstream demand, pipeThroughput)`.
A full input buffer pushes overflow upstream; a full output buffer stalls the
machine.

Aggregation over a system, traced process, or world: sum `netEnergy` for the
scope's energy balance; external inputs are items members demand but aren't
supplied internally; external outputs are items produced beyond internal demand;
flag the largest shortfalls as bottlenecks.

## Pipe priority — read this before touching flow resolution

Each pipe end has an **insert priority** and an **extract priority**, each a
signed scalar from −128 to 127, default 0, set independently. The resolution is
**asymmetric**, and that asymmetry is intentional:

- **Insert contention** (one source feeding several destinations): fill in order
  of **descending** insert priority — higher fills first. Equal priority splits
  evenly, round-robin in batches sized by pipe speed (movement capacity).
- **Extract contention** (several sources feeding one destination): drain in order
  of **ascending** extract priority — lower drains first. Equal priority splits
  evenly.
- **Self-loop guard**: an `io` end that extracts from and inserts into the same
  buffer will not re-insert what it pulled when its insert priority is below its
  extract priority.

Cases that must hold:

1. Furnace into two chests, main insert 10, overflow insert 0 → main fills first,
   spills to overflow only when full.
2. One stream into three machines all at insert 0 → even round-robin.
3. One `io` pipe filtering a barrel, extract 5 insert 0 → won't re-insert (insert
   < extract).
4. Two sources into one machine, A extract 0, B extract 10 → A drains fully before
   B is touched.
5. A chest at insert 127, everything else ≤ 0 → always filled first.

Insert favors higher, extract favors lower. Don't "simplify" this into one rule.

## Overclock

A single scalar multiplier, default 1, applied linearly. On machines it scales
cycles/sec, energy consumption, and energy production together. On pipes it scales
movement capacity. Optional min/max bounds per definition for UI clamping. No
non-linear curve.

## Resolved decisions

All of the original open questions are decided. Build to these.

1. **Pipe priority asymmetry is final** — insert favors higher, extract favors
   lower, exactly as specified above and in the five cases. Not up for revision.
2. **Recipe energy overrides machine base** — when a node's recipe specifies an
   energy term, it replaces the machine-base term of the same kind (production or
   consumption), rather than adding to it.
3. **EU is carried as a fluid** — fuel items store `eu`; a fuel-burning machine
   converts it into an output fluid (a liquid item) that flows through pipes
   under the normal movement-capacity and priority rules. The specific fluid is
   content, not a model concept (default content uses steam at 1 EU/unit). See
   "Energy generation".
4. **Round-robin batch size follows pipe speed** — the equal-priority split is
   batched by the pipe's movement capacity (already a pipe parameter), not a
   separate fixed constant.
5. **Display units auto-scale per category** — store canonical units internally;
   the UI auto-scales values for readability.

## Visual style

The look is defined in `STYLE_GUIDE.md` (edunode's "Instrument Panel on Dark
Glass"): a deep slate field, one signal-cyan accent for the active path, phosphor
status colors, mono type for every id/label/metric, and a display sans for
everything else. Read that file before doing UI work and use its `:root` tokens as
the only palette — don't introduce new hues.

Because the app is a web UI in a system webview, the guide applies almost
directly — paste the `:root` tokens, reuse the component recipes as CSS/className
styling, and the glass/glow/shadow effects (`backdrop-filter`, `box-shadow`,
`drop-shadow`) all work. Two adjustments for our context:

- **Bundle fonts locally.** Don't use the Google Fonts `@import`; the app is
  offline-first. Ship IBM Plex Mono and Space Grotesk as local `woff2` files and
  `@font-face` them.
- **Check across webviews.** Rendering differs between platform webviews
  (Chromium/WebView2 on Windows, WebKit on macOS and Linux). Verify
  `backdrop-filter` and font smoothing on each target; where `backdrop-filter` is
  weak, fall back to a solid translucent `--panel` fill.

Canvas: nodes, ports (React Flow handles), and pipes (edges) are React Flow
elements styled with the same tokens — node bodies are `div`s, edges are SVG paths
(color by category, thickness by flow, dashed for shortfall). Apply the cyan glow
to the selected/live element only.

Domain-state palette (the guide doesn't cover these directly):

- **Status colors** — decided: OK → green (`--s-ref`), shortfall → red
  (`--s-bad`, a red added to the palette for this app), overflow → yellow
  (`--s-learn`), stalled / unpowered → grey (`--s-not`). Cyan (`--spine`) stays
  reserved for the selected/live element.
- **Category colors** (solid / liquid / energy edges and ports) — default
  assignment (adjustable): solid → silver (`--s-teach`), liquid → blue
  (`--s-indep`), energy → gold (`--subedge`). A converter's output fluid is a
  liquid, so it rides the liquid color. Cyan stays reserved; red/green/yellow
  stay reserved for status.

## Out of scope for v1

Tick-by-tick simulation with real backpressure, probabilistic recipe outputs
(use expected rates), and any multiplayer or cloud sync.

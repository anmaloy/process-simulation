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
balance, and saves.

Phase 5 is done too: the frontend shell + bridge (`frontend/src/`). `styles/`
holds the palette tokens (verbatim from `STYLE_GUIDE.md` §2) and the rail+stage
shell; IBM Plex Mono and Space Grotesk ship as local `woff2` via `@fontsource`
(no Google Fonts `@import`, offline-first — the built `dist/` loads nothing from
a CDN). `bridge/` is a typed TS client (`ApiClient`) over `window.pywebview.api`
mirroring the P4 method shapes, with a `DevApi` fallback over an in-memory
fixture so `npm run dev` runs in a plain browser with no Python host; the client
unwraps the result envelope (throwing `ApiCallError` on `{ok: false}`). `App.tsx`
boots the bridge and round-trips a `get_world` call.

Phase 6 is done: the node-graph canvas (`frontend/src/canvas/`). React Flow
(`@xyflow/react`) renders the World — custom machine nodes (`div` bodies) with
ports as handles (one per category×direction from the MachineDef slot layout),
and pipes as edges colored by category. The World is the single source of truth:
placing a machine, wiring a pipe, moving a node, or deleting either bubbles up as
a P4 `apply_edit` call whose returned World reseeds the graph, so a laid-out
graph round-trips through the API into a loadable project (positions persist on
`MachineNode.x/y`). The category color map (`colors.ts`: solid→silver,
liquid→blue, energy→gold) is the single seam; `graph.ts` holds the World↔React
Flow mapping and the connection validation (out→in, matching category). Edge
`data` leaves seams (`status`, `flow`, `shortfall`) for the P8 balance overlay —
absent until a recompute fills them, so nothing is fabricated. Pan/zoom, the
minimap, and controls are React Flow defaults, re-skinned with the style tokens.

Phase 7 is done: the inspector panels (`frontend/src/panels/`). A docked panel
edits the current selection — canvas clicks pick a node or pipe, rail lists pick
a library entity (item / recipe / machine def) — through one `Inspector` that
dispatches to a per-kind editor (`ItemInspector`, `RecipeInspector`,
`MachineDefInspector`, `NodeInspector`, `PipeInspector`). Selection is controlled
by the host (click-driven, not React Flow's own), so the two surfaces share one
inspector with no feedback loop. The load-bearing rule is sparse-field
discipline, built into the `fields.tsx` kit: an *optional* field that is absent
renders as "unset / + set" (never a populated 0) and clearing it removes the key;
a genuinely *inapplicable* field isn't mounted at all — a non-fuel def shows no
efficiency/`maxFluidRate`, an `in`-only pipe end shows no extract priority, and
flipping a role or the fuel flag clears the now-inapplicable terms. Every field
edit routes through the P4 `apply_edit` upsert and is followed by a `recompute`,
so edits persist and the balance refreshes; the returned World reseeds the
canvas.

Phase 8 is done: the balance overlay (`frontend/src/overlay/`). The engine's
`ScopeReport` is made legible on the canvas and in a floating readout. Nodes
carry their balance status (OK green / shortfall red / overflow yellow /
stalled grey) as a left bar, a status chip, and a faint body ring; pipe edges
thicken with resolved flow and dash when they feed a starved node; the cyan glow
stays reserved for the selected element. `status.ts` is the single status
color/label map (read by the node view, the legends, and the readout);
`units.ts` is the single display-scaling seam (resolved decision #5 — canonical
units stored, values auto-scaled to k/M/G and sub-unit for readability).
`Legends` renders the category + status keys; `BalanceReadout` shows the chosen
scope's external I/O, net energy, and ranked bottlenecks, with a scope switcher
(machine / process / system / world). Scope recompute is effect-driven in
`App.tsx`: any edit (which replaces the World) or scope-target change refreshes
the report, and a scope with no target (e.g. machine scope with nothing
selected) clears it rather than fabricating one. To feed the edge overlay, the
engine's `ScopeReport` gained a **`pipeFlows`** field (per-pipe resolved `flow` +
a `shortfall` flag) — an additive, documented contract bump mirrored in the TS
`ScopeReport` type.

Phase 9 is done: systems / grouping (`frontend/src/systems/`). A System groups
member nodes (and nested systems) and can **collapse** into one canvas node.
Collapse is presentation only — pipes always reference the real member nodes, so
the engine's balance is byte-identical folded or expanded (the model's
`collapsed` flag is never read by the engine; P2d already scoped systems with
nesting). `collapseState` decides which members fold away and which *outermost*
collapsed systems render as nodes; `buildCanvasGraph` folds them, re-points a
pipe crossing a collapsed boundary to the system node (a handle encodes the
member port as `b:<nodeId>:<port>`), and drops a pipe internal to one collapsed
system. A new pipe drawn to a collapsed system's handle decodes back to a real
node→node pipe, so a group stays genuinely connectable. Canvas multi-select
offers a "Group" action; the `SystemInspector` edits membership, nesting
(cycle-guarded), the collapsed flag, and exposed ports; a folded node shows its
member count and aggregate status/energy from a per-system recompute. The
`System` model gained optional `x`/`y` (the collapsed node's position).

Phase 10 is done: the app shell & packaging (`app.py`). The whole app runs as a
single Python process — `app.py` builds the thin `Api`, binds it to a pywebview
window, and loads the **built** `frontend/dist/index.html` from disk via a
`file://` URI (Vite `base: "./"` keeps asset paths relative; no dev server, no
network). All non-GUI logic is import-safe and unit-tested headless (`webview`
is imported lazily inside `main`): `dist_dir` resolves the bundle
(`PROCESS_SIM_DIST` env → PyInstaller `_MEIPASS` → repo layout), `build_api`
optionally preloads a project file, and `find_external_refs` enforces the
offline guarantee by scanning the built HTML/CSS for any external load.
`python app.py --check` runs that scan (exit 0/1/2) and the built bundle passes;
`backdrop-filter` degrades to a solid `--panel` fill via `@supports` on every
glass surface. Packaging is a documented PyInstaller recipe plus a `process-sim`
console-script entry point.

Post-P10 revision (energy model + UX): the energy model moved to **carrier
tiers** — `MachineDef.energySource` (electric/liquid/fuel) with a source/output
carrier replaces the old fuel-burning flag and per-recipe energy terms; steam and
fuel are routed items, electricity is the scalar grid balance (see "Domain
model", "Carrier tiers", and decisions #2–#3). The engine now solves per-node
**duty cycles** so a starved machine reduces yield (downtime) instead of showing
unmet demand as an external input, and treats **storage** defs as transparent
buffers (`nodeDuty` + reframed `externalInputs`/`externalOutputs` in
`ScopeReport`). On the UI side: searchable/alphabetised combo pickers everywhere
(`frontend/src/ui/Combobox.tsx`), name-derived ids with reference-rewriting
rename (`ui/ids.ts`), undo (Ctrl+Z), per-category display units (items/mB/EU),
palette-matched checkboxes, category pipe-speed defaults, and storage/duty
badges on the node body.

A later pass made **electricity a routed, transfer-limited carrier** (it flows
over energy pipes like steam, no longer a scalar link) and added **persistence**:
`Api(world, autosave_path)` writes the World after every change and `app.py`
restores it on launch (`~/.process_sim/autosave.psim.json`, `PROCESS_SIM_HOME`
override; a corrupt autosave is ignored, an explicit project file wins), so the
app no longer resets on reopen. `DevApi` mirrors this with `localStorage` for the
browser build.

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
  per-node balances, net energy, ranked bottlenecks, and `pipeFlows` — per-pipe
  resolved flow + a starved-edge `shortfall` flag, added for the P8 overlay).
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

Run the desktop app (needs the frontend built first, and a GUI):

```bash
cd frontend && npm run build      # produce frontend/dist/
cd ../backend
python app.py                     # open the pywebview window
python app.py path/to/project.json  # open with a project loaded
python app.py --check             # verify the bundle loads nothing external (CI)
```

Package per OS with PyInstaller, bundling the built frontend beside the binary
(`process-sim` is also the installed console-script entry point):

```bash
pyinstaller --name process-sim --windowed \
    --add-data "../frontend/dist:frontend/dist" app.py
```

Frontend (run from `frontend/`):

```bash
cd frontend
npm install        # install deps (Vite + React + TS + @xyflow/react)
npm run dev        # Vite dev server
npm run build      # type-check (tsc -b) + production bundle -> dist/
npm run lint       # tsc --noEmit type check
```

Layout (`styles/`, `bridge/`, `canvas/`, and `panels/` exist now):

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
    bridge/      # typed ApiClient over window.pywebview.api + DevApi fallback
    styles/      # palette tokens + shell CSS + local @font-face (woff2)
    canvas/      # React Flow graph: custom nodes, edges, ports, color map (P6)
    panels/      # docked inspector + per-kind editors, sparse-field kit (P7)
    overlay/     # balance overlay: status map, legends, scope readout (P8)
    systems/     # grouping: collapse/expand, nesting, connectable group node (P9)
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
- **Recipe** — input stacks → output stacks over a production time. Recipes carry
  **no** energy term: energy is a machine property (a machine draws a defined
  amount while running), so it lives on the `MachineDef`, not the recipe.
- **MachineDef** — a template: an **energy source**, allowed recipes, slot layout
  per category and direction, stack sizes, overclock bounds, and optional
  storage. `energySource` ∈ `electric` / `liquid` / `fuel` (absent = passive)
  says how the machine is powered; `sourceItemId` names the carrier it consumes.
  A **consumer** sets `baseEnergyConsumption` (EU/s drawn to run its recipe). A
  **converter** sets `outputItemId` + `maxFluidRate` and optional
  `efficiency` (`w`, waste fraction) — it spends its source's energy to turn an
  optional `inputItemId` **material** into that output carrier (a boiler burns
  fuel to heat water → steam; a turbine spends steam → electricity with no
  separate material). The material is consumed at `materialRatio` output units
  per material unit (optional, default `1` = 1:1); a boiler making 16 steam per
  water sets it to `16`. A **storage** def sets `storage`
  (`{category, capacity}`) and is a transparent buffer. Nothing here names a
  specific fluid — the carriers are just items (content).
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

Energy per node — a machine draws its `baseEnergyConsumption` while running; a
converter earns a `produce` term by making an energy-category output. Both the
draw and the output are **routed carriers** (items), and the scalar energy terms
are *derived* from those energy-category flows. An absent term stays absent (not
zero):

```
consume   = baseEnergyConsumption * overclock * count   # routed as its carrier
produce   = (converter energy-carrier output)           # see "Carrier tiers"
netEnergy = produce - consume     # derived from energy-category carriers
```

### Carrier tiers (fuel → steam → electricity)

Energy flows as a chain of carriers: a **fuel** item (stored `eu`) is burned by a
boiler into **steam** (a liquid); a turbine consumes steam and produces
**electricity** (the `energy` category). **All three carriers route as items**
through pipes, subject to movement capacity and priority — so electricity has a
transfer limit from its electric connections (default 5120 EU/s = 256 EU/t ×
20 t/s), exactly like steam or a solid. A machine's `energySource` picks the
carrier it consumes; `sourceItemId` names it. An electric machine with no carrier
chosen falls back to a scalar mains draw (no transfer limit). Electricity is kept
out of the material feedstock/yield lists (it is the grid, reported as
`netEnergy`), but a power shortage still shows as a throttled duty + a bottleneck.

The fuel→output conversion math is unchanged (see `engine/energy.py`): with max
rate `R`, draw `D`, waste `w`, the converter consumes its source for *energy* (a
fuel by its `eu`, else a liquid 1:1) and, when `inputItemId` is set, consumes
that **material** at `materialRatio` output units per material unit (default 1 =
1:1), emitting the output carrier as a routed item. Default content models the
Modern Industrialization boiler, whose `materialRatio` is 16 (1 mB water → 16 mB
steam, validated against the mod source: `SteamHeaterComponent` /
`MIServerConfig.steamPerWater`); steam itself is 1 mB = 1 EU, so a machine at
1 EU/t draws 1 mB/t = 20 mB/s of steam.

**Units.** The engine is canonical **per second**. A tick is 1/20 s, so the UI
edits/shows energy and converter output rates **per tick** (EU/t, mB/t), while
recipe amounts and material flows read **per second** — the two bases are a ×20
conversion applied only at the UI boundary (`overlay/units.ts`), so the engine
and its tests stay per-second.

Pipes are **pure transfer vehicles** — only `movementCapacity × overclock` caps
sustained flow; pipe storage is ignored in the steady-state balance.

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

Aggregation over a system, traced process, or world: these are self-sufficient
devices, so a machine starved of an internally-produced input doesn't get an
external top-up — it runs at a reduced **duty cycle** (downtime), which lowers
its output and cascades downstream (solved by relaxation in `compute_scope`,
reported as `nodeDuty`). The scope then reports: `netEnergy` (sum of duty-scaled
energy terms); **external inputs** = outside *feedstock* only (items with no
internal producer that the running scope consumes); **external outputs** = the
**actual yield** (effective production that leaves the scope); and the largest
internal shortfalls as **bottlenecks**. A **storage** node is a transparent
passthrough (its buffer only absorbs transients), spliced out of the flow graph.

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
2. **Energy is a machine property** — recipes carry no energy term; a machine
   draws `baseEnergyConsumption` while running. A machine's `energySource`
   (electric / liquid / fuel) picks the carrier it consumes.
3. **All carriers are routed items (electricity included)** — fuel items store
   `eu`; a boiler burns fuel into steam, a turbine turns steam into electricity.
   Fuel, steam, **and** electricity flow through pipes under the normal
   movement-capacity and priority rules, so electricity is transfer-limited by
   its electric connections (default 5120 EU/s). `netEnergy` is derived from the
   energy-category carriers and electricity is excluded from the feedstock/yield
   lists. Pipes are pure transfer vehicles — pipe storage is ignored. The
   specific carriers are content (default content uses steam). See "Carrier
   tiers".
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

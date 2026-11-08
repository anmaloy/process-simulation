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

Mirrors edunode's architecture (a simple, buildless client-side app over a thin
Python backend — see `anmaloy/edunode_app`):

- **Backend:** Python 3.11+. The rate-balance engine, data model, and file IO are
  pure Python with no UI dependency, under `process_sim/`.
- **UI:** one hand-written `ui/index.html` — vanilla JS, a hand-rolled SVG/HTML
  canvas (pan/zoom/drag), inspectors, and the balance overlay. **No build step,
  no npm, no framework, no `node_modules`.** Edit the file and refresh.
- **Backend surface:** `backend.Api` (re-exported from `process_sim.api`) holds
  the working World and marshals JSON <-> model. The same instance is bound two
  ways, and the UI's `callApi` auto-detects which transport is live and unwraps
  the `{ok,data}` result envelope:
  - `app.py` — the **pywebview desktop app**; the UI calls
    `window.pywebview.api.<method>(...)`.
  - `server.py` — a **stdlib HTTP server** (`POST /api/<method>`, body
    `{"args": [...]}`) for any browser on the network. Run exactly one process.
- **Persistence:** the working World auto-saves to
  `~/.process_sim/autosave.psim.json` after every change (`PROCESS_SIM_HOME`
  overrides the dir), so the app resumes on relaunch. Project files are JSON (the
  whole World). `export_standalone.py` bakes the World into a single view-only
  HTML (no backend).
- **Tests:** pytest for the engine, model, IO, api, and app/server surface. The
  UI is validated by driving `server.py` (the real engine) in a headless browser.

The styling target is edunode's CSS (`STYLE_GUIDE.md` `:root` tokens, pasted
verbatim); fonts ship as local `woff2` under `ui/fonts/` (offline-first).

## Project state

The headless core (`process_sim/`: `engine/` rate-balance, `model/` dataclasses,
`io/` project/library/MI-import, `api.py` the JSON bridge) is complete and fully
pytest-covered — it computes every scope's balance, round-trips projects, and
imports Modern Industrialization recipe data. This core was carried over intact;
only its *surface* changed.

The UI was **rewritten from a React/Vite/TypeScript + React Flow app into the
single buildless `ui/index.html`** to match edunode and remove the
build-toolchain pain and a React Flow viewport bug. It has full feature parity:
the node-graph canvas (place/move/delete machines, wire/delete pipes,
place pipe junctions to route a trunk to many consumers, pan/zoom/fit), per-kind
inspectors with sparse-field discipline (item / recipe /
machineDef / node / pipe / pipeNode / system), the input/output-driven recipe finder, the
balance overlay (status bar + chip + duty %, flow-weighted/dashed edges, floating
readout with a world/process/node/system scope switcher and ranked bottlenecks),
the collapsible/searchable library rail, systems (shift-select -> Group,
collapse/expand), undo (Ctrl+Z), MI import, and project save/load. Every
structural gesture and field edit goes through `apply_edit` and is followed by a
`recompute`; the returned World reseeds the canvas.

Energy is the carrier-tiers model (routed fuel -> steam -> electricity, per-recipe
hybrid energy, MI power tiers clamping overclock, per-node duty cycles,
transparent storage buffers, membership-aware tag flow resolution). The "Domain
model", "Carrier tiers", "Computation" and "Resolved decisions" sections below
are the authoritative detail and are unchanged by the UI rewrite.

### API surface (frozen contract)

`api.py` exposes a single `Api` class (re-exported as `backend.Api`); one
long-lived instance is bound by the desktop app (pywebview — the UI calls
`window.pywebview.api.<method>(...)`) and by the server (`server.py` — `POST
/api/<method>` with body `{"args": [...]}`). Arguments arrive as plain JSON and
return values serialize straight back. The class is **marshaling only** — it
converts JSON↔model and delegates to `engine/` and `io/`; it holds no rate math,
flow resolution, or file-format logic. The one state it keeps is *the current
World*; edits mutate it, reads and recomputes run against it.

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
| `import_mi_dir(path, options)` | path string, `{machineTypes?, name?, onConflict?}` | `{world, idRemap, skippedIds, stats}` |

- **`edit`** is `{"op": "upsert", "kind": <kind>, "entity": {...}}` (replaces the
  entity of that id in place, else appends) or `{"op": "remove", "kind": <kind>,
  "id": <id>}`. `kind` ∈ `item`, `recipe`, `machineDef`, `node`, `pipe`,
  `pipeNode`, `system`. Editing recomputes nothing by itself — the UI follows an
  edit with a `recompute` call.
- **`recompute` scopes**: `world` (default), `node` (`nodeId` — a single
  machine), `process` (`nodeId` — its connected component via `trace_process`),
  `system` (`systemId`). The payload is `ScopeReport.to_dict()` (external I/O,
  per-node balances, net energy, ranked bottlenecks, and `pipeFlows` — per-pipe
  resolved flow + a starved-edge `shortfall` flag, added for the P8 overlay).
- **`on_conflict`** is a `ConflictPolicy` value: `rename` (default), `skip`,
  `error`.
- **`import_mi_dir`** reads an **extracted Modern Industrialization jar
  directory** (`io/mi_import.py`) and merges its machine recipes into the current
  World via `import_library`. By **default it discovers and imports every
  powered-machine recipe type it finds** — any namespaced type (not `minecraft:`,
  not a bare vanilla type) whose recipes carry a machine shape — so a *different
  mod* with the same recipe structure imports with no code change; the type set
  is no longer a hand-maintained allowlist (`DEFAULT_MACHINE_TYPES` is kept only
  as a ready-made subset). Pass `machineTypes` (bare type names) to narrow it. The
  parser normalizes MI's shapes (object-or-list I/O, the forge hammer's
  `ingredient`/`result` shape, bare/namespaced ids, `tag` refs → `isTag` items,
  `probability`, default `amount` 1), converts units (ticks→seconds, `eu` kept
  per-tick on the recipe), merges every mod's `lang` for display names, reads item
  fuel `eu` from NeoForge's `furnace_fuels` burn-time and fluid `eu` from
  `fluid_fuels`, and groups recipes by `type` into electric-consumer machine defs
  with a data-derived slot layout and the `MACHINE_CATALOG`'s tier ceiling
  (`voltageTier` + `maxEuPerTick`; single-block electric machines are LV =
  32 EU/t, from the jar's `CableTier` / `MachineTier`; multiblocks import
  unclamped). A recipe type run by distinct **named blocks** (`MULTIBLOCK_VARIANTS`
  — the steam/electric **quarry** and steam/electric **blast furnace**, which the
  recipe data can't tell apart) yields those variant defs (steam on steam,
  electric on routed electricity) instead of one generic def. For the machines
  that also have
  **steam tiers** (`STEAM_MACHINE_TIERS` — bronze/steel for macerator, mixer,
  compressor, cutting machine; steel-only for packer, unpacker, wiremill) it adds
  a bronze/steel def beside the electric one, running the same recipes but powered
  by steam (`energySource: liquid`, `sourceItemId` steam) and clamped to the
  tier's `MachineTier` EU/t ceiling (bronze 2, steel 4). It also adds the
  non-recipe **energy-generation** converter defs (`ENERGY_MACHINES`: boilers,
  steam/plasma turbines, diesel generators) and the routed-electricity carrier,
  and sets imported consumers to draw it; the **water pumps** (`PUMP_MACHINES` —
  bronze/steel on steam, electric on electricity, each a water source running a
  fixed-1-EU/t "pump water" recipe at bronze 200 / steel 400 / electric
  3200 mB/s, the full-placement max); pass `include_energy: false` to skip the
  energy machines **and** pumps (they share the carriers). It always adds the
  **storage** defs (`STORAGE_MACHINES` — material tanks/barrels, the single-block
  Configurable Tank/Chest, and the Large Tank multiblock, with the jar's
  capacities as editable buffer annotations). It resolves tag membership (each
  tag item's `members`, registering missing member items), filling the vanilla
  `c:` material tags (`c:ores/iron` & co.) the jar references but NeoForge — not
  the mod — defines, from the vanilla naming convention. `stats` reports the
  counts (files scanned, recipes imported/skipped, items, tags, member items
  added, defs, `steamMachines`, `energyMachines`, `pumps`, `storage`, per-type).

A changed shape here is a deliberate, documented contract bump — the UI's
`callApi` wrappers in `ui/index.html` mirror this table.

### Commands

Run the desktop app (needs a system web engine for pywebview):

```bash
pip install pywebview            # the only runtime dependency
python app.py                    # open the desktop window
python app.py path/to/project.psim.json   # open with a project loaded
```

Run as a server instead (standard library only — reach it from any browser/device):

```bash
python server.py                 # http://localhost:8000
python server.py 9000            # custom port
```

Edit the UI: change `ui/index.html` and refresh the browser (or relaunch the
desktop window). There is no build, no `node_modules`, nothing to reinstall for a
change.

Tests / lint (from the repo root):

```bash
pip install pytest ruff
pytest                           # engine, model, io, api, app/server
ruff check . && ruff format --check .
```

`pyproject.toml` sets `pythonpath = ["."]` and `testpaths = ["tests"]`, so
`pytest` from the root finds the `process_sim` package and the tests with no
extra flags. Bake a backend-less, view-only copy with
`python export_standalone.py` (-> `dist/process_sim.html`).

Layout:

```
app.py               # pywebview desktop entry (binds backend.Api)
server.py            # stdlib HTTP server (POST /api/<method>) for browsers
backend.py           # Api home (re-exports process_sim.api) + build_api factory
export_standalone.py # bake the World into one view-only HTML file
process_sim/
  engine/            # rate-balance computation, pure Python
  model/             # dataclasses: Item, Recipe, MachineDef, MachineNode, Pipe, System, World
  io/                # load/save projects, library + Modern Industrialization import
  api.py             # the JSON API surface — thin; marshals World to/from JSON
ui/
  index.html         # the entire UI: vanilla JS, hand-rolled canvas, inspectors, overlay
  fonts/             # local woff2 (IBM Plex Mono, Space Grotesk) — offline
examples/            # a sample project
tests/               # pytest for engine/model/io/api/app
```


## Architecture rules

- **The engine does not import the UI or pywebview.** All rate/balance
  computation lives in `process_sim/engine/` and `process_sim/model/` and runs
  headless. The UI reaches it only through `backend.Api` (over the pywebview
  bridge or `POST /api/<method>`), which serializes World state to/from JSON.
  Keep `api.py` thin — it marshals data, it does not hold logic. This keeps the
  hard part testable without a webview.
- **No build step.** The UI is a single hand-written `ui/index.html` (vanilla JS
  + CSS). Do not reintroduce npm, a bundler, a framework, or a `node_modules` —
  the whole point of the edunode-style rewrite was to delete them. Edit the file
  and refresh.
- **Offline-first.** The app makes no network calls at runtime. Keep all assets
  (fonts under `ui/fonts/`, scripts, styles) local; nothing loads from a CDN.
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
- **Stack** — a `{resource, quantity}` pair, per recipe cycle unless noted. The
  resource is a concrete `itemId` **or** a `tag` (a group reference like
  `c:gears/iron` = "any item in this set"; a tag is a first-class fungible
  resource backed by an `Item` with `isTag: true` whose `members` list the
  concrete items it includes). A stack may carry an optional
  `probability` (expected consume/produce chance; absent = always, `0` = a
  referenced-but-unconsumed catalyst).
- **Recipe** — input stacks → output stacks over a production time. Energy is
  **hybrid** (see decision #2): a recipe may carry its own `euPerTick` (the
  EU/tick its machine draws while making it) which overrides the machine's
  `baseEnergyConsumption` default; a recipe without it falls back to the machine
  draw. An optional `machineType` names the machine kind that runs it (MI's
  recipe `type`, e.g. `macerator`).
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
  and a parallel count. Two optional **boundary** flags opt a node out of the
  automatic supply/demand gating for the edge of the system a player tends by
  hand: `assumeInputs` (manual insertion — treat any input not supplied by a pipe
  as available) and `assumeOutputs` (manual extraction — treat outputs as removed,
  so it runs even with nothing downstream). Default off.
- **Pipe** — the graph edge. Carries one category between two machine ports; has
  storage capacity, movement capacity, an overclock multiplier, and per-end
  roles and priorities. **Liquid pipes carry an unlimited rate** — only solids
  and electricity are throughput-capped by `movementCapacity`.
- **PipeNode** — a pipe *junction*: a small routing dot (one `category`, `x`/`y`)
  with no recipe/def/slots/energy. It exists only to diverge or merge pipes of
  one category — a steam or power or belt trunk fans out to many consumers
  without wiring each back to the source. In the balance it is a **transparent
  passthrough**, spliced out of the flow graph by the same contraction that
  dissolves storage buffers (`X → junction → Y` resolves as a direct `X → Y`
  throttled by the tighter pipe; connectivity for `trace_process` routes through
  it). It carries no throughput or priority of its own — those stay on the pipes.
  A pipe end at a junction uses role `io`. The resolved flow is reported on every
  segment of a spliced chain, so a trunk feeding a junction shows the sum its
  branches carry.
- **System** — a user-defined group of nodes treated as one unit, with exposed
  ports and aggregated stats. Collapsible, nestable, connectable.
- **World** — all items, recipes, machine defs, nodes, pipes, pipe nodes, and
  systems.

A slot is one buffer location holding one stack; `slots × stackSize` per category
and direction sets how much a machine can hold.

## Computation (all rates per second)

For a node running recipe `R` at `overclock` with parallel `count`:

`overclock` is first **clamped** by the machine's power ceiling (decision #2):
`overclock ≤ maxEuPerTick / euPerTick` when both are set — a capped machine runs
its recipe slower, since speed and energy move together. A term `s` carries an
optional `probability` (absent = 1); a `probability = 0` term is a catalyst
(referenced, not consumed/produced) and is left absent, never a zero rate:

```
cyclesPerSec   = overclock / R.timeSeconds
outputRate(s)  = s.quantity * p(s) * cyclesPerSec * count   # p(s)=probability||1
inputDemand(s) = s.quantity * p(s) * cyclesPerSec * count
```

Energy per node — the draw is **hybrid** (decision #2): a recipe's own
`euPerTick` overrides the machine's `baseEnergyConsumption` default. `euPerTick`
is per **tick**, so the engine multiplies by 20 for its per-second canonical
draw; `baseEnergyConsumption` is already per-second. A converter earns a
`produce` term by making an energy-category output. Both the draw and the output
are **routed carriers** (items), and the scalar energy terms are *derived* from
those energy-category flows. An absent term stays absent (not zero):

```
euPerTick = R.euPerTick  (else baseEnergyConsumption / 20)   # per-tick draw
consume   = euPerTick * 20 * overclock * count               # routed as carrier
produce   = (converter energy-carrier output)                # see "Carrier tiers"
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

Pipes are **pure transfer vehicles** — for solids and electricity,
`movementCapacity × overclock` caps sustained flow; **liquid pipes are
uncapped** (unlimited rate). Pipe storage is ignored in the steady-state
balance.

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

Aggregation over a system, traced process, or world models an **automatic
system**: a machine runs only to the extent its inputs are actually delivered by
connected pipes *and* its outputs are actually taken downstream — "if everything
is not continuous, it won't run". Each node's **duty cycle** is the smaller of
those two limits, solved by relaxation in `compute_scope` (reported as
`nodeDuty`):

- **Input-limited (starved):** an input isn't supplied → the node can't run → a
  **shortfall** (red), reported as a bottleneck. A node flagged `assumeInputs`
  (manual insertion) treats any unsupplied input as available, so it runs on
  hand-fed feedstock. Placing *just* a converter (nothing wired) is a shortfall,
  not an overflow of output it can't make.
- **Demand-limited:** an output isn't fully consumed, so the node throttles and
  draws proportionally less input — a water pump only runs as often as the boiler
  draws water, so its material isn't drawn 100% of the time. An output nothing
  consumes is a **product** and leaves as yield; an output with a downstream
  consumer backpressures to that demand unless the node is flagged
  `assumeOutputs` (manual extraction). Storage (buffer) nodes are transparent, so
  an item only a buffer holds is still a product.

**Generation sources on a feedback cycle.** A **generation source** — an energy
converter (boiler / turbine / generator making a carrier) or an input-less
producer (a water pump, quarry, or drill yielding a resource from power alone,
with no material feedstock) — is the supply edge of the system. When such a
source sits on a *flow cycle* (its own output can return to its input through the
pipe graph — a boiler whose steam powers a pump that pumps the boiler's water),
plain mutual demand-backpressure would throttle every node on the loop to zero
("they should be pumping and consuming, they are doing neither"). The engine
detects these cycles and treats a source on one as if `assumeOutputs` were set:
it runs at the rate its *inputs* permit and the surplus leaves as yield, so a
boiler fed water and fuel makes steam at its rated rate and a powered pump makes
water at its rated rate. A source in a plain *linear* chain is **not** on a cycle
and still demand-throttles normally (burning no more fuel than its consumers
draw).

A node held below the duty that demand alone would allow (computed by a second,
inputs-unconstrained relaxation) is the input-limited case. The scope reports:
`netEnergy` (sum of duty-scaled energy terms); **external inputs** = the
manually-inserted feedstock the running scope consumes; **external outputs** =
the **actual yield** (products leaving the scope); and the largest input
shortfalls as **bottlenecks**. A **storage** node is a transparent passthrough
(its buffer only absorbs transients), spliced out of the flow graph.

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
non-linear curve. The engine additionally clamps a machine's effective overclock
to its power ceiling (`euPerTick × overclock ≤ maxEuPerTick`, decision #2) — a
machine can't run faster than its tier/upgrades can feed it.

## Resolved decisions

All of the original open questions are decided. Build to these.

1. **Pipe priority asymmetry is final** — insert favors higher, extract favors
   lower, exactly as specified above and in the five cases. Not up for revision.
2. **Energy is hybrid (recipe overrides machine)** — revised to match Modern
   Industrialization, which puts `eu`/`duration` on each recipe. A recipe's
   `euPerTick` is the EU/tick its machine draws while making it and **overrides**
   the machine's `baseEnergyConsumption`; a recipe without it falls back to the
   machine draw, so pre-existing machine-energy projects keep working. A
   machine's `energySource` (electric / liquid / fuel) still picks the carrier
   consumed, and a machine carries a MI-style power ceiling (`voltageTier` +
   `maxEuPerTick`) that clamps overclock so `euPerTick × overclock ≤
   maxEuPerTick`.
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

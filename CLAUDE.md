# Process Simulation — Production Chain Planner

A desktop app for designing and balancing production systems as node graphs.
Define items, machines, recipes, and pipes; wire them into chains; the app reports
supply vs. demand — shortfall, overflow, energy balance — for a single machine, a
connected process, a grouped system, or the whole world.

This file is the working reference. A fuller functional spec was provided
separately and is not committed; the parts that matter day-to-day (model,
formulas, priority rules) are below. **If the spec and this file ever disagree,
the spec wins — update this file to match.**

## Stack

A buildless client-side UI over a thin Python backend.

- **Backend:** Python 3.11+. Rate-balance engine, data model, and file IO are
  pure Python, no UI dependency, under `process_sim/`.
- **UI:** one hand-written `ui/index.html` — vanilla JS, a hand-rolled SVG/HTML
  canvas (pan/zoom/drag), inspectors, balance overlay. **No build step, no npm,
  no framework, no `node_modules`.** Edit the file and refresh.
- **Bridge:** `backend.Api` (re-exported from `process_sim.api`) holds the working
  World and marshals JSON ↔ model. One instance is bound two ways; the UI's
  `callApi` auto-detects the live transport and unwraps the `{ok,data}` envelope:
  - `app.py` — the **pywebview desktop app**; UI calls
    `window.pywebview.api.<method>(...)`.
  - `server.py` — a **stdlib HTTP server** (`POST /api/<method>`, body
    `{"args":[...]}`) for any browser on the LAN. Run exactly one process.
- **Persistence:** the working World autosaves to
  `~/.process_sim/autosave.psim.json` (`PROCESS_SIM_HOME` overrides the dir).
  Autosave is **on a timer / explicit save, not per-edit** (see Performance).
  Project files are JSON (the whole World). An **imported catalog** is also saved
  to `~/.process_sim/libraries/*.psim-lib.json` and merged into the World at
  startup and on project load (`ConflictPolicy.UPDATE`), so the machine catalog is
  always available without re-importing. `export_standalone.py` bakes the World
  into a single view-only HTML.
- **Tests:** pytest for engine, model, IO, api, and app/server. The UI is
  validated by driving `server.py` (the real engine) in a headless browser.

Styling target: `STYLE_GUIDE.md` (`:root` tokens, pasted verbatim). Fonts ship as
local `woff2` under `ui/fonts/` (offline-first). The architecture mirrors a simple
buildless client-side app over a thin Python backend.

## Project state

The headless core (`process_sim/`: `engine/` rate-balance, `model/` dataclasses,
`io/` project/library/MI-import, `api.py` the JSON bridge) is complete and
pytest-covered. The UI is the single buildless `ui/index.html`: node-graph canvas
(place/move/delete machines, wire/delete pipes, pipe junctions, pan/zoom/fit),
per-kind inspectors with sparse-field discipline (item / recipe / machineDef /
node / pipe / pipeNode / system), the input/output recipe finder, the balance
overlay (status bar + chip + duty %, flow-weighted/dashed edges, floating readout
with a world/process/node/system scope switcher and ranked bottlenecks), the
collapsible/searchable library rail, systems (shift-select → Group,
collapse/expand), undo (Ctrl+Z), recipe import, project save/load, and the
**Run** / **Save** controls.

Energy is the carrier-tiers model (routed fuel → steam → electricity, per-recipe
hybrid energy, MI power tiers clamping overclock, per-node duty cycles, transparent
storage buffers, membership-aware tag flow). The "Domain model", "Carrier tiers",
"Computation", and "Resolved decisions" sections below are authoritative.

## Performance & persistence model

The model is steady-state, so a change to the graph does not re-solve the balance
on its own. Two deliberate decoupling points keep big worlds (hundreds of
machines) responsive:

- **Recompute is on demand.** An edit updates the World and re-renders the canvas,
  but does **not** recompute the balance — the UI marks the overlay stale and the
  user presses **Run** to re-solve. (Switching scope or selecting a node in a
  focused scope still recomputes, since that is a deliberate "show me this".)
- **Autosave is on a timer, not per-edit.** `apply_edit` / `set_world` mark the
  World dirty; the write happens on a 10-minute timer, on an explicit **Save**
  (`save_autosave`), best-effort on page unload, and authoritatively when the
  desktop window closes / the server stops. Imports and `load_project` persist
  immediately (infrequent, major). `save_autosave` is a no-op on a clean World.

Engine optimization (`engine/aggregate.py`): the per-node duty **probe** routes
only the supply keys that can satisfy the probed node's own inputs (not every
supply key in the scope), and single-edge carriers take a `min(supply, demand,
throughput)` fast path. This turns a long chain from O(N²) to roughly O(N) per
solve. A single carrier shared by many sinks (one power/steam trunk → N machines)
is still O(N²) but with a small constant — fine on demand for hundreds of nodes.

## API surface (stable contract)

`api.py` exposes one `Api` class (re-exported as `backend.Api`); one long-lived
instance is bound by the desktop app and the server. Arguments arrive as plain
JSON and return values serialize straight back. The class is **marshaling only** —
it converts JSON ↔ model and delegates to `engine/` and `io/`; it holds no rate
math, flow resolution, or file-format logic. Its one piece of state is the current
World; edits mutate it, reads/recomputes run against it.

**Result envelope.** Every method returns `{"ok":true,"data":<payload>}` on
success or `{"ok":false,"error":{"type":"<ErrorName>","message":"<str>"}}` on an
expected failure (malformed file, unreadable payload, bad request shape, unknown
entity kind, id collision under the `error` policy). Payloads use the model's
camelCase wire format.

| method | arguments | `data` payload |
|---|---|---|
| `get_world()` | — | World |
| `set_world(world)` | World dict | World (normalized) |
| `apply_edit(edit)` | `{op, kind, entity\|id}` | World after the edit |
| `save_autosave()` | — | `{saved, dirty, path}` |
| `load_project(path)` | path string | World |
| `save_project(path)` | path string | `{"path": str}` |
| `recompute(request)` | `{scope, nodeId?\|systemId?}` | ScopeReport |
| `export_library(options)` | `{name?, itemIds?, recipeIds?, machineDefIds?}` | Library envelope |
| `save_library(path, options)` | path string, options dict | `{"path": str}` |
| `load_library(path)` | path string | Library envelope |
| `import_library(library, on_conflict="rename")` | Library envelope, policy | `{world, idRemap, skippedIds, updatedIds}` |
| `import_library_file(path, on_conflict="rename")` | path string, policy | `{world, idRemap, skippedIds, updatedIds}` |
| `import_recipe_files(paths, options)` | list of paths, `{machineTypes?, name?, onConflict?}` | `{world, …, stats}` |
| `import_mi_dir(path, options)` | path string, `{machineTypes?, name?, onConflict?}` | `{world, …, stats}` |

- **`edit`** is `{"op":"upsert","kind":<kind>,"entity":{...}}` (replaces the entity
  of that id in place, else appends) or `{"op":"remove","kind":<kind>,"id":<id>}`.
  `kind` ∈ `item`, `recipe`, `machineDef`, `node`, `pipe`, `pipeNode`, `system`.
  Editing recomputes nothing and saves nothing by itself — it marks the World
  dirty; the UI runs the sim and saves on demand. A `node` entity carries
  `recipeIds` (a list — a node runs every recipe in it, summed); a legacy single
  `recipeId` is still read from older saves.
- **`recompute` scopes:** `world` (default), `node` (`nodeId` — a single machine),
  `process` (`nodeId` — its connected component via `trace_process`), `system`
  (`systemId`). Payload is `ScopeReport.to_dict()` (external I/O, per-node
  balances, net energy, ranked bottlenecks, and `pipeFlows` — per-pipe resolved
  flow + a starved-edge `shortfall` flag).
- **`on_conflict`** is a `ConflictPolicy`: `rename` (default), `skip`, `update`,
  `error`. `update` overwrites the World's entity of a colliding id **in place**
  (reported in `updatedIds`). Both importers default to `update` — MI entities are
  canonical reference data keyed by stable ids, so re-importing is idempotent (no
  duplication, nothing renamed) **and refreshes** values in place without
  disturbing nodes/pipes; new recipes are still added.

### Importing recipes

Two entry points share one parser and one persistence path (the imported catalog
is both saved to the library folder and baked into the autosaved World, so it
auto-loads on later launches with **no re-import**):

- **`import_mi_dir`** reads an **extracted Modern Industrialization jar directory**
  (`io/mi_import.py`). By default it discovers and imports **every** powered-machine
  recipe type it finds — any namespaced type (not `minecraft:`, not a bare vanilla
  type) whose recipes carry a machine shape — so a different mod with the same
  recipe structure imports with no code change (`DEFAULT_MACHINE_TYPES` is kept
  only as a ready-made subset). Pass `machineTypes` (bare names) to narrow it.
- **`import_recipe_files`** is the **generic** importer: `paths` is any mix of
  files and directories (a whole jar, a recipe folder, or a hand-picked set of
  individual MI-format recipe `.json` files from any mod). Directories are scanned
  recursively; support data (lang names, fuel maps, tag definitions) is loaded
  best-effort from the common ancestor when it is a jar root and simply absent
  otherwise. Recipe ids are stable (relative to the common base), so re-importing
  the same files updates in place.

The parser normalizes MI's shapes (object-or-list I/O, the forge hammer's
`ingredient`/`result` shape, bare/namespaced ids, `tag` refs → `isTag` items,
`probability`, default `amount` 1), converts units (ticks → seconds, `eu` kept
per-tick on the recipe), merges every mod's `lang` for display names, reads item
fuel `eu` from NeoForge's `furnace_fuels` burn-time × 20 (coal's 1600 burn ticks =
32 000 EU) and fluid `eu` from `fluid_fuels` (already per-mB), and groups recipes
by `type` into electric-consumer machine defs with a data-derived slot layout and
the `MACHINE_CATALOG`'s tier ceiling (`voltageTier` + `maxEuPerTick`; single-block
electric machines are LV = 32 EU/t; multiblocks import unclamped). The generic
electric def is named off the jar's `electric_<type>` block where one exists. A
recipe type run by distinct **named blocks** (`MACHINE_VARIANTS` — steam/electric
quarry, steam/electric blast furnace, bronze/steel/electric furnace, and the
steam-only Coke Oven) yields those variant defs (each on its own carrier and tier)
instead of one generic def. Furnaces run vanilla `minecraft:smelting` recipes (the
one vanilla type mapped to a machine, `_VANILLA_MACHINE_TYPES`); a smelting recipe
carries no `eu`, so the furnace def supplies a base draw and `cookingtime` is its
duration. For machines with **steam tiers** (`STEAM_MACHINE_TIERS`) it adds a
bronze/steel def beside the electric one, powered by steam (`energySource: liquid`,
`sourceItemId` steam) and clamped to the tier ceiling (bronze 2, steel 4 EU/t). It
also adds the non-recipe **energy-generation** converter defs (`ENERGY_MACHINES`:
boilers, steam/plasma turbines, diesel generators) and the routed-electricity
carrier, setting imported consumers to draw it; the **water pumps**
(`PUMP_MACHINES` — bronze/steel on steam, electric on electricity, each a water
source at bronze 200 / steel 400 / electric 3200 mB/s). Pass `include_energy:false`
to skip the energy machines **and** pumps (they share the carriers). It always adds
the **storage** defs (`STORAGE_MACHINES`, with jar capacities as editable buffer
annotations) and the recipe-less **special** machines (`SPECIAL_MACHINES` — Nuclear
Reactor, Replicator: inert placeable defs). It resolves tag membership (registering
missing member items) and fills the vanilla `c:` material tags the jar references
but NeoForge defines. `stats` reports counts (files scanned, recipes
imported/skipped, items, tags, member items added, defs, steam/energy/pump/storage/
special, per-type).

A changed shape here is a deliberate, documented contract bump — the UI's
`callApi` wrappers mirror this table.

### Commands

```bash
pip install pywebview            # the only runtime dependency
python app.py                    # open the desktop window
python app.py path/to/project.psim.json   # open with a project loaded
```

```bash
python server.py                 # http://localhost:8000
python server.py 9000            # custom port
```

Edit the UI: change `ui/index.html` and refresh. No build, nothing to reinstall.

```bash
pip install pytest ruff
pytest                           # engine, model, io, api, app/server
ruff check . && ruff format --check .
```

`pyproject.toml` sets `pythonpath = ["."]` and `testpaths = ["tests"]`. Bake a
view-only copy with `python export_standalone.py` (→ `dist/process_sim.html`).

```
app.py               # pywebview desktop entry (binds backend.Api)
server.py            # stdlib HTTP server (POST /api/<method>)
backend.py           # Api home + build_api factory
export_standalone.py # bake the World into one view-only HTML
process_sim/
  engine/            # rate-balance computation, pure Python
  model/             # dataclasses: Item, Recipe, MachineDef, MachineNode, Pipe, System, World
  io/                # load/save projects, library + MI import
  api.py             # the JSON API surface — thin
ui/index.html        # the entire UI
ui/fonts/            # local woff2 (offline)
examples/            # a sample project
tests/               # pytest
```

## Architecture rules

- **The engine does not import the UI or pywebview.** All rate/balance computation
  lives in `process_sim/engine/` and `process_sim/model/` and runs headless. The
  UI reaches it only through `backend.Api`. Keep `api.py` thin — it marshals, it
  does not hold logic.
- **No build step.** The UI is one hand-written `ui/index.html`. Do not reintroduce
  npm, a bundler, a framework, or `node_modules`.
- **Offline-first.** No network calls at runtime. All assets are local.
- **Recompute is steady-state and on demand** (see Performance). Every machine and
  pipe runs continuously at its effective rate; there is no event loop over
  discrete items.
- **Sparse, optional fields.** Most properties are optional. Unset means *absent*,
  not zero. Inspectors show only the fields an entity uses. Computation skips
  absent fields rather than substituting zero.

## Domain model

- **Item** — a tracked resource. Category is `solid`, `liquid`, or `energy`. Energy
  (unit EU) is a normal item so it can appear in recipes and balances. An item may
  carry a stored-energy value `eu` (its fuel content); a fuel with `eu = 1000`
  consumed at 2 EU/s lasts 500 s. Items without `eu` are not fuels.
- **Stack** — a `{resource, quantity}` pair, per recipe cycle unless noted. The
  resource is a concrete `itemId` **or** a `tag` (a group reference like
  `c:gears/iron` backed by an `Item` with `isTag:true` whose `members` list the
  concrete items). A stack may carry an optional `probability` (expected chance;
  absent = always, `0` = a referenced-but-unconsumed catalyst).
- **Recipe** — input stacks → output stacks over a production time. Energy is
  **hybrid**: a recipe may carry its own `euPerTick` (overrides the machine's
  `baseEnergyConsumption` default); without it, it falls back to the machine draw.
  Optional `machineType` names the machine kind that runs it (MI's recipe `type`).
- **MachineDef** — a template: `energySource` ∈ `electric` / `liquid` / `fuel`
  (absent = passive) says how it is powered; `sourceItemId` names the carrier it
  consumes. A `fuel` converter may leave `sourceItemId` **unset** (an MI boiler —
  the player picks the fuel): the engine meters its `fuelDrain` (EU/s) against
  whatever eu-bearing **fuel item** is wired in, consuming `fuelDrain / item.eu`
  units per second; with no fuel wired it stalls (one converter burns one fuel —
  the best-supplied wired candidate). A **consumer** sets `baseEnergyConsumption`
  (EU/s). A **converter** sets `outputItemId` + `maxFluidRate` and optional
  `efficiency` (`w`, waste fraction): it spends its source's energy to turn an
  optional `inputItemId` **material** into the output carrier (boiler burns fuel to
  heat water → steam; turbine spends steam → electricity). The material is consumed
  at `materialRatio` output units per material unit (default `1`). A **storage** def
  sets `storage` (`{category, capacity}`) and is a transparent buffer. Carriers are
  just items (content); nothing here names a specific fluid.
- **MachineNode** — a placed machine bound to **one or more recipes**, an overclock
  multiplier, and a parallel count. `recipeIds` is canonical (a node's I/O and
  energy are the **sum** of every bound recipe); legacy single `recipeId` is still
  read, and `recipe_list` returns whichever is set. The boundary flag `assumeInputs`
  (manual insertion — treat any unsupplied input as available) lets an edge machine
  run on hand-fed feedstock. (`assumeOutputs` remains for back-compat but no longer
  affects the balance — output never throttles a machine.) Default off.
- **Pipe** — the graph edge. Carries one category between two machine ports; has
  storage capacity, movement capacity, an overclock multiplier, and per-end roles
  and priorities. **Liquid pipes carry an unlimited rate** — only solids and
  electricity are throughput-capped by `movementCapacity`.
- **PipeNode** — a pipe *junction*: a routing dot (one `category`, `x`/`y`) with no
  recipe/def/slots/energy. It diverges or merges pipes of one category (a trunk
  fans out to many consumers). In the balance it is a **transparent passthrough**,
  spliced out of the flow graph by the same contraction that dissolves storage
  buffers (`X → junction → Y` resolves as a direct `X → Y` throttled by the tighter
  pipe; `trace_process` routes through it). A pipe end at a junction uses role `io`.
  Resolved flow is reported on every segment of a spliced chain.
- **System** — a user-defined group of nodes treated as one unit, with exposed
  ports and aggregated stats. Collapsible, nestable, connectable.
- **World** — all items, recipes, machine defs, nodes, pipes, pipe nodes, systems.

A slot holds one stack; `slots × stackSize` per category and direction sets how
much a machine can hold.

## Computation (all rates per second)

For a node running recipe `R` at `overclock` with parallel `count` (a node bound to
several recipes runs each and **sums** I/O and energy):

`overclock` is first **clamped** by the power ceiling: `overclock ≤ maxEuPerTick /
euPerTick` when both are set — a clamped machine runs its recipe slower (speed and
energy move together). The clamp is **silent** (the machine's real rate at its
tier, not an error) and is applied per recipe. A term `s` carries an optional
`probability` (absent = 1); `probability = 0` is a catalyst (left absent, never a
zero rate):

```
cyclesPerSec   = overclock / R.timeSeconds
outputRate(s)  = s.quantity * p(s) * cyclesPerSec * count   # p(s)=probability||1
inputDemand(s) = s.quantity * p(s) * cyclesPerSec * count
```

Energy per node — the draw is **hybrid**: a recipe's `euPerTick` overrides the
machine's `baseEnergyConsumption`. `euPerTick` is per **tick** (×20 for the
per-second draw); `baseEnergyConsumption` is already per-second. Both draw and
output are **routed carriers** (items); the scalar energy terms are derived from
those energy-category flows. An absent term stays absent:

```
euPerTick = R.euPerTick  (else baseEnergyConsumption / 20)   # per-tick draw
consume   = euPerTick * 20 * overclock * count               # routed as carrier
produce   = (converter energy-carrier output)                # see Carrier tiers
netEnergy = produce - consume     # derived from energy-category carriers
```

### Carrier tiers (fuel → steam → electricity)

Energy flows as a chain of carriers: a **fuel** item (stored `eu`) is burned by a
boiler into **steam** (a liquid); a turbine consumes steam and produces
**electricity** (the `energy` category). **All three carriers route as items**
through pipes, subject to movement capacity and priority — electricity has a
transfer limit from its electric connections (default 5120 EU/s = 256 EU/t × 20).
`energySource` picks the carrier consumed; `sourceItemId` names it. An electric
machine with no carrier chosen falls back to a scalar mains draw (no transfer
limit). Electricity is kept out of the material feedstock/yield lists (reported as
`netEnergy`), but a power shortage still shows as throttled duty + a bottleneck.

The fuel→output math (`engine/energy.py`): with max rate `R`, fluid drawn `D`
(capped at `R`), fuel energy `E`, efficiency `η`:

```
fluidOut     = D                   # output-fluid units
fuelDrain    = D / η               # EU of fuel consumed
burnTime     = E · η / D           # how long one fuel unit lasts
usableOutput = E · η               # total output from one fuel unit
```

Most machines are lossless (`η = 1`, exact 1:1). An **efficiency** `w` (waste
fraction) applies to heat generated but not delivered when the fluid isn't fully
drawn:

```
η         = D / (D + w·(R − D))
fuelDrain = D + w·(R − D)          # = w·R + (1−w)·D, well-defined at D = 0
wasted    = w·(R − D)
```

Default `w = 0`. The default-content **Large Steam Boiler** sets `w = 0.8`,
`R = 256` — it burns `w·R` fuel even at zero draw (idles hot) and loses `w` of any
surplus, so η reaches 1 only at full load. Default content models the MI boiler,
whose `materialRatio` is 16 (1 mB water → 16 mB steam, validated against
`SteamHeaterComponent` / `MIServerConfig.steamPerWater`); steam is 1 mB = 1 EU, so
a machine at 1 EU/t draws 1 mB/t = 20 mB/s of steam.

**Units.** The engine is canonical **per second** (a tick is 1/20 s). The UI
edits/shows energy and converter output rates **per tick** (EU/t, mB/t) while
recipe amounts and material flows read **per second** — a ×20 conversion applied
only at the UI boundary, so the engine and its tests stay per-second.

Pipes are **pure transfer vehicles**: for solids and electricity,
`movementCapacity × overclock` caps sustained flow; **liquid pipes are uncapped**.
Pipe storage is ignored in the steady-state balance (it only buffers transients).

Per-item balance at a node:

```
input:  shortfall = max(0, inputDemand - supplied)
output: overflow  = max(0, outputRate  - demanded)
```

Each pipe's flow is `min(upstream available, downstream demand, pipeThroughput)`.

Aggregation over a system, process, or world is **purely input-driven**: every
machine runs at the maximum its delivered inputs allow, and **output never throttles
a machine**. Unconsumed output is **surplus** — assumed routed to storage or voided
— reported as overflow (a good state), not backpressure. So a source whose inputs
are available runs at 100% *independently* of how much its consumers take or how
many siblings share its supply.

Each node's **duty cycle** (`nodeDuty`) is solved by a relaxation in `compute_scope`
(`_relax_inputs`): start every runnable node at ceiling 1 and iterate `duty_i =
min(1, availability_i)`, where availability is the fraction of its inputs actually
delivered. Availability is measured by a **per-node probe**: to find how much of
each input node *i* can obtain, route once with *i* bidding its full rate for every
input while every other node bids at its current duty. Because only *i* bids full, a
plentiful shared carrier is never over-subscribed by nodes throttled elsewhere (so
it is never mistaken for scarce); because *i* bids full and not its shrinking duty,
it re-probes its true max-min share and climbs when a sibling frees capacity (no
ratchet collapse); two equally scarce inputs are each measured at full, so neither
is masked. A **feedback cycle** (a boiler whose steam powers the pump that feeds it
water) holds at full because each side supplies the other. Iterated to a fixed
point. (For performance, the probe routes only the supply keys that satisfy *i*'s
own inputs — the result for *i* is exact since other keys never reach it.)
`assumeInputs` treats any unsupplied input as available. Placing *just* a converter
(nothing wired) is a shortfall, not an overflow of output it can't make.

The flow resolution behind the probe (`_resolve_item`) resolves each carrier over
**residual rounds**, not one pass: a single even insert/extract split strands
feasible capacity when several sources feed many sinks, so it re-runs on the
residual supply/demand until no more can be delivered — a sink reads as starved only
when the network genuinely cannot feed it. (A single-edge carrier short-circuits to
`min(supply, demand, throughput)`.)

A node held below 1 is input-limited; the inputs it **cannot obtain in full** at its
operating point are its **shortfalls** (and the ranked **bottlenecks**), read off
the probe. A plentiful, reachable carrier is never blamed when a different input is
the real limit; an input that is globally plentiful but simply not routed to this
node *is* flagged, because the probe measures what actually reaches the node.
Storage (buffer) nodes and pipe junctions are transparent passthroughs.

**Each scope is a standalone slice**, not a filtered view. An input is **feedstock**
(assumed hand-fed, never a throttle, reported under external inputs) when it has no
producer *inside the scope* but **is produced elsewhere in the world** — the scope
boundary has merely cut its supply chain. This makes a `node` or `system` view a
genuine slice. An input that **nothing in the whole world produces** is not
feedstock: it stays unmet and the node stalls (unless `assumeInputs`), so the world
scope is unchanged (no producer in scope = no producer anywhere).

The scope reports: `netEnergy` (sum of duty-scaled energy terms); **external
inputs** = feedstock the running scope consumes across its boundary; **external
outputs** = the **yield** (everything produced that is not consumed inside this
scope); and the largest input shortfalls as **bottlenecks**.

## Pipe priority — read this before touching flow resolution

Each pipe end has an **insert priority** and an **extract priority**, each a signed
scalar −128…127, default 0, set independently. The resolution is **asymmetric**, and
that asymmetry is intentional:

- **Insert contention** (one source → several destinations): fill in **descending**
  insert priority — higher fills first. Equal priority splits evenly, round-robin in
  batches sized by pipe speed (movement capacity).
- **Extract contention** (several sources → one destination): drain in **ascending**
  extract priority — lower drains first. Equal priority splits evenly.
- **Self-loop guard**: an `io` end that extracts from and inserts into the same
  buffer will not re-insert what it pulled when its insert priority is below its
  extract priority.

Cases that must hold:

1. Furnace into two chests, main insert 10, overflow insert 0 → main fills first,
   spills to overflow only when full.
2. One stream into three machines all at insert 0 → even round-robin.
3. One `io` pipe filtering a barrel, extract 5 insert 0 → won't re-insert
   (insert < extract).
4. Two sources into one machine, A extract 0, B extract 10 → A drains fully before
   B is touched.
5. A chest at insert 127, everything else ≤ 0 → always filled first.

Insert favors higher, extract favors lower. Don't "simplify" this into one rule.

## Overclock

A single scalar multiplier, default 1, applied linearly. On machines it scales
cycles/sec, energy consumption, and energy production together. On pipes it scales
movement capacity. Optional min/max bounds per definition for UI clamping. No
non-linear curve. The engine additionally clamps a machine's effective overclock to
its power ceiling (`euPerTick × overclock ≤ maxEuPerTick`) — a machine can't run
faster than its tier/upgrades can feed it. This clamp is **silent** (the real rate,
not an error state).

## Resolved decisions

Build to these.

1. **Pipe priority asymmetry is final** — insert favors higher, extract favors
   lower, exactly as the five cases specify. Not up for revision.
2. **Energy is hybrid (recipe overrides machine)** — a recipe's `euPerTick`
   overrides the machine's `baseEnergyConsumption`; a recipe without it falls back
   to the machine draw, so machine-energy projects keep working. `energySource`
   picks the carrier; a MI-style power ceiling (`voltageTier` + `maxEuPerTick`)
   clamps overclock.
3. **All carriers are routed items (electricity included)** — fuel items store `eu`;
   a boiler burns fuel into steam, a turbine turns steam into electricity. All three
   flow through pipes under the normal movement-capacity and priority rules, so
   electricity is transfer-limited (default 5120 EU/s). `netEnergy` is derived from
   the energy-category carriers; electricity is excluded from feedstock/yield lists.
   Pipe storage is ignored.
4. **Round-robin batch size follows pipe speed** — the equal-priority split is
   batched by the pipe's movement capacity, not a separate fixed constant.
5. **Display units auto-scale per category** — store canonical units internally; the
   UI auto-scales for readability.

## Visual style

The look is defined in `STYLE_GUIDE.md` ("Instrument Panel on Dark Glass"): a deep
slate field, one signal-cyan accent for the active path, phosphor status colors,
mono type for every id/label/metric, a display sans for everything else. Read that
file before UI work and use its `:root` tokens as the only palette. Two context
adjustments: bundle fonts locally (no Google Fonts `@import`), and verify
`backdrop-filter` / font smoothing across platform webviews (fall back to a solid
translucent `--panel` where weak).

Canvas nodes are `div`s, edges are SVG paths (color by category, thickness by flow,
dashed for shortfall). Apply the cyan glow to the selected/live element only.

Domain-state palette (the guide doesn't cover these):

- **Status colors** — a node's **overall** colour is a *run band* that is a plain
  function of its duty cycle: **green** (`--s-ref`) = running at full rate (`ok`);
  **yellow** (`--s-learn`) = `throttled` (running, held below full by an input that
  can't keep up — normal, not an error); **red** (`--s-bad`) = `stalled` (not
  running). There is **no** separate surplus/overflow run band — surplus output is
  normal in the input-driven model and reported as **yield**. Per-**item** balances
  still carry finer verdicts: an input is `shortfall` → red only when it is the
  genuine limiter (drives the dashed starved edge); an output with surplus is
  `overflow` → blue. Energy-carrier surplus is `netEnergy`. Cyan (`--spine`) stays
  reserved for the selected/live element.
- **Category colors** (default, adjustable): solid → silver (`--s-teach`), liquid →
  blue (`--s-indep`), energy → gold (`--subedge`). A converter's output fluid rides
  the liquid color. Cyan, and red/green/yellow, stay reserved.

## Out of scope for v1

Tick-by-tick simulation with real backpressure, probabilistic recipe outputs (use
expected rates), and any multiplayer or cloud sync.

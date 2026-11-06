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

- Python 3.11+
- PySide6 (Qt for Python) for the UI
- `QGraphicsView` / `QGraphicsScene` for the node canvas
- Standard-library `json` for project files
- `pytest` for tests

No web frontend. The node canvas is drawn in Qt, not HTML.

## Project state

Nothing is scaffolded yet beyond this file, the README, and the license. There is
no package layout, no dependency manifest, and no run/test commands wired up. Add
those as the first development step and document the real commands here once they
exist — don't invent entry points that aren't there.

Intended layout (adjust as it takes shape):

```
process_sim/
  engine/        # rate-balance computation, pure Python, no Qt
  model/         # data classes: Item, Recipe, MachineDef, MachineNode, Pipe, System, World
  io/            # load/save project files, library import/export
  ui/            # PySide6 widgets, canvas, inspectors, editors
  app.py         # entry point
tests/
```

## Architecture rules

- **The engine does not import Qt.** All rate/balance computation lives in
  `engine/` and `model/` and runs without a UI. The UI reads from the engine; the
  engine never reaches into the UI. This keeps the hard part testable.
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
- **Stack** — an `{item, quantity}` pair, per recipe cycle unless noted.
- **Recipe** — input stacks → output stacks over a production time, with optional
  energy production/consumption.
- **MachineDef** — a template: energy behavior, allowed recipes, slot layout per
  category and direction, stack sizes, overclock bounds.
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

Energy per node (omit terms whose fields are unset):

```
energyConsumption = baseEnergyConsumption * overclock * count  (+ recipe.energyConsumption term if set)
energyProduction  = baseEnergyProduction  * overclock * count  (+ recipe.energyProduction  term if set)
netEnergy         = energyProduction - energyConsumption
```

Machine-base and recipe-specified energy are additive by default (open question —
see below).

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
  evenly, round-robin in stack-sized batches.
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

## Open questions (not yet decided)

1. Confirm the insert-higher / extract-lower asymmetry above is what we want.
2. When a machine has base energy *and* its recipe specifies energy — additive
   (current assumption) or does the recipe override?
3. Model EU as a pipe-carried resource (obeys movement capacity and priority) or
   as a direct lossless link? Default assumption: treat it like any carried
   resource.
4. Equal-priority round-robin batch size — stack size, a fixed batch, or purely
   proportional?
5. Canonical units per category and whether display auto-scales.

## Visual style

The look is defined in `STYLE_GUIDE.md` (edunode's "Instrument Panel on Dark
Glass"): a deep slate field, one signal-cyan accent for the active path, phosphor
status colors, mono type for every id/label/metric, and a display sans for
everything else. Read that file before doing UI work and use its `:root` tokens as
the only palette — don't introduce new hues.

That guide is written for web/CSS. We're on Qt, so it translates rather than
copies:

- **Tokens** — define the `:root` colors once as a Python module of constants and
  reuse them everywhere: in QSS stylesheet strings, in `QPalette`, and in canvas
  painting. The hex values are the source of truth; keep them identical to the
  guide.
- **QSS** — Qt Style Sheets cover borders, radii, backgrounds, padding, and font
  properties, so most component recipes (segmented control, tool button, chips,
  inputs, meters, list rows) port over almost directly.
- **Fonts** — don't use the Google Fonts `@import`. Bundle IBM Plex Mono and Space
  Grotesk (`.ttf`) in the repo and load them with `QFontDatabase.addApplicationFont`
  at startup.
- **No Qt equivalent:** `backdrop-filter` (glass blur) and CSS `box-shadow` aren't
  supported by QSS. Approximate glow and elevation with
  `QGraphicsDropShadowEffect`, and treat "dark glass" panels as solid translucent
  `--panel` fills rather than real backdrop blur unless a specific widget warrants
  `QGraphicsBlurEffect`. The radial background glow can be painted in a
  `paintEvent` with `QRadialGradient`.
- **Canvas** — ports, pipes, and node bodies are custom-painted `QGraphicsItem`s,
  not DOM. Pull their stroke/fill colors from the same token constants; apply the
  cyan glow to the selected/live element only.

Still to decide before the palette is complete (the guide doesn't cover our
domain states directly):

- **Category colors** (solid / liquid / energy edges and ports) — the guide
  reserves cyan for "active/selected" and gold as the single secondary accent, so
  categories should come from the phosphor set (`--s-*`). Assignments aren't fixed
  yet.
- **Status colors** (OK / shortfall / overflow / stalled / unpowered) — OK → green
  (`--s-ref`), stalled/unpowered → grey (`--s-not`) map cleanly. Shortfall is the
  problem state and the spec wanted red, but this palette has **no red**; either
  add one deliberately or map shortfall to yellow (`--s-learn`). Overflow is
  open too. Decide these together.

## Out of scope for v1

Tick-by-tick simulation with real backpressure, probabilistic recipe outputs
(use expected rates), and any multiplayer or cloud sync.

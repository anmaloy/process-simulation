# Examples

Sample project files (`*.psim.json`) you can open with **Load project** (or
`python app.py examples/<file>`).

## `mi_lv_battery_line.psim.json`

A small, self-contained chain generated from a real Modern Industrialization
import (`import_mi_dir`), kept to a handful of entities so it stays readable:

- **assembler** (`mi.machine.assembler`) — an electric machine def at the **LV**
  tier, so `maxEuPerTick = 32` (the overclock ceiling, read from MI's
  `CableTier`).
- two assembler recipes carrying their own `euPerTick` (8 EU/t) and
  `machineType` (`assembler`): one makes `redstone_battery`, the other consumes
  two of them to make a `basic_machine_hull`.
- two placed nodes wired by a solid pipe that carries the battery. The battery
  maker runs `count = 2` so its output (0.2/s) exactly meets the hull maker's
  demand; both run at duty 1.0. Both nodes set `assumeInputs` (their other
  feedstock — including the `c:plates/battery_alloy` **tag** — is hand-fed) and
  the hull maker sets `assumeOutputs` (the hull is the product).

Recomputing the world reports `netEnergy = -480` EU/s (320 for the battery maker
at count 2, 160 for the hull maker), a yield of 0.1 `basic_machine_hull`/s, and
no bottlenecks.

It is a tiny illustrative excerpt of MI's data (two recipes), not a
redistribution of the mod — regenerate or widen it by pointing **Import MI** at
your own extracted MI jar directory.

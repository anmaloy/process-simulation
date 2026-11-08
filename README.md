# Process Simulation

A desktop tool for designing and balancing production chains as node graphs.

Define the items, machines, recipes, and pipes in a production system, wire them
together on a canvas, and the app tells you where it's starved, where it's
overflowing, and whether it has enough energy to run. It works at four scopes: a
single machine, a traced process, a grouped system, or the whole world.

## Model

The planner computes a steady-state rate balance rather than simulating items one
at a time. Every machine and pipe runs continuously at its effective rate, and
the app compares supply against demand for each resource — reporting shortfall
where demand wins and overflow where supply does. Energy is tracked as a resource
like any other.

## Stack

- Python 3.11+ backend — the rate-balance engine, data model, and file IO
  (`process_sim/`), with no UI dependency.
- A single hand-written `ui/index.html` — vanilla JS, a hand-rolled canvas, the
  inspectors, and the balance overlay. **No build step, no npm, no framework.**

The backend (`backend.Api`) is bound two ways: `python app.py` opens a native
pywebview window; `python server.py` serves the same UI and API over HTTP so any
browser on the network can reach it. Offline and file-based — a project saves to a
single local JSON file, the working world auto-saves between launches, and the app
makes no network calls at runtime.

## Run

```bash
pip install pywebview        # only needed for the desktop app
python app.py                # native window
# or, reachable from any browser/device:
python server.py             # http://localhost:8000
```

Editing the UI is just editing `ui/index.html` and refreshing — there is nothing
to build or reinstall.

## Status

Working. The headless engine/model/IO are complete and pytest-covered, and the UI
has full feature parity (canvas, inspectors, balance overlay, systems, undo,
Modern Industrialization import, project save/load). See `CLAUDE.md` for the model
and `STYLE_GUIDE.md` for the visual system.

## License

MIT. See [LICENSE](LICENSE).

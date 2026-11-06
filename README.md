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

- Python 3.11+ backend — the rate-balance engine and data model
- pywebview shell hosting a web UI (Vite + React + TypeScript, React Flow canvas)

Offline and file-based: a project saves to a single local JSON file, and the app
makes no network calls at runtime.

## Status

Early. The repo currently holds the project reference (`CLAUDE.md`), the visual
style guide (`STYLE_GUIDE.md`), and this README; the application itself isn't
scaffolded yet.

## License

MIT. See [LICENSE](LICENSE).

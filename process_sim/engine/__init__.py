"""Rate-balance computation, pure Python (WORKFLOW Phase 2).

Does not import the UI or pywebview — the whole engine runs headless. Layers:

- :mod:`.energy`    — fuel → fluid conversion (isolated; resolved decision #3).
- :mod:`.node`      — per-node rates and energy (P2a).
- :mod:`.balance`   — per-node shortfall / overflow (P2b).
- :mod:`.flow`      — asymmetric pipe-priority resolution (P2c; isolated seam).
- :mod:`.aggregate` — scope reports: external I/O, energy, bottlenecks (P2d).
"""

from .aggregate import (
    Bottleneck,
    PipeFlow,
    ScopeReport,
    compute_scope,
    compute_system,
    compute_world,
    trace_process,
)
from .balance import (
    InputBalance,
    NodeBalance,
    OutputBalance,
    Status,
    balance_node,
)
from .energy import FuelConversion, burn_time, convert_fuel, usable_output
from .flow import (
    Claim,
    pipe_throughput,
    resolve_extract,
    resolve_insert,
    will_reinsert,
)
from .node import NodeComputation, compute_node

__all__ = [
    # energy
    "FuelConversion",
    "convert_fuel",
    "usable_output",
    "burn_time",
    # node
    "NodeComputation",
    "compute_node",
    # balance
    "Status",
    "InputBalance",
    "OutputBalance",
    "NodeBalance",
    "balance_node",
    # flow
    "Claim",
    "resolve_insert",
    "resolve_extract",
    "will_reinsert",
    "pipe_throughput",
    # aggregate
    "Bottleneck",
    "PipeFlow",
    "ScopeReport",
    "compute_scope",
    "compute_world",
    "trace_process",
    "compute_system",
]

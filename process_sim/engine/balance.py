"""Per-node balance (WORKFLOW P2b).

Pure arithmetic given a node's computed rates and the per-item supplied /
demanded amounts resolved by the flow layer::

    input:  shortfall = max(0, inputDemand − supplied)
    output: overflow  = max(0, outputRate  − demanded)

Status mapping is the engine's view of domain state; the UI color map (P8) reads
these names, not raw numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .node import NodeComputation

__all__ = [
    "Status",
    "InputBalance",
    "OutputBalance",
    "NodeBalance",
    "balance_node",
]

_EPS = 1e-9


class Status(StrEnum):
    """Domain state of a node or one of its item balances.

    Two layers use this enum. **Per-item** input/output balances use ``OK`` /
    ``SHORTFALL`` / ``OVERFLOW`` — a single item's demand-vs-supply. A node's
    **overall** status is instead a *run band* read off its duty cycle, so the
    canvas signals what actually needs attention (see ``aggregate``):

    - ``OK`` (green)        — running 50–100% of the rate demand allows.
    - ``THROTTLED`` (yellow) — running, but below 50% (an upstream feed is slower;
      normal, not an error).
    - ``STALLED`` (red)      — not running at all (no feed, no power, or nothing
      downstream).
    - ``CAPPED`` (red)       — over capacity: its overclock is clamped by the
      machine's power-tier ceiling, so it can't reach the set rate.

    ``SHORTFALL`` / ``OVERFLOW`` stay the per-item verdicts (and drive the
    starved-edge dashed-pipe flag); a node is never *overall* ``OVERFLOW`` now,
    since an unconsumed output is a product that leaves as yield.
    """

    OK = "ok"
    SHORTFALL = "shortfall"
    OVERFLOW = "overflow"
    STALLED = "stalled"
    THROTTLED = "throttled"
    CAPPED = "capped"


@dataclass
class InputBalance:
    """One input item's demand vs. what was supplied to the node."""

    item_id: str
    demand: float
    supplied: float
    shortfall: float
    status: Status

    def to_dict(self) -> dict[str, object]:
        return {
            "itemId": self.item_id,
            "demand": self.demand,
            "supplied": self.supplied,
            "shortfall": self.shortfall,
            "status": self.status.value,
        }


@dataclass
class OutputBalance:
    """One output item's rate vs. how much downstream demanded."""

    item_id: str
    rate: float
    demanded: float
    overflow: float
    status: Status

    def to_dict(self) -> dict[str, object]:
        return {
            "itemId": self.item_id,
            "rate": self.rate,
            "demanded": self.demanded,
            "overflow": self.overflow,
            "status": self.status.value,
        }


@dataclass
class NodeBalance:
    """Per-item input/output balances for one node, plus an overall status."""

    node_id: str
    inputs: dict[str, InputBalance] = field(default_factory=dict)
    outputs: dict[str, OutputBalance] = field(default_factory=dict)
    status: Status = Status.OK

    def to_dict(self) -> dict[str, object]:
        return {
            "nodeId": self.node_id,
            "inputs": {k: v.to_dict() for k, v in self.inputs.items()},
            "outputs": {k: v.to_dict() for k, v in self.outputs.items()},
            "status": self.status.value,
        }


def balance_node(
    computation: NodeComputation,
    supplied: dict[str, float] | None = None,
    demanded: dict[str, float] | None = None,
) -> NodeBalance:
    """Balance a node's inputs/outputs against supplied/demanded amounts.

    ``supplied`` maps input item id → amount reaching the node; ``demanded``
    maps output item id → amount drawn downstream. A missing entry is treated as
    ``0`` (nothing supplied / nothing demanded). The overall status is the worst
    of its item statuses — a shortfall (the node is starved) outranks an overflow.
    """

    supplied = supplied or {}
    demanded = demanded or {}

    inputs: dict[str, InputBalance] = {}
    for item_id, demand in computation.inputs.items():
        got = supplied.get(item_id, 0.0)
        short = max(0.0, demand - got)
        status = Status.SHORTFALL if short > _EPS else Status.OK
        inputs[item_id] = InputBalance(item_id, demand, got, short, status)

    outputs: dict[str, OutputBalance] = {}
    for item_id, rate in computation.outputs.items():
        want = demanded.get(item_id, 0.0)
        over = max(0.0, rate - want)
        status = Status.OVERFLOW if over > _EPS else Status.OK
        outputs[item_id] = OutputBalance(item_id, rate, want, over, status)

    overall = Status.OK
    if any(b.status is Status.SHORTFALL for b in inputs.values()):
        overall = Status.SHORTFALL
    elif any(b.status is Status.OVERFLOW for b in outputs.values()):
        overall = Status.OVERFLOW

    return NodeBalance(computation.node_id, inputs, outputs, overall)

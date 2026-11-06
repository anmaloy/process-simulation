"""Aggregation over scopes (WORKFLOW P2d).

A **scope** is a set of nodes: a single machine, a traced process (connected
component), a system (its members), or the whole world. Per scope this computes:

- per-node rates/energy (P2a) and resolved per-item flows along in-scope pipes,
- **external inputs**  — items members demand but aren't supplied internally,
- **external outputs** — items produced beyond internal demand,
- **net energy** — the sum of members' ``netEnergy`` (absent terms omitted),
- **bottlenecks** — the largest internal shortfalls, ranked.

Flow resolution reuses the isolated :mod:`.flow` primitives. The steady-state
flow on an in-scope pipe is the binding of its insert allocation (descending
insert priority at the sink) and its extract allocation (ascending extract
priority at the source); for a plain chain both reduce to
``min(supply, demand, throughput)``. Full-buffer effects are reported as
steady-state states (shortfall / overflow), not run as a tick loop — tick-level
backpressure is out of scope for v1.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..model import (
    Category,
    Pipe,
    PipeEnd,
    PipeEndRole,
    World,
)
from .balance import NodeBalance, balance_node
from .flow import Claim, pipe_throughput, resolve_extract, resolve_insert, will_reinsert
from .node import NodeComputation, compute_node

__all__ = [
    "Bottleneck",
    "ScopeReport",
    "compute_scope",
    "compute_world",
    "trace_process",
    "compute_system",
]

_EPS = 1e-9


@dataclass
class Bottleneck:
    """A ranked internal shortfall: ``node_id`` short ``shortfall`` of ``item_id``."""

    node_id: str
    item_id: str
    shortfall: float

    def to_dict(self) -> dict[str, object]:
        return {
            "nodeId": self.node_id,
            "itemId": self.item_id,
            "shortfall": self.shortfall,
        }


@dataclass
class ScopeReport:
    """Aggregated steady-state stats for one scope."""

    node_ids: list[str]
    external_inputs: dict[str, float] = field(default_factory=dict)
    external_outputs: dict[str, float] = field(default_factory=dict)
    net_energy: float | None = None
    computations: dict[str, NodeComputation] = field(default_factory=dict)
    balances: dict[str, NodeBalance] = field(default_factory=dict)
    bottlenecks: list[Bottleneck] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "nodeIds": list(self.node_ids),
            "externalInputs": dict(self.external_inputs),
            "externalOutputs": dict(self.external_outputs),
            "balances": {k: v.to_dict() for k, v in self.balances.items()},
            "bottlenecks": [b.to_dict() for b in self.bottlenecks],
        }
        if self.net_energy is not None:
            out["netEnergy"] = self.net_energy
        return out


# --------------------------------------------------------------------------- #
# World index — id lookups, built once per call.
# --------------------------------------------------------------------------- #


class _Index:
    def __init__(self, world: World) -> None:
        self.items = {i.id: i for i in world.items}
        self.recipes = {r.id: r for r in world.recipes}
        self.defs = {d.id: d for d in world.machine_defs}
        self.nodes = {n.id: n for n in world.nodes}
        self.pipes = list(world.pipes)
        self.pipe_category = {p.id: p.category for p in world.pipes}
        self.systems = {s.id: s for s in world.systems}


def _category_of(index: _Index, item_id: str) -> Category | None:
    item = index.items.get(item_id)
    return item.category if item else None


# --------------------------------------------------------------------------- #
# Pipe direction — which end extracts (source) and which inserts (sink).
# --------------------------------------------------------------------------- #


def _direction(pipe: Pipe) -> tuple[PipeEnd, PipeEnd] | None:
    """Return ``(source_end, sink_end)`` or ``None`` for a malformed pipe.

    The source end extracts (role ``out`` or ``io``); the sink end inserts (role
    ``in`` or ``io``). An ``io``–``io`` pipe is taken as ``a → b``.
    """

    a, b = pipe.a, pipe.b
    a_src = a.role in (PipeEndRole.OUT, PipeEndRole.IO)
    b_src = b.role in (PipeEndRole.OUT, PipeEndRole.IO)
    a_sink = a.role in (PipeEndRole.IN, PipeEndRole.IO)
    b_sink = b.role in (PipeEndRole.IN, PipeEndRole.IO)

    if a_src and b_sink:
        return a, b
    if b_src and a_sink:
        return b, a
    return None


@dataclass
class _Edge:
    pipe_id: str
    source: str
    sink: str
    throughput: float
    insert_priority: int | None
    extract_priority: int | None


def _edges_in_scope(index: _Index, node_ids: set[str]) -> list[_Edge]:
    """Directed, throughput-capped edges for pipes wholly inside the scope.

    Self-loop edges whose ``io`` end must not re-insert (insert < extract) are
    dropped by the self-loop guard.
    """

    edges: list[_Edge] = []
    for pipe in index.pipes:
        direction = _direction(pipe)
        if direction is None:
            continue
        src_end, sink_end = direction
        if src_end.node_id not in node_ids or sink_end.node_id not in node_ids:
            continue
        if src_end.node_id == sink_end.node_id and not will_reinsert(
            sink_end.insert_priority, src_end.extract_priority
        ):
            continue
        edges.append(
            _Edge(
                pipe_id=pipe.id,
                source=src_end.node_id,
                sink=sink_end.node_id,
                throughput=pipe_throughput(pipe.movement_capacity, pipe.overclock),
                insert_priority=sink_end.insert_priority,
                extract_priority=src_end.extract_priority,
            )
        )
    return edges


# --------------------------------------------------------------------------- #
# Flow resolution per item.
# --------------------------------------------------------------------------- #


def _resolve_item(
    supply: dict[str, float],
    demand: dict[str, float],
    edges: list[_Edge],
) -> list[tuple[_Edge, float]]:
    """Resolve steady-state flow of one item over the scope's edges.

    Returns ``[(edge, flow), ...]``. Each edge's flow binds its insert
    allocation (the source's supply split by descending insert priority across
    its sink edges) with its extract allocation (the sink's demand split by
    ascending extract priority across its source edges). For a plain chain both
    reduce to ``min(supply, demand, throughput)``.
    """

    # Edges that can carry this item: a producer at the source, a consumer at
    # the sink. (Category match is enforced by the caller's edge/item pairing.)
    live = [
        e
        for e in edges
        if supply.get(e.source, 0.0) > _EPS and demand.get(e.sink, 0.0) > _EPS
    ]
    if not live:
        return []

    # Insert side: per source, split its supply across outgoing edges by
    # descending insert priority; each edge capped by throughput and sink demand.
    insert_alloc: dict[int, float] = {}
    by_source: dict[str, list[_Edge]] = {}
    for e in live:
        by_source.setdefault(e.source, []).append(e)
    for source, group in by_source.items():
        claims = [
            Claim(
                id=str(id(e)),
                capacity=min(e.throughput, demand.get(e.sink, 0.0)),
                priority=e.insert_priority,
            )
            for e in group
        ]
        got = resolve_insert(supply.get(source, 0.0), claims)
        for e in group:
            insert_alloc[id(e)] = got[str(id(e))]

    # Extract side: per sink, split its demand across incoming edges by
    # ascending extract priority; each edge capped by throughput and source supply.
    extract_alloc: dict[int, float] = {}
    by_sink: dict[str, list[_Edge]] = {}
    for e in live:
        by_sink.setdefault(e.sink, []).append(e)
    for sink, group in by_sink.items():
        claims = [
            Claim(
                id=str(id(e)),
                capacity=min(e.throughput, supply.get(e.source, 0.0)),
                priority=e.extract_priority,
            )
            for e in group
        ]
        got = resolve_extract(demand.get(sink, 0.0), claims)
        for e in group:
            extract_alloc[id(e)] = got[str(id(e))]

    return [(e, min(insert_alloc[id(e)], extract_alloc[id(e)])) for e in live]


# --------------------------------------------------------------------------- #
# Scope aggregation.
# --------------------------------------------------------------------------- #


def compute_scope(world: World, node_ids: list[str] | None = None) -> ScopeReport:
    """Aggregate steady-state stats over ``node_ids`` (default: the whole world)."""

    index = _Index(world)
    if node_ids is None:
        ids = [n.id for n in world.nodes]
    else:
        ids = [nid for nid in node_ids if nid in index.nodes]
    id_set = set(ids)

    # P2a: per-node computation (full draw for now; a steam draw pass could
    # refine fuel-burning nodes once steam is modelled as a liquid item).
    computations: dict[str, NodeComputation] = {}
    for nid in ids:
        node = index.nodes[nid]
        recipe = index.recipes.get(node.recipe_id) if node.recipe_id else None
        machine_def = index.defs.get(node.machine_def_id)
        if machine_def is None:
            # Unknown def: treat as an empty node so the scope still composes.
            computations[nid] = NodeComputation(node_id=nid, cycles_per_sec=0.0)
            continue
        computations[nid] = compute_node(node, machine_def, recipe)

    # Aggregate supply/demand per item across the scope.
    supply: dict[str, float] = {}
    demand: dict[str, float] = {}
    for comp in computations.values():
        for item_id, rate in comp.outputs.items():
            supply[item_id] = supply.get(item_id, 0.0) + rate
        for item_id, dem in comp.inputs.items():
            demand[item_id] = demand.get(item_id, 0.0) + dem

    edges = _edges_in_scope(index, id_set)

    # Resolve flow per item; accumulate what each node receives / ships.
    supplied_internal: dict[str, dict[str, float]] = {nid: {} for nid in ids}
    demanded_internal: dict[str, dict[str, float]] = {nid: {} for nid in ids}
    internal_flow: dict[str, float] = {}  # item -> total internally routed

    items = set(supply) | set(demand)
    for item_id in items:
        category = _category_of(index, item_id)
        per_source = {nid: computations[nid].outputs.get(item_id, 0.0) for nid in ids}
        per_sink = {nid: computations[nid].inputs.get(item_id, 0.0) for nid in ids}
        item_edges = [
            e for e in edges if index.pipe_category.get(e.pipe_id) == category
        ]
        for edge, flow in _resolve_item(per_source, per_sink, item_edges):
            if flow <= _EPS:
                continue
            supplied_internal[edge.sink][item_id] = (
                supplied_internal[edge.sink].get(item_id, 0.0) + flow
            )
            demanded_internal[edge.source][item_id] = (
                demanded_internal[edge.source].get(item_id, 0.0) + flow
            )
            internal_flow[item_id] = internal_flow.get(item_id, 0.0) + flow

    # External I/O: demand not met internally in, production not used internally out.
    external_inputs: dict[str, float] = {}
    external_outputs: dict[str, float] = {}
    for item_id in items:
        routed = internal_flow.get(item_id, 0.0)
        ext_in = demand.get(item_id, 0.0) - routed
        ext_out = supply.get(item_id, 0.0) - routed
        if ext_in > _EPS:
            external_inputs[item_id] = ext_in
        if ext_out > _EPS:
            external_outputs[item_id] = ext_out

    # Per-node balances. An input item with no internal producer anywhere in
    # scope is a boundary input (assumed available) — not a shortfall; an item
    # that IS produced internally but under-supplied is a genuine bottleneck.
    producible = {item for item, s in supply.items() if s > _EPS}
    balances: dict[str, NodeBalance] = {}
    bottlenecks: list[Bottleneck] = []
    for nid in ids:
        comp = computations[nid]
        got = dict(supplied_internal[nid])
        for item_id in comp.inputs:
            if item_id not in producible:
                got[item_id] = comp.inputs[item_id]  # boundary input, satisfied
        bal = balance_node(comp, got, demanded_internal[nid])
        balances[nid] = bal
        for item_id, ib in bal.inputs.items():
            if ib.shortfall > _EPS and item_id in producible:
                bottlenecks.append(Bottleneck(nid, item_id, ib.shortfall))

    bottlenecks.sort(key=lambda b: b.shortfall, reverse=True)

    net_energy = _sum_net_energy(computations.values())

    return ScopeReport(
        node_ids=ids,
        external_inputs=external_inputs,
        external_outputs=external_outputs,
        net_energy=net_energy,
        computations=computations,
        balances=balances,
        bottlenecks=bottlenecks,
    )


def _sum_net_energy(computations) -> float | None:
    """Sum present ``netEnergy`` terms; ``None`` if no node has an energy term."""

    present = [c.net_energy for c in computations if c.net_energy is not None]
    return sum(present) if present else None


def compute_world(world: World) -> ScopeReport:
    """Whole-world scope."""

    return compute_scope(world, None)


def trace_process(world: World, start_node_id: str) -> list[str]:
    """Node ids of the connected component (a *process*) containing the start.

    Connectivity follows pipes regardless of direction. A process is a scope on
    its own; the world is the composition of its disjoint processes.
    """

    index = _Index(world)
    if start_node_id not in index.nodes:
        return []
    adjacency: dict[str, set[str]] = {n.id: set() for n in world.nodes}
    for pipe in world.pipes:
        a, b = pipe.a.node_id, pipe.b.node_id
        if a in adjacency and b in adjacency:
            adjacency[a].add(b)
            adjacency[b].add(a)
    seen: set[str] = set()
    stack = [start_node_id]
    while stack:
        nid = stack.pop()
        if nid in seen:
            continue
        seen.add(nid)
        stack.extend(adjacency[nid] - seen)
    return [n.id for n in world.nodes if n.id in seen]


def _system_node_ids(index: _Index, system_id: str, seen: set[str]) -> list[str]:
    """Member node ids of a system, including nested systems (cycle-safe)."""

    if system_id in seen:
        return []
    seen.add(system_id)
    system = index.systems.get(system_id)
    if system is None:
        return []
    ids = list(system.member_node_ids)
    for sub in system.member_system_ids:
        ids.extend(_system_node_ids(index, sub, seen))
    # De-dupe while preserving order.
    out: list[str] = []
    for nid in ids:
        if nid not in out and nid in index.nodes:
            out.append(nid)
    return out


def compute_system(world: World, system_id: str) -> ScopeReport:
    """Scope over a system's members (nested systems flattened)."""

    index = _Index(world)
    return compute_scope(world, _system_node_ids(index, system_id, set()))

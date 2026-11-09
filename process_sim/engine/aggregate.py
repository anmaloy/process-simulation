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
    EnergySource,
    Pipe,
    PipeEnd,
    PipeEndRole,
    World,
)
from .balance import InputBalance, NodeBalance, OutputBalance, Status
from .flow import Claim, pipe_throughput, resolve_extract, resolve_insert, will_reinsert
from .node import NodeComputation, compute_node

__all__ = [
    "Bottleneck",
    "PipeFlow",
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
class PipeFlow:
    """Resolved steady-state flow on one in-scope pipe (P8 overlay seam).

    ``flow`` is the total rate routed across the pipe (summed over the items it
    carries). ``shortfall`` is true when the pipe feeds a node that is starved of
    an item this pipe carries — the "downstream end is starved" signal the canvas
    draws as a dashed edge.
    """

    pipe_id: str
    flow: float
    shortfall: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "pipeId": self.pipe_id,
            "flow": self.flow,
            "shortfall": self.shortfall,
        }


@dataclass
class ScopeReport:
    """Aggregated steady-state stats for one scope.

    ``external_inputs`` are the **feedstock** rates the scope must be supplied
    from outside (items with no internal producer); an internally-produced item
    that falls short is *not* a feedstock input — it is reported as reduced
    yield + a bottleneck, since a starved machine simply runs with downtime.
    ``external_outputs`` is the scope's **actual yield** (effective production,
    after duty-cycle throttling, that leaves the scope). ``node_duty`` maps node
    id → its running fraction in ``[0, 1]`` (1 = full rate, < 1 = throttled by a
    starved input).
    """

    node_ids: list[str]
    external_inputs: dict[str, float] = field(default_factory=dict)
    external_outputs: dict[str, float] = field(default_factory=dict)
    net_energy: float | None = None
    computations: dict[str, NodeComputation] = field(default_factory=dict)
    balances: dict[str, NodeBalance] = field(default_factory=dict)
    bottlenecks: list[Bottleneck] = field(default_factory=list)
    pipe_flows: dict[str, PipeFlow] = field(default_factory=dict)
    node_duty: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "nodeIds": list(self.node_ids),
            "externalInputs": dict(self.external_inputs),
            "externalOutputs": dict(self.external_outputs),
            "balances": {k: v.to_dict() for k, v in self.balances.items()},
            "bottlenecks": [b.to_dict() for b in self.bottlenecks],
            "pipeFlows": {k: v.to_dict() for k, v in self.pipe_flows.items()},
            "nodeDuty": dict(self.node_duty),
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
        #: Pipe-junction ids. A junction is a transparent routing waypoint, not a
        #: machine, so it never appears in a scope's node set or balances; it only
        #: carries pipes through and is spliced out of the flow graph (like a
        #: storage buffer) during aggregation.
        self.pipe_nodes = {pn.id for pn in world.pipe_nodes}
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
    #: Every pipe this (possibly spliced) edge traverses. A direct edge lists one
    #: pipe; an edge spliced through storage/junction waypoints lists the whole
    #: chain, so the resolved flow is reported on each segment (a trunk feeding a
    #: junction shows the sum of what its branches carry).
    pipe_ids: tuple[str, ...]
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
        # Liquid pipes carry an unlimited rate — only solids and electricity are
        # throughput-capped. (A stored movement_capacity on a liquid pipe, e.g.
        # from an older save, is ignored.)
        throughput = (
            float("inf")
            if pipe.category is Category.LIQUID
            else pipe_throughput(pipe.movement_capacity, pipe.overclock)
        )
        edges.append(
            _Edge(
                pipe_ids=(pipe.id,),
                source=src_end.node_id,
                sink=sink_end.node_id,
                throughput=throughput,
                insert_priority=sink_end.insert_priority,
                extract_priority=src_end.extract_priority,
            )
        )
    return edges


def _contract_storage(edges: list[_Edge], storage_ids: set[str]) -> list[_Edge]:
    """Dissolve storage (buffer) nodes, splicing each in-edge to each out-edge.

    A buffer is a transparent passthrough in the steady-state balance (capacity
    only absorbs transients), so a chain ``X → buffer → Y`` resolves as a direct
    ``X → Y`` edge throttled by the tighter of the two pipes. The spliced edge
    carries both pipes' ids (so each segment reports its flow in the overlay) and
    takes the sink's insert priority and the source's extract priority. Chains of
    buffers contract iteratively until no edge touches a buffer.
    """

    if not storage_ids:
        return edges
    edges = list(edges)
    for _ in range(len(storage_ids) + 1):  # bound against buffer rings
        active = {s for e in edges for s in (e.source, e.sink) if s in storage_ids}
        if not active:
            break
        s = next(iter(active))
        in_edges = [e for e in edges if e.sink == s]
        out_edges = [e for e in edges if e.source == s]
        rest = [e for e in edges if e.source != s and e.sink != s]
        spliced: list[_Edge] = []
        for ie in in_edges:
            for oe in out_edges:
                if ie.source == oe.sink:
                    continue
                spliced.append(
                    _Edge(
                        pipe_ids=ie.pipe_ids + oe.pipe_ids,
                        source=ie.source,
                        sink=oe.sink,
                        throughput=min(ie.throughput, oe.throughput),
                        insert_priority=oe.insert_priority,
                        extract_priority=ie.extract_priority,
                    )
                )
        edges = rest + spliced
    return [
        e for e in edges if e.source not in storage_ids and e.sink not in storage_ids
    ]


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


_MAX_DUTY_ITERS = 64


def compute_scope(world: World, node_ids: list[str] | None = None) -> ScopeReport:
    """Aggregate steady-state stats over ``node_ids`` (default: the whole world).

    The scope is an **automatic system**: a machine runs only to the extent its
    inputs are actually delivered by connected pipes *and* its outputs are
    actually taken downstream — "if everything is not continuous, it won't run".
    Each node's **duty cycle** is the smaller of those two limits, solved in two
    relaxation passes:

    - **Pass A — the demand ceiling** (``_relax_demand``): the duty each node
      would run at with unlimited inputs, limited only by its output being taken.
      An output nothing consumes is a **product** that leaves as yield (no
      backpressure); an output with a downstream consumer backpressures to that
      consumer's *ceiling* demand, so a water pump only runs as often as the
      boiler would draw water. ``assume_outputs`` (manual extraction) lifts the
      ceiling to 1.
    - **Pass B — the input-limited duty** (``_relax_inputs``): starting from the
      ceiling, each node throttles to the fraction of its inputs actually
      delivered. Crucially a consumer's *demand* on its suppliers stays pinned at
      the ceiling, so a starved consumer keeps pulling at full and never drags its
      supplier to zero. ``assume_inputs`` (manual insertion) treats any unsupplied
      input as available.

    A node held below its ceiling is input-limited; the inputs that can't meet the
    ceiling are its shortfalls (and the bottlenecks), so a *plentiful* carrier is
    never blamed when a different input is the real limit. A source-less FUEL
    converter (an MI boiler) burns whatever eu-bearing fuel is wired to it,
    metered by that fuel's ``eu``; with nothing wired it cannot run. Storage
    (buffer) nodes and pipe junctions are transparent passthroughs.
    ``external_inputs`` is the outside feedstock (manually-inserted inputs),
    ``external_outputs`` the actual yield (products leaving the scope).
    """

    index = _Index(world)
    if node_ids is None:
        ids = [n.id for n in world.nodes]
    else:
        ids = [nid for nid in node_ids if nid in index.nodes]

    id_set = set(ids)

    # Nominal per-node computation (full draw). Energy carriers (steam, fuel)
    # ride in `inputs`/`outputs`; electricity stays the scalar net-energy term.
    computations: dict[str, NodeComputation] = {}
    storage_ids: set[str] = set()
    for nid in ids:
        node = index.nodes[nid]
        recipe = index.recipes.get(node.recipe_id) if node.recipe_id else None
        machine_def = index.defs.get(node.machine_def_id)
        if machine_def is None:
            computations[nid] = NodeComputation(node_id=nid, cycles_per_sec=0.0)
            continue
        if machine_def.storage is not None:
            storage_ids.add(nid)
        computations[nid] = compute_node(node, machine_def, recipe, items=index.items)

    # Directed, throughput-capped, junction/storage-spliced edges. Built here (up
    # front) because fuel resolution below needs to know which producers feed each
    # converter. Pipe junctions are transparent routing waypoints: included in the
    # edge graph so a pipe machine→junction→machine isn't dropped for a non-machine
    # endpoint, then spliced out with the same contraction that dissolves storage.
    junction_ids = index.pipe_nodes
    edges = _edges_in_scope(index, id_set | junction_ids)
    edges = _contract_storage(edges, storage_ids | junction_ids)
    item_edges_by_cat: dict[Category | None, list[_Edge]] = {}
    for e in edges:
        # All segments of a spliced chain share a category (a junction only joins
        # pipes of its own), so the first pipe's category keys the whole edge.
        cat = index.pipe_category.get(e.pipe_ids[0])
        item_edges_by_cat.setdefault(cat, []).append(e)

    # --- Polymorphic fuel for source-less FUEL converters -------------------- #
    # A FUEL converter that names no ``source_item_id`` (an MI boiler — "the
    # player picks which fuel to burn") burns whatever fuel is piped into it. Its
    # ``fuel_drain`` (EU/s at full rate) is met by a wired-in **fuel item** (one
    # carrying stored ``eu``), consumed at ``fuel_drain / item.eu`` units per
    # second and scaling with duty like any other input. We pick the fuel actually
    # feeding the converter (the eu-bearing output of a producer with an edge into
    # it; the best-supplied one when several are wired — one boiler burns one
    # fuel). A converter with a fuel draw but no fuel wired in is recorded in
    # ``no_fuel`` and forced to stall (it cannot run on nothing).
    producers_into: dict[str, set[str]] = {}
    for e in edges:
        producers_into.setdefault(e.sink, set()).add(e.source)
    no_fuel: set[str] = set()
    for nid in ids:
        node = index.nodes[nid]
        mdef = index.defs.get(node.machine_def_id)
        if mdef is None or mdef.energy_source is not EnergySource.FUEL:
            continue
        if mdef.source_item_id is not None:
            continue  # a fixed carrier is already routed by compute_node
        comp = computations[nid]
        conv = comp.fuel_conversion
        if conv is None or conv.fuel_drain <= _EPS:
            continue
        candidates: dict[str, float] = {}
        for p in producers_into.get(nid, ()):
            pc = computations.get(p)
            if pc is None:
                continue
            for iid, rate in pc.outputs.items():
                it = index.items.get(iid)
                if it is not None and it.eu:
                    candidates[iid] = candidates.get(iid, 0.0) + rate
        if not candidates:
            no_fuel.add(nid)
            continue
        fuel_id = max(candidates, key=lambda k: (candidates[k], k))
        fuel_eu = index.items[fuel_id].eu or 1.0
        comp.inputs[fuel_id] = comp.inputs.get(fuel_id, 0.0) + conv.fuel_drain / fuel_eu

    # A **generation source** is the supply edge of the system: an energy
    # converter (a boiler/turbine/generator making a carrier from its source) or
    # an input-less producer (a water pump, quarry, or drill yielding a resource
    # from power alone, with no material feedstock). Identified here; whether a
    # given source's output is treated as a product is decided below, once the
    # flow graph is known — only sources on a feedback *cycle* are exempted from
    # demand-backpressure (a lone generator in a linear chain still throttles to
    # what its consumer draws, so it burns no more fuel than needed).
    def _is_source(nid: str) -> bool:
        node = index.nodes[nid]
        mdef = index.defs.get(node.machine_def_id)
        if mdef is None:
            return False
        if (
            mdef.energy_source in (EnergySource.FUEL, EnergySource.LIQUID)
            and mdef.max_fluid_rate is not None
            and mdef.output_item_id is not None
        ):
            return True
        recipe = index.recipes.get(node.recipe_id) if node.recipe_id else None
        if recipe is not None and recipe.outputs:
            has_material_input = any(
                s.resource_id is not None
                and (s.probability is None or s.probability != 0.0)
                and _category_of(index, s.resource_id) is not Category.ENERGY
                for s in recipe.inputs
            )
            if not has_material_input:
                return True
        return False

    source_nodes = {nid for nid in ids if _is_source(nid)}
    assume_in = {nid: index.nodes[nid].assume_inputs for nid in ids}
    assume_out = {nid: index.nodes[nid].assume_outputs for nid in ids}

    # A *demand key* is a recipe-input resource id — a concrete item **or** a
    # tag. A *supply key* is a concrete item a node actually produces. A tag
    # demand is satisfied by any concrete supply the tag includes
    # (membership-aware), so a producer of ``iron_ingot`` feeds a consumer whose
    # recipe input is the tag ``c:ingots/iron``. ``_satisfies`` is the single
    # place that relation lives.
    def _satisfies(supply_key: str, demand_key: str) -> bool:
        if supply_key == demand_key:
            return True
        d = index.items.get(demand_key)
        if d is not None and d.is_tag and d.members and supply_key in d.members:
            return True
        # A tag *supply* satisfying a concrete demand is unusual but symmetric.
        s = index.items.get(supply_key)
        if s is not None and s.is_tag and s.members and demand_key in s.members:
            return True
        return False

    demand_keys = sorted({k for nid in ids for k in computations[nid].inputs})
    supply_present = {k for nid in ids for k in computations[nid].outputs}
    # Per demand key: the produced supply keys that can satisfy it.
    match_sources: dict[str, list[str]] = {
        d: [s for s in supply_present if _satisfies(s, d)] for d in demand_keys
    }
    # The inverse: per produced supply key, every demand key it can satisfy. Flow
    # is resolved **per supply key** (below), so all consumers of one physical
    # item — whether they name it directly or via a tag that includes it —
    # contend for it together and split by the pipe rules, instead of an earlier
    # key claiming it all. Only supply keys that satisfy some demand are kept.
    match_keys: dict[str, list[str]] = {}
    for d, srcs in match_sources.items():
        for s in srcs:
            match_keys.setdefault(s, []).append(d)
    supply_keys = sorted(match_keys)

    # Items a *non-storage* node consumes — membership-aware: a produced supply
    # key counts as consumed if it satisfies any non-storage demand key (so a
    # concrete output absorbed by a tag demand backpressures and is not mistaken
    # for yield). An output nothing here consumes is a product that leaves the
    # scope as yield (storage is a transparent buffer, so an item only a buffer
    # holds is still a product).
    consumed_items: set[str] = set()
    for nid in ids:
        if nid in storage_ids:
            continue
        for item_id, rate in computations[nid].inputs.items():
            if rate > _EPS:
                consumed_items.update(match_sources.get(item_id, ()))

    # A generation source on a feedback cycle — its output can return to its own
    # input through the pipe graph (a boiler whose steam powers a pump that pumps
    # the boiler's water) — would collapse to zero duty under mutual
    # demand-backpressure. Exempt it: its output is treated as a product so it
    # runs at the rate its inputs permit, surplus leaving as yield. A source in a
    # plain linear chain is not on a cycle and still throttles to downstream
    # demand (so it over-produces/over-burns nothing).
    if source_nodes:
        adjacency: dict[str, set[str]] = {}
        for e in edges:
            adjacency.setdefault(e.source, set()).add(e.sink)

        def _reaches_self(start: str) -> bool:
            seen: set[str] = set()
            stack = list(adjacency.get(start, ()))
            while stack:
                n = stack.pop()
                if n == start:
                    return True
                if n in seen:
                    continue
                seen.add(n)
                stack.extend(adjacency.get(n, ()))
            return False

        for nid in source_nodes:
            if _reaches_self(nid):
                assume_out[nid] = True

    def _route(
        supply_duty: dict[str, float],
        demand_duty: dict[str, float],
    ) -> tuple[
        dict[str, dict[str, float]],
        dict[str, dict[str, float]],
        dict[str, dict[str, object]],
    ]:
        """Resolve flows; return supplied, drained, pipes.

        Producers supply at ``supply_duty`` (their output scaled by that duty);
        consumers pull at ``demand_duty``. The two vectors are usually the same
        (the actual steady state), but the relaxation probes with them split: the
        **demand ceiling** pass drives producers greedily (``supply_duty`` all 1)
        while consumers pull at the evolving ceiling, and the **input** pass holds
        consumer demand at that ceiling while producers supply at their current
        duty — so a starved consumer still *pulls* at full and never spuriously
        back-throttles its supplier.

        Resolution is **per produced supply key** (a concrete item — or a tag a
        recipe outputs directly). Every consumer that can take that item, whether
        it names the item or a tag that includes it, contends for it together in
        one :func:`_resolve_item` call, so a shared output splits across them by
        the pipe insert/extract rules (equal priority → even) instead of the
        first-listed demand key claiming it all. A sink's remaining demand for
        each key is tracked across supply keys (``demand_rem``) so a key satisfied
        by several items isn't over-filled. ``supplied`` is keyed by the sink's
        demand key, ``drained`` by the source's supply key.
        """

        supplied: dict[str, dict[str, float]] = {nid: {} for nid in ids}
        drained: dict[str, dict[str, float]] = {nid: {} for nid in ids}
        # Remaining unmet demand per sink per demand key (mutated as supply keys
        # are resolved in turn), so a tag met partly by one item draws the rest
        # from another without exceeding the recipe's demand.
        demand_rem: dict[str, dict[str, float]] = {
            nid: {
                d: computations[nid].inputs[d] * demand_duty[nid]
                for d in computations[nid].inputs
            }
            for nid in ids
        }
        pipes: dict[str, dict[str, object]] = {}
        for supply_key in supply_keys:
            category = _category_of(index, supply_key)
            # Each producer's output of this one item (no cross-key commit needed —
            # a supply key is resolved exactly once).
            per_source: dict[str, float] = {}
            for nid in ids:
                made = computations[nid].outputs.get(supply_key, 0.0) * supply_duty[nid]
                if made > _EPS:
                    per_source[nid] = made
            if not per_source:
                continue
            keys_here = match_keys.get(supply_key, ())
            # Each sink's total demand this item can serve (summed over the demand
            # keys it satisfies, at their current remaining).
            per_sink = {
                nid: tot
                for nid in ids
                if (tot := sum(demand_rem[nid].get(d, 0.0) for d in keys_here)) > _EPS
            }
            if not per_sink:
                continue
            for edge, flow in _resolve_item(
                per_source, per_sink, item_edges_by_cat.get(category, [])
            ):
                if flow <= _EPS:
                    continue
                drained[edge.source][supply_key] = (
                    drained[edge.source].get(supply_key, 0.0) + flow
                )
                # Attribute the edge flow to the sink's satisfied demand keys
                # (proportional to each key's remaining demand), reducing it.
                sink = edge.sink
                rem = [
                    (d, r)
                    for d in keys_here
                    if (r := demand_rem[sink].get(d, 0.0)) > _EPS
                ]
                pool = sum(r for _, r in rem)
                carried: set[str] = set()
                for d, r in rem:
                    share = min(flow * (r / pool) if pool > _EPS else 0.0, r)
                    if share <= 0.0:
                        continue
                    demand_rem[sink][d] -= share
                    supplied[sink][d] = supplied[sink].get(d, 0.0) + share
                    carried.add(d)
                # Report the flow on every segment the edge traverses (a direct
                # pipe, or each pipe in a spliced storage/junction chain).
                for pid in edge.pipe_ids:
                    acc = pipes.setdefault(
                        pid, {"flow": 0.0, "sink": sink, "items": set()}
                    )
                    acc["flow"] = float(acc["flow"]) + flow  # type: ignore[arg-type]
                    items_on_pipe: set[str] = acc["items"]  # type: ignore[assignment]
                    items_on_pipe.update(carried)
        return supplied, drained, pipes

    ones = {nid: 1.0 for nid in ids}

    def _relax_demand() -> dict[str, float]:
        """Pass A — the **demand ceiling**: the duty each node would run at with
        unlimited inputs, limited only by its output being taken (product chains
        backpressure; a product or an ``assume_outputs`` node has no ceiling below
        1). Producers run greedily so a producer is throttled only by *true*
        downstream demand. ``no_fuel`` converters are pinned to 0. Monotone
        decreasing from 1, so it converges.
        """

        dd = {nid: (0.0 if nid in no_fuel else 1.0) for nid in ids}
        for _ in range(_MAX_DUTY_ITERS):
            _, drained, _ = _route(ones, dd)
            converged = True
            for nid in ids:
                if nid in no_fuel:
                    continue
                comp = computations[nid]
                lim = 1.0
                if not assume_out[nid]:
                    for item_id, nominal in comp.outputs.items():
                        if item_id not in consumed_items or nominal <= _EPS:
                            continue  # product: leaves as yield, no backpressure
                        lim = min(lim, drained[nid].get(item_id, 0.0) / nominal)
                nd = max(0.0, min(1.0, lim))
                if abs(nd - dd[nid]) > 1e-9:
                    converged = False
                dd[nid] = nd
            if converged:
                break
        return dd

    def _relax_inputs(
        dd: dict[str, float],
    ) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
        """Pass B — the **input-limited** duty: start at the demand ceiling and
        iterate ``duty_i = min(dd_i, availability_i)``, where availability is the
        flow reaching ``i`` when it pulls at the ceiling and producers supply at
        their *current* duty. Because demand stays pinned at ``dd`` (not the
        shrinking duty), a starved consumer keeps pulling at full and never drags
        its supplier to zero — the old multiplicative relaxation's collapse. The
        final-iteration ``supplied`` (demand pinned at ``dd``) is returned as each
        input's *availability* for shortfall attribution.
        """

        duty = dict(dd)
        avail: dict[str, dict[str, float]] = {nid: {} for nid in ids}
        for _ in range(_MAX_DUTY_ITERS):
            avail, _, _ = _route(duty, dd)
            converged = True
            for nid in ids:
                if nid in no_fuel:
                    if duty[nid] != 0.0:
                        converged = False
                    duty[nid] = 0.0
                    continue
                comp = computations[nid]
                lim = dd[nid]
                if not assume_in[nid]:
                    for item_id, nominal in comp.inputs.items():
                        if nominal > _EPS:
                            got = avail[nid].get(item_id, 0.0)
                            lim = min(lim, got / nominal)
                nd = max(0.0, min(dd[nid], lim))
                if abs(nd - duty[nid]) > 1e-9:
                    converged = False
                duty[nid] = nd
            if converged:
                break
        return duty, avail

    duty_demand = _relax_demand()
    duty, avail_supplied = _relax_inputs(duty_demand)
    # Actual steady-state flows at the solved duty (supply and demand both at the
    # real running rate) — the numbers reported for pipes, yield, and energy.
    supplied_internal, demanded_internal, pipe_acc = _route(duty, duty)

    # --- Feedstock (manual insertion) + yield (products) ------------------- #
    external_inputs: dict[str, float] = {}
    external_outputs: dict[str, float] = {}
    for nid in ids:
        d = duty[nid]
        comp = computations[nid]
        if assume_in[nid]:
            for item_id, nominal in comp.inputs.items():
                if _category_of(index, item_id) is Category.ENERGY:
                    continue
                topup = nominal * d - supplied_internal[nid].get(item_id, 0.0)
                if topup > _EPS:
                    external_inputs[item_id] = external_inputs.get(item_id, 0.0) + topup
        for item_id, nominal in comp.outputs.items():
            if _category_of(index, item_id) is Category.ENERGY:
                continue
            exported = nominal * d - demanded_internal[nid].get(item_id, 0.0)
            is_product = assume_out[nid] or item_id not in consumed_items
            if is_product and exported > _EPS:
                external_outputs[item_id] = (
                    external_outputs.get(item_id, 0.0) + exported
                )

    # --- Per-node balances + bottlenecks ---------------------------------- #
    # A node's **overall** status is a run band off its duty (what actually needs
    # attention): CAPPED (over its power-tier ceiling) or STALLED (not running) →
    # red; THROTTLED (< 50%) → yellow; OK (≥ 50%) → green. Per-item inputs keep the
    # SHORTFALL verdict — but only for an input that genuinely can't meet the
    # demand ceiling (``availability < demand``), so a *plentiful* carrier is never
    # blamed when a different input is the real limit; those SHORTFALLs are the
    # bottlenecks and the dashed-edge flags.
    balances: dict[str, NodeBalance] = {}
    bottlenecks: list[Bottleneck] = []
    for nid in ids:
        comp = computations[nid]
        d = duty[nid]
        dd = duty_demand[nid]
        in_bals: dict[str, InputBalance] = {}
        for item_id, nominal in comp.inputs.items():
            demand_amt = nominal * dd
            avail_amt = avail_supplied[nid].get(item_id, 0.0)
            if assume_in[nid]:
                avail_amt = max(avail_amt, demand_amt)
            short = max(0.0, demand_amt - avail_amt)
            shown = min(avail_amt, demand_amt)
            status = Status.SHORTFALL if short > _EPS else Status.OK
            in_bals[item_id] = InputBalance(item_id, demand_amt, shown, short, status)
            # Any genuinely short input is a bottleneck — energy carriers included
            # (a power shortage), which stay out of feedstock/yield but surface here.
            if short > _EPS:
                bottlenecks.append(Bottleneck(nid, item_id, short))
        out_bals: dict[str, OutputBalance] = {}
        for item_id, nominal in comp.outputs.items():
            made = nominal * d
            taken = demanded_internal[nid].get(item_id, 0.0)
            out_bals[item_id] = OutputBalance(item_id, made, taken, 0.0, Status.OK)
        if comp.overclock_capped:
            overall = Status.CAPPED  # over capacity: can't reach the set rate
        elif d <= _EPS:
            overall = Status.STALLED  # not running at all
        elif d < 0.5 - _EPS:
            overall = Status.THROTTLED  # running below 50%
        else:
            overall = Status.OK  # running 50–100%
        balances[nid] = NodeBalance(nid, in_bals, out_bals, overall)

    bottlenecks.sort(key=lambda b: b.shortfall, reverse=True)

    # Per-pipe flow + a dashed-edge flag: a pipe reads as starved when the node
    # it feeds is short of an item the pipe carries.
    pipe_flows: dict[str, PipeFlow] = {}
    for pipe_id, acc in pipe_acc.items():
        sink_id: str = acc["sink"]  # type: ignore[assignment]
        items_on_pipe = acc["items"]  # type: ignore[assignment]
        sink_balance = balances.get(sink_id)
        starved_edge = bool(
            sink_balance
            and any(
                (ib := sink_balance.inputs.get(item_id)) is not None
                and ib.status is Status.SHORTFALL
                for item_id in items_on_pipe
            )
        )
        pipe_flows[pipe_id] = PipeFlow(pipe_id, float(acc["flow"]), starved_edge)

    # Net energy scales with duty (a throttled machine draws/makes less).
    net_energy = _sum_net_energy((computations[nid], duty[nid]) for nid in ids)

    return ScopeReport(
        node_ids=ids,
        external_inputs=external_inputs,
        external_outputs=external_outputs,
        net_energy=net_energy,
        computations=computations,
        balances=balances,
        bottlenecks=bottlenecks,
        pipe_flows=pipe_flows,
        node_duty=duty,
    )


def _sum_net_energy(items) -> float | None:
    """Sum present duty-scaled ``netEnergy`` terms; ``None`` if no term exists."""

    present = [c.net_energy * d for c, d in items if c.net_energy is not None]
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
    # Connectivity routes through pipe junctions: a junction is a traversable
    # waypoint (so machine→junction→machine is one component) but is not itself a
    # machine, so it is dropped from the returned node list by the final filter.
    adjacency: dict[str, set[str]] = {n.id: set() for n in world.nodes}
    for jid in index.pipe_nodes:
        adjacency[jid] = set()
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

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

    # Fast path: a single carrying edge has no contention — its flow is just the
    # min of the source's supply, the sink's demand, and the pipe throughput.
    # This is the overwhelmingly common case (a point-to-point pipe), and skipping
    # the residual-round machinery is a large constant-factor win on big worlds.
    if len(live) == 1:
        e = live[0]
        f = min(supply.get(e.source, 0.0), demand.get(e.sink, 0.0), e.throughput)
        return [(e, f)] if f > _EPS else []

    by_source: dict[str, list[_Edge]] = {}
    by_sink: dict[str, list[_Edge]] = {}
    for e in live:
        by_source.setdefault(e.source, []).append(e)
        by_sink.setdefault(e.sink, []).append(e)

    # Accumulated flow per edge, resolved over residual rounds. A single pass of
    # the insert/extract rules can strand usable capacity when several sources
    # feed many sinks: each source even-splits its supply across all its sinks,
    # each sink even-splits its demand across all its sources, and the per-edge
    # ``min`` of the two leaves a feasible-but-undelivered remainder (e.g. three
    # boilers feeding one steam trunk delivered a machine only half of what the
    # trunk could supply). Re-running on the residual supply/demand recovers it,
    # converging to the max flow the priorities allow, so a sink reads as starved
    # only when the network genuinely cannot feed it — never as a split artefact.
    flow: dict[int, float] = {id(e): 0.0 for e in live}
    for _ in range(_MAX_FLOW_ROUNDS):
        r_supply = {
            s: supply.get(s, 0.0) - sum(flow[id(e)] for e in grp)
            for s, grp in by_source.items()
        }
        r_demand = {
            k: demand.get(k, 0.0) - sum(flow[id(e)] for e in grp)
            for k, grp in by_sink.items()
        }

        # Insert side: per source, split its residual supply across outgoing edges
        # by descending insert priority; each edge capped by residual throughput
        # and the sink's residual demand.
        insert_alloc: dict[int, float] = {}
        for source, group in by_source.items():
            claims = [
                Claim(
                    id=str(id(e)),
                    capacity=min(e.throughput - flow[id(e)], r_demand.get(e.sink, 0.0)),
                    priority=e.insert_priority,
                )
                for e in group
            ]
            got = resolve_insert(max(0.0, r_supply.get(source, 0.0)), claims)
            for e in group:
                insert_alloc[id(e)] = got[str(id(e))]

        # Extract side: per sink, split its residual demand across incoming edges
        # by ascending extract priority; each edge capped by residual throughput
        # and the source's residual supply.
        extract_alloc: dict[int, float] = {}
        for sink, group in by_sink.items():
            claims = [
                Claim(
                    id=str(id(e)),
                    capacity=min(
                        e.throughput - flow[id(e)], r_supply.get(e.source, 0.0)
                    ),
                    priority=e.extract_priority,
                )
                for e in group
            ]
            got = resolve_extract(max(0.0, r_demand.get(sink, 0.0)), claims)
            for e in group:
                extract_alloc[id(e)] = got[str(id(e))]

        # Apply the binding of both allocations. Per source the insert shares sum
        # to ≤ its residual supply and per sink the extract shares sum to ≤ its
        # residual demand, so the per-edge min is feasible for every source and
        # sink at once — the round can be applied simultaneously (order-free).
        progressed = 0.0
        for e in live:
            add = min(insert_alloc[id(e)], extract_alloc[id(e)])
            if add > _EPS:
                flow[id(e)] += add
                progressed += add
        if progressed <= _EPS:
            break

    return [(e, flow[id(e)]) for e in live if flow[id(e)] > _EPS]


# --------------------------------------------------------------------------- #
# Scope aggregation.
# --------------------------------------------------------------------------- #


_MAX_DUTY_ITERS = 64
#: Residual rounds inside :func:`_resolve_item`. A simple chain settles in one;
#: a multi-source/multi-sink carrier (a shared steam/power trunk) needs a few to
#: mop up stranded capacity. Convergence is monotone, so the cap only bounds the
#: worst case; most calls break out early when a round delivers nothing.
_MAX_FLOW_ROUNDS = 24


def compute_scope(world: World, node_ids: list[str] | None = None) -> ScopeReport:
    """Aggregate steady-state stats over ``node_ids`` (default: the whole world).

    The model is **purely input-driven**: every machine runs at the maximum its
    delivered inputs allow, and output is never a throttle. Unconsumed output is
    **surplus** — assumed routed to storage or voided — reported as overflow (a
    good state), not backpressure. So a source whose inputs are available (a
    quarry on an ample steam line, a boiler fed fuel and water) runs at 100%
    independently of how much its consumers take or how many siblings share its
    supply: adding or removing one has no bearing on the rest.

    Each node's **duty cycle** is solved by :func:`_relax_inputs`: start every
    runnable node at ceiling 1 and iterate ``duty_i = min(1, availability_i)``,
    where availability is the fraction of its inputs actually delivered (demand
    stays pinned at the ceiling so a starved consumer never drags its supplier
    down, and a feedback cycle such as boiler↔pump holds at 1 because each side
    supplies the other fully). ``assume_inputs`` (manual insertion) treats any
    unsupplied input as available, so an edge machine runs on hand-fed feedstock.
    (``assume_outputs`` has no effect here — output never throttles a machine, so
    the flag is retained only for backward compatibility with older saves.)

    The inputs that can't be fully met are a node's shortfalls (and the
    bottlenecks), so a *plentiful* carrier is never blamed when a different input
    is the real limit. A source-less FUEL converter (an MI boiler) burns whatever
    eu-bearing fuel is wired to it, metered by that fuel's ``eu``; with nothing
    wired it cannot run. Storage (buffer) nodes and pipe junctions are transparent
    passthroughs. ``external_inputs`` is the outside feedstock (manually-inserted
    inputs), ``external_outputs`` the actual yield (products + surplus leaving the
    scope).
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
        # A node may run several recipes at once; its I/O and energy are the sum.
        recipes = [
            index.recipes[rid] for rid in node.recipe_list if rid in index.recipes
        ]
        machine_def = index.defs.get(node.machine_def_id)
        if machine_def is None:
            computations[nid] = NodeComputation(node_id=nid, cycles_per_sec=0.0)
            continue
        if machine_def.storage is not None:
            storage_ids.add(nid)
        computations[nid] = compute_node(node, recipes, machine_def, items=index.items)

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

    assume_in = {nid: index.nodes[nid].assume_inputs for nid in ids}

    # A *demand key* is a recipe-input resource id — a concrete item **or** a
    # tag. A *supply key* is a concrete item a node actually produces. A tag
    # demand is satisfied by any concrete supply the tag includes
    # (membership-aware), so a producer of ``iron_ingot`` feeds a consumer whose
    # recipe input is the tag ``c:ingots/iron``. ``_satisfies`` is the single
    # place that relation lives.
    def _seg(key: str) -> str:
        # Final path/namespace segment of an item id, e.g. ``c:creosote`` and
        # ``modern_industrialization:creosote`` both reduce to ``creosote``.
        return key.rsplit("/", 1)[-1].split(":")[-1]

    def _memberless_match(tag_key: str, concrete_key: str) -> bool:
        # A tag that carries *no* members can never be satisfied by the normal
        # membership rule, so it would silently dead-end every recipe that needs
        # it (an import gap — e.g. ``c:creosote`` left memberless while the jar
        # ships ``modern_industrialization:creosote``). As a last resort, match a
        # memberless tag to a concrete item sharing its final segment (the MI
        # naming convention). Guarded by "no members" so a populated tag is never
        # widened by name.
        t = index.items.get(tag_key)
        if t is None or not t.is_tag or t.members:
            return False
        c = index.items.get(concrete_key)
        if c is None or c.is_tag:
            return False
        return _seg(tag_key) == _seg(concrete_key)

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
        # Last resort: a memberless tag ↔ a concrete item of the same name.
        if _memberless_match(demand_key, supply_key):
            return True
        if _memberless_match(supply_key, demand_key):
            return True
        return False

    demand_keys = sorted({k for nid in ids for k in computations[nid].inputs})
    supply_present = {k for nid in ids for k in computations[nid].outputs}
    # Per demand key: the produced supply keys that can satisfy it.
    match_sources: dict[str, list[str]] = {
        d: [s for s in supply_present if _satisfies(s, d)] for d in demand_keys
    }
    # Supply keys produced *anywhere in the world* (not only inside this scope).
    # This distinguishes a boundary input (produced elsewhere, just outside the
    # current node/system slice — so it is feedstock crossing the scope boundary)
    # from a genuinely unsourced input (produced by nothing at all — so it stays a
    # shortfall and the node stalls, unless it is flagged for manual insertion).
    world_supply: set[str] = set()
    for n in world.nodes:
        for rid in n.recipe_list:
            r = index.recipes.get(rid)
            if r is None:
                continue
            for st in r.outputs:
                if st.resource_id is not None and (
                    st.probability is None or st.probability != 0.0
                ):
                    world_supply.add(st.resource_id)
        md = index.defs.get(n.machine_def_id)
        if md is not None and md.output_item_id is not None:
            world_supply.add(md.output_item_id)
    world_sourced: dict[str, bool] = {
        d: any(_satisfies(s, d) for s in world_supply) for d in demand_keys
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

    # --- Scope boundary: which inputs are feedstock (externally supplied) ----- #
    # Each scope is an independent slice. An input is feedstock — assumed hand-fed,
    # so it never throttles and is reported under external_inputs — when it has no
    # producer inside the scope **but is produced elsewhere in the world**: the
    # scope boundary has merely cut its supply chain, which is what makes a
    # node/system view a real standalone slice (e.g. the ore a single macerator
    # eats is feedstock in its own node view, though a quarry makes it elsewhere).
    # An ``assume_inputs`` node treats all its inputs as hand-fed. An input that
    # nothing in the whole world produces is *not* feedstock — it stays an
    # unresolved internal demand and the node stalls (placing a lone converter
    # with nothing wired is a shortfall, not free yield), so the world scope is
    # unchanged: there, no-producer-in-scope means no producer anywhere.
    def _is_external(nid: str, key: str) -> bool:
        if assume_in[nid]:
            return True
        if match_sources.get(key):
            return False  # produced inside the scope → resolved by flow
        return world_sourced.get(key, False)  # produced outside → boundary feedstock

    ext_avail: dict[str, set[str]] = {
        nid: {k for k in computations[nid].inputs if _is_external(nid, k)}
        for nid in ids
    }

    def _route(
        supply_duty: dict[str, float],
        demand_amt: dict[str, dict[str, float]],
        keys: list[str] | None = None,
    ) -> tuple[
        dict[str, dict[str, float]],
        dict[str, dict[str, float]],
        dict[str, dict[str, object]],
    ]:
        """Resolve flows; return supplied, drained, pipes.

        Producers supply at ``supply_duty`` (their output scaled by that duty);
        each consumer pulls the per-input amounts in ``demand_amt[nid][key]`` (an
        absolute rate, not a scalar duty). Keeping demand **per input** is what
        decouples otherwise-independent supply chains: a machine throttled by one
        scarce input (say ore) asks only for the steam its reduced rate needs, so
        it no longer hogs a plentiful shared carrier and starve its siblings.

        Resolution is **per produced supply key** (a concrete item — or a tag a
        recipe outputs directly). Every consumer that can take that item, whether
        it names the item or a tag that includes it, contends for it together in
        one :func:`_resolve_item` call, so a shared output splits across them by
        the pipe insert/extract rules (equal priority → even) instead of the
        first-listed demand key claiming it all. A sink's remaining demand for
        each key is tracked across supply keys (``demand_rem``) so a key satisfied
        by several items isn't over-filled. ``supplied`` is keyed by the sink's
        demand key, ``drained`` by the source's supply key.

        ``keys`` optionally restricts resolution to a subset of ``supply_keys``
        (used by the per-node probe, which only needs the keys that can satisfy
        the probed node's own inputs — resolving the rest would compute shares it
        discards). A key outside the subset contributes nothing to the probed
        node's availability, so the restricted result is exact for that node.
        """

        supplied: dict[str, dict[str, float]] = {nid: {} for nid in ids}
        drained: dict[str, dict[str, float]] = {nid: {} for nid in ids}
        # Remaining unmet demand per sink per demand key (mutated as supply keys
        # are resolved in turn), so a tag met partly by one item draws the rest
        # from another without exceeding the recipe's demand.
        demand_rem: dict[str, dict[str, float]] = {
            nid: dict(demand_amt.get(nid, {})) for nid in ids
        }
        pipes: dict[str, dict[str, object]] = {}
        for supply_key in supply_keys if keys is None else keys:
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

    internal_inputs: dict[str, list[str]] = {
        nid: [k for k in computations[nid].inputs if k not in ext_avail[nid]]
        for nid in ids
    }

    # Per node: the supply keys that can satisfy any of its inputs. The probe only
    # needs these — a supply key the node doesn't consume contributes nothing to
    # its availability — so restricting the probe route to them turns the whole
    # relaxation from "re-route every supply key for every node" into
    # "re-route only the node's own feeds", the difference between O(N²) and O(N)
    # on a long chain. (Internal inputs only: feedstock keys have no in-scope
    # producer and route to nothing anyway, and their availability is never read.)
    relevant_supply_keys: dict[str, list[str]] = {}
    for nid in ids:
        rel: set[str] = set()
        for k in internal_inputs[nid]:
            rel.update(match_sources.get(k, ()))
        relevant_supply_keys[nid] = sorted(rel)

    def _relax_inputs(
        dd: dict[str, float],
    ) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
        """Solve each node's input-limited duty, with supply chains decoupled.

        The model is purely input-driven: a runnable node's ceiling is ``dd`` (1),
        since unconsumed output is surplus, never a throttle. A node runs at the
        minimum, over its **internally-produced** inputs, of the fraction of each
        it can actually obtain — feedstock inputs (crossing the scope boundary)
        are always available and never limit it.

        The hard part is measuring that obtainable fraction without coupling
        unrelated chains. Two naive demand vectors both fail: if every node
        demands every input at the full ceiling, an ore-starved macerator still
        asks for 100% of a shared steam trunk and the trunk looks scarce to
        everyone (the false "steam bottleneck"); if every node instead demands at
        its own shrinking duty, a node that dropped for one reason releases a
        different, ample input and can never climb back (a ratchet collapse), and
        a node whose inputs are *all* internal with one scarce spirals to zero.

        The correct measure is a **per-node probe**. To find how much of each input
        node ``i`` can obtain, route once with ``i`` bidding its full rate for
        every input while **every other node bids at its current duty**. Because
        only ``i`` bids full, a plentiful shared carrier is never over-subscribed
        by nodes throttled elsewhere, so it is never mistaken for scarce; because
        ``i`` bids full (not its own shrinking duty), it always re-probes its true
        max-min share and climbs when a sibling frees capacity — and two equally
        scarce inputs are each measured at full, so neither is masked. ``i``'s duty
        is the min obtainable fraction over its internal inputs. A feedback cycle
        (a boiler whose steam powers the pump that feeds it water) still holds at 1
        because each side supplies the other fully. Iterated to a fixed point.
        """

        duty = dict(dd)
        base_demand = {
            nid: {k: v * duty[nid] for k, v in computations[nid].inputs.items()}
            for nid in ids
        }
        # Each node's obtainable amount per input, from its most recent probe.
        probe_avail: dict[str, dict[str, float]] = {nid: {} for nid in ids}
        for _ in range(_MAX_DUTY_ITERS):
            converged = True
            new_duty: dict[str, float] = {}
            for nid in ids:
                if nid in no_fuel or not internal_inputs[nid]:
                    # no_fuel: cannot run (no fuel wired). no internal inputs: only
                    # feedstock, so it runs at its ceiling. Neither needs a probe.
                    new_duty[nid] = 0.0 if nid in no_fuel else dd[nid]
                    if abs(new_duty[nid] - duty[nid]) > 1e-9:
                        converged = False
                    continue
                comp = computations[nid]
                # Probe: nid bids full for its own inputs; everyone else bids at
                # their current duty (base_demand). One route gives nid's share of
                # each of its inputs under real contention — this is what both sets
                # its duty and, at convergence, attributes its shortfalls (an input
                # it cannot obtain in full is a limiter, whether globally scarce or
                # simply not routed to it; a plentiful, reachable carrier is not).
                demand_amt = dict(base_demand)
                demand_amt[nid] = {k: v * dd[nid] for k, v in comp.inputs.items()}
                avail, _, _ = _route(duty, demand_amt, keys=relevant_supply_keys[nid])
                probe_avail[nid] = avail[nid]
                lim = dd[nid]
                for k in internal_inputs[nid]:
                    nominal = comp.inputs[k]
                    if nominal > _EPS:
                        lim = min(lim, avail[nid].get(k, 0.0) / nominal)
                nd = max(0.0, min(dd[nid], lim))
                new_duty[nid] = nd
                if abs(nd - duty[nid]) > 1e-9:
                    converged = False
            duty = new_duty
            base_demand = {
                nid: {k: v * duty[nid] for k, v in computations[nid].inputs.items()}
                for nid in ids
            }
            if converged:
                break
        return duty, probe_avail

    # Input-driven model: a runnable node's ceiling is 1 (unconsumed output is
    # surplus, routed to storage / voided — it never throttles the machine). Only
    # a source-less fuel converter with no fuel wired in cannot run.
    duty_demand = {nid: (0.0 if nid in no_fuel else 1.0) for nid in ids}
    duty, probe_avail = _relax_inputs(duty_demand)
    # Actual steady-state flows at the solved duty (each node pulls exactly the
    # nominal × duty it runs at) — the numbers reported for pipes, yield and energy.
    actual_demand = {
        nid: {k: v * duty[nid] for k, v in computations[nid].inputs.items()}
        for nid in ids
    }
    supplied_internal, demanded_internal, pipe_acc = _route(duty, actual_demand)

    # --- Feedstock (scope-boundary inputs) + yield (products leaving) --------- #
    # Each scope is a standalone slice. ``external_inputs`` is the feedstock it
    # consumes across its boundary (inputs with no in-scope producer, plus any
    # assume-inputs top-up); ``external_outputs`` is the yield — everything a node
    # produces that is not consumed *inside this scope*. Output consumed by a node
    # outside the scope (a downstream machine not in the node/system view) has
    # left the scope and so counts as yield here, exactly as a pure product does.
    external_inputs: dict[str, float] = {}
    external_outputs: dict[str, float] = {}
    for nid in ids:
        d = duty[nid]
        comp = computations[nid]
        for item_id, nominal in comp.inputs.items():
            if item_id not in ext_avail[nid]:
                continue
            if _category_of(index, item_id) is Category.ENERGY:
                continue
            fed = nominal * d - supplied_internal[nid].get(item_id, 0.0)
            if fed > _EPS:
                external_inputs[item_id] = external_inputs.get(item_id, 0.0) + fed
        for item_id, nominal in comp.outputs.items():
            if _category_of(index, item_id) is Category.ENERGY:
                continue
            exported = nominal * d - demanded_internal[nid].get(item_id, 0.0)
            if exported > _EPS:
                external_outputs[item_id] = (
                    external_outputs.get(item_id, 0.0) + exported
                )

    # --- Per-node balances + bottlenecks ---------------------------------- #
    # Overall node status is a plain run band off its duty — the single thing that
    # needs attention: STALLED (not running) → red; THROTTLED (running below full,
    # an input limits it) → yellow; OK (running at full) → green. Surplus output is
    # normal and expected in an input-driven model (the goal is to max every
    # machine), so it is reported as yield, not a distinct node colour. Per-item an
    # input is SHORTFALL only when it is a genuine limiter — the node cannot obtain
    # it in full (its probe share falls short) *and* it is one of the inputs that
    # actually pins the duty. So a plentiful, reachable carrier (ample steam) is
    # never blamed, while an input that is globally plentiful but not routed to
    # this node (e.g. water wired only to the boilers) is correctly flagged. These
    # SHORTFALLs are the bottlenecks and the dashed-edge flags.
    balances: dict[str, NodeBalance] = {}
    bottlenecks: list[Bottleneck] = []
    for nid in ids:
        comp = computations[nid]
        d = duty[nid]
        throttled = d < 1.0 - _EPS
        avail_n = probe_avail.get(nid, {})
        in_bals: dict[str, InputBalance] = {}
        for item_id, nominal in comp.inputs.items():
            demand_amt = nominal  # full-rate draw (what it needs to run at 100%)
            is_external = item_id in ext_avail[nid]
            if is_external or nominal <= _EPS:
                obtainable = nominal  # feedstock is always available in full
            else:
                obtainable = min(nominal, avail_n.get(item_id, 0.0))
            # A limiter can't be obtained in full and sits at (or below) the duty
            # the node settled at — i.e. it is one of the inputs pinning the rate,
            # not a merely partial but non-binding one.
            frac = obtainable / nominal if nominal > _EPS else 1.0
            is_limiter = (
                throttled and not is_external and frac <= d + 1e-9 and frac < 1.0 - _EPS
            )
            supplied_amt = obtainable if is_limiter else nominal * d
            short = max(0.0, nominal - supplied_amt) if is_limiter else 0.0
            status = Status.SHORTFALL if is_limiter else Status.OK
            in_bals[item_id] = InputBalance(
                item_id, demand_amt, supplied_amt, short, status
            )
            # The limiting inputs are the bottlenecks — energy carriers included
            # (a genuine power shortage), which stay out of feedstock/yield but
            # surface here. A plentiful, reachable carrier never reaches this branch.
            if is_limiter and short > _EPS:
                bottlenecks.append(Bottleneck(nid, item_id, short))
        out_bals: dict[str, OutputBalance] = {}
        for item_id, nominal in comp.outputs.items():
            made = nominal * d
            taken = demanded_internal[nid].get(item_id, 0.0)
            over = max(0.0, made - taken)
            # A material/fluid surplus is routed to storage/voided (informational);
            # electricity is the grid (reported as netEnergy).
            is_energy = _category_of(index, item_id) is Category.ENERGY
            ostatus = Status.OVERFLOW if (over > _EPS and not is_energy) else Status.OK
            out_bals[item_id] = OutputBalance(item_id, made, taken, over, ostatus)
        if d <= _EPS:
            overall = Status.STALLED  # not running at all
        elif throttled:
            overall = Status.THROTTLED  # running below full (an input limits it)
        else:
            overall = Status.OK  # running at full rate
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

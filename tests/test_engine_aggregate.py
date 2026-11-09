"""Phase 2d — aggregation over scopes.

Acceptance bar (WORKFLOW P2d): a multi-node chain reports correct external I/O,
energy balance, and bottleneck ranking; scope results compose (world = sum of
its processes). Uses the real P2 fixtures plus a small programmatic world to
prove the aggregate wiring drives the isolated flow primitives.
"""

import math

from process_sim.engine import (
    Status,
    compute_scope,
    compute_world,
    trace_process,
)
from process_sim.engine.aggregate import compute_system
from process_sim.model import (
    Category,
    Direction,
    EnergySource,
    Item,
    MachineDef,
    MachineNode,
    Pipe,
    PipeEnd,
    PipeEndRole,
    Recipe,
    SlotGroup,
    Stack,
    StorageSpec,
    System,
    World,
)


def _close(a: float, b: float) -> bool:
    return math.isclose(a, b, abs_tol=1e-9)


# --------------------------------------------------------------------------- #
# Furnace → chest: a producer whose output nothing consumes.
# --------------------------------------------------------------------------- #


def test_furnace_chest_external_io(furnace_chest: World) -> None:
    report = compute_world(furnace_chest)
    # Ore has no internal producer → feedstock input at the furnace's demand rate.
    assert _close(report.external_inputs["ore"], 0.5)
    # The chest doesn't consume, so the furnace's ingots are the scope's yield.
    assert _close(report.external_outputs["ingot"], 0.5)
    # Energy is a machine property now: the furnace draws its base 20 EU/s.
    assert _close(report.net_energy, -20.0)
    assert _close(report.node_duty["furnace1"], 1.0)  # fully supplied


def test_furnace_chest_product_is_surplus(furnace_chest: World) -> None:
    report = compute_world(furnace_chest)
    furnace = report.balances["furnace1"]
    # Ingots go to a chest (a transparent buffer): the furnace's output leaves the
    # scope as yield (nothing consumes it internally), i.e. surplus → OVERFLOW
    # (blue, the good "running with surplus" state). It runs fully supplied (ore
    # manually inserted) with no input shortfall.
    assert furnace.outputs["ingot"].status is Status.OVERFLOW
    assert furnace.status is Status.OVERFLOW
    assert furnace.inputs["ore"].status is Status.OK
    assert report.bottlenecks == []


def _lone_boiler(**node_kw) -> World:
    return World(
        items=[
            Item("coal", "Coal", Category.SOLID, eu=1000),
            Item("water", "Water", Category.LIQUID),
            Item("steam", "Steam", Category.LIQUID),
        ],
        machine_defs=[
            MachineDef(
                id="boiler",
                energy_source=EnergySource.FUEL,
                source_item_id="coal",
                input_item_id="water",
                output_item_id="steam",
                max_fluid_rate=100,
                material_ratio=16,
            )
        ],
        nodes=[MachineNode(id="b", machine_def_id="boiler", **node_kw)],
    )


def test_unconnected_inputs_stall_the_node() -> None:
    # A boiler with nothing wired to its fuel/water inputs can't run at all →
    # STALLED (red); its unmet inputs are the shortfalls (not an overflow of steam
    # it cannot actually make).
    report = compute_world(_lone_boiler())
    assert _close(report.node_duty["b"], 0.0)
    assert report.balances["b"].status is Status.STALLED
    assert report.balances["b"].inputs["coal"].status is Status.SHORTFALL
    assert {b.item_id for b in report.bottlenecks} == {"coal", "water"}
    assert report.external_outputs == {}  # produces nothing


def test_manual_insertion_runs_on_assumed_feedstock() -> None:
    # Flagged for manual insertion, the same boiler assumes its inputs are fed by
    # hand and runs: coal + water become feedstock and steam the yield.
    report = compute_world(_lone_boiler(assume_inputs=True))
    assert _close(report.node_duty["b"], 1.0)
    # Steam it makes leaves as yield (nothing consumes it) → surplus → OVERFLOW.
    assert report.balances["b"].status is Status.OVERFLOW
    assert _close(report.external_inputs["coal"], 100 / 1000)  # eu-metered fuel
    assert _close(report.external_inputs["water"], 100 / 16)  # material ratio 16
    assert _close(report.external_outputs["steam"], 100)


# --------------------------------------------------------------------------- #
# Powered process: a real chain with a generator.
# --------------------------------------------------------------------------- #


def test_powered_process_chain_flow(powered_process: World) -> None:
    report = compute_world(powered_process)
    # The smelter demand-gates to the slower presser: it makes exactly the
    # 0.25 ingot/s the presser draws — no pile-up — so it runs at 50% duty.
    assert _close(report.node_duty["smelter1"], 0.5)
    presser = report.balances["presser1"]
    assert _close(presser.inputs["ingot"].supplied, 0.25)
    assert presser.inputs["ingot"].status is Status.OK
    # All ingot is consumed internally (no leftover); plate is the scope's product.
    assert "ingot" not in report.external_outputs
    assert _close(report.external_outputs["plate"], 0.25)
    # Ore (at the smelter's throttled draw) and the generator's coal are feedstock.
    assert _close(report.external_inputs["ore"], 0.25)
    assert _close(report.external_inputs["coal"], 200 / 1000)  # gen burns coal by eu
    assert "ingot" not in report.external_inputs  # demand met internally
    # Energy: generator makes 200 electricity; smelter draws 30×0.5, presser 60.
    assert _close(report.net_energy, 125.0)
    assert report.bottlenecks == []


def test_powered_process_bottleneck_ranked(powered_process: World) -> None:
    # Speed the press up so it out-demands the smelter: a genuine internal shortfall.
    press = next(r for r in powered_process.recipes if r.id == "press")
    press.time_seconds = 1.0  # demand ingot 1.0/s vs supply 0.5/s
    report = compute_world(powered_process)
    assert len(report.bottlenecks) == 1
    bn = report.bottlenecks[0]
    assert bn.node_id == "presser1" and bn.item_id == "ingot"
    assert _close(bn.shortfall, 0.5)
    # An internally-produced shortfall is downtime, not a feedstock input: the
    # press runs at half duty and its plate yield halves accordingly.
    assert "ingot" not in report.external_inputs
    assert _close(report.node_duty["presser1"], 0.5)
    assert _close(report.external_outputs["plate"], 0.5)  # 1.0 nominal × 0.5 duty


def test_scopes_compose(powered_process: World) -> None:
    # The chain and the isolated generator are two disjoint processes.
    chain = trace_process(powered_process, "smelter1")
    assert set(chain) == {"smelter1", "presser1"}
    gen = trace_process(powered_process, "gen1")
    assert gen == ["gen1"]

    chain_report = compute_scope(powered_process, chain)
    gen_report = compute_scope(powered_process, gen)
    world_report = compute_world(powered_process)

    # Chain: smelter demand-gates to the presser (duty 0.5), drawing 30×0.5 EU;
    # presser draws 60 → −75 (not −90, since the smelter no longer overproduces).
    assert _close(chain_report.net_energy, -75.0)
    assert _close(gen_report.net_energy, 200.0)
    # World energy = sum of its processes' energy.
    assert _close(
        world_report.net_energy, chain_report.net_energy + gen_report.net_energy
    )


def test_scope_report_serializes(powered_process: World) -> None:
    d = compute_world(powered_process).to_dict()
    assert set(d) >= {
        "nodeIds",
        "externalInputs",
        "externalOutputs",
        "balances",
        "pipeFlows",
    }
    assert d["netEnergy"] == 125.0


# --------------------------------------------------------------------------- #
# Per-pipe flow (P8 overlay seam): resolved rate + dashed-edge shortfall flag.
# --------------------------------------------------------------------------- #


def test_pipe_flow_routed(powered_process: World) -> None:
    report = compute_world(powered_process)
    # smelter ships 0.5 ingot/s; presser pulls 0.25/s → 0.25 routed over p_ingot.
    pf = report.pipe_flows["p_ingot"]
    assert _close(pf.flow, 0.25)
    assert pf.shortfall is False  # presser's ingot demand is fully met


def test_pipe_flow_no_draw(furnace_chest: World) -> None:
    # The chest has no recipe, so it demands nothing: the pipe carries no flow
    # and is absent from the per-pipe map (nothing fabricated).
    report = compute_world(furnace_chest)
    assert "p1" not in report.pipe_flows


def test_pipe_flow_shortfall_flag(powered_process: World) -> None:
    # Speed the press up so it out-demands the smelter: the feed pipe is starved.
    press = next(r for r in powered_process.recipes if r.id == "press")
    press.time_seconds = 1.0  # demand ingot 1.0/s vs supply 0.5/s
    report = compute_world(powered_process)
    pf = report.pipe_flows["p_ingot"]
    assert _close(pf.flow, 0.5)  # all the smelter makes is routed
    assert pf.shortfall is True  # but the presser is still short → dashed edge


def test_pipe_flow_even_split_serializes() -> None:
    # The three fan-out pipes each carry 30 x/s and each feeds a starved consumer.
    report = compute_scope(_fanout_world(), None)
    for i in range(3):
        pf = report.pipe_flows[f"p{i}"]
        assert _close(pf.flow, 30.0)
        assert pf.shortfall is True
    wire = report.to_dict()["pipeFlows"]
    assert wire["p0"] == {"pipeId": "p0", "flow": 30.0, "shortfall": True}


# --------------------------------------------------------------------------- #
# Aggregate drives the flow primitives: insert contention even-splits.
# --------------------------------------------------------------------------- #


def _fanout_world() -> World:
    """One source producing 90 x/s feeding three consumers each wanting 100 x/s."""

    in_slot = SlotGroup(Category.SOLID, Direction.IN, 1, 64)
    out_slot = SlotGroup(Category.SOLID, Direction.OUT, 1, 64)

    make_x = Recipe(id="make_x", time_seconds=1.0, outputs=[Stack("x", 90)])
    use_x = Recipe(
        id="use_x", time_seconds=1.0, inputs=[Stack("x", 100)], outputs=[Stack("y", 1)]
    )
    producer = MachineDef(
        id="producer", allowed_recipe_ids=["make_x"], slots=[out_slot]
    )
    consumer = MachineDef(
        id="consumer", allowed_recipe_ids=["use_x"], slots=[in_slot, out_slot]
    )
    nodes: list[MachineNode] = [
        MachineNode(id="src", machine_def_id="producer", recipe_id="make_x")
    ]
    pipes: list[Pipe] = []
    for i in range(3):
        cid = f"c{i}"
        nodes.append(MachineNode(id=cid, machine_def_id="consumer", recipe_id="use_x"))
        pipes.append(
            Pipe(
                id=f"p{i}",
                category=Category.SOLID,
                movement_capacity=1000,
                a=PipeEnd(node_id="src", role=PipeEndRole.OUT, extract_priority=0),
                b=PipeEnd(node_id=cid, role=PipeEndRole.IN, insert_priority=0),
            )
        )
    return World(
        name="fanout",
        items=[
            Item("x", "X", Category.SOLID),
            Item("y", "Y", Category.SOLID),
        ],
        recipes=[make_x, use_x],
        machine_defs=[producer, consumer],
        nodes=nodes,
        pipes=pipes,
    )


def test_aggregate_insert_contention_even_split() -> None:
    report = compute_scope(_fanout_world(), None)
    supplied = [report.balances[f"c{i}"].inputs["x"].supplied for i in range(3)]
    assert all(_close(s, 30.0) for s in supplied)  # 90 split evenly three ways
    assert _close(report.external_outputs.get("x", 0.0), 0.0)  # all 90 routed


# --------------------------------------------------------------------------- #
# Yield / duty-cycle cascade and storage passthrough.
# --------------------------------------------------------------------------- #


def _chain_world(src_qty: float, buffer: bool = False) -> World:
    """src → (buffer?) → mid → sink, each wanting 1/s; src makes `src_qty`/s."""

    in_s = SlotGroup(Category.SOLID, Direction.IN, 1, 64)
    out_s = SlotGroup(Category.SOLID, Direction.OUT, 1, 64)
    recipes = [
        Recipe(id="mk", time_seconds=1.0, outputs=[Stack("a", src_qty)]),
        Recipe(id="mid", time_seconds=1.0, inputs=[Stack("a", 1)], outputs=[Stack("b", 1)]),
        Recipe(id="use", time_seconds=1.0, inputs=[Stack("b", 1)], outputs=[Stack("c", 1)]),
    ]
    defs = [
        MachineDef(id="dsrc", slots=[out_s]),
        MachineDef(id="dmid", slots=[in_s, out_s]),
        MachineDef(id="dsink", slots=[in_s, out_s]),
        MachineDef(id="dbuf", storage=StorageSpec(Category.SOLID, 1000), slots=[in_s, out_s]),
    ]
    nodes = [
        MachineNode(id="src", machine_def_id="dsrc", recipe_id="mk"),
        MachineNode(id="mid", machine_def_id="dmid", recipe_id="mid"),
        MachineNode(id="sink", machine_def_id="dsink", recipe_id="use"),
    ]
    pipes: list[Pipe] = []

    def pipe(pid, a, b, cat="solid"):
        return Pipe(
            id=pid,
            category=Category(cat),
            movement_capacity=1000,
            a=PipeEnd(node_id=a, role=PipeEndRole.OUT),
            b=PipeEnd(node_id=b, role=PipeEndRole.IN),
        )

    if buffer:
        nodes.append(MachineNode(id="buf", machine_def_id="dbuf"))
        pipes.append(pipe("p_sb", "src", "buf"))
        pipes.append(pipe("p_bm", "buf", "mid"))
    else:
        pipes.append(pipe("p_sm", "src", "mid"))
    pipes.append(pipe("p_ms", "mid", "sink"))

    return World(
        items=[Item(i, i.upper(), Category.SOLID) for i in ("a", "b", "c")],
        recipes=recipes,
        machine_defs=defs,
        nodes=nodes,
        pipes=pipes,
    )


def test_duty_cascade_reduces_downstream_yield() -> None:
    # src makes only 0.5 a/s; mid wants 1 → runs at 50% → sink starves to 50% too.
    report = compute_world(_chain_world(0.5))
    assert _close(report.node_duty["src"], 1.0)
    assert _close(report.node_duty["mid"], 0.5)
    assert _close(report.node_duty["sink"], 0.5)
    # Final yield (c) is halved; nothing is reported as a feedstock top-up.
    assert _close(report.external_outputs["c"], 0.5)
    assert "a" not in report.external_inputs and "b" not in report.external_inputs
    # Two bottlenecks: mid short of a, sink short of b.
    short = {(b.node_id, b.item_id) for b in report.bottlenecks}
    assert short == {("mid", "a"), ("sink", "b")}


def _power_world(wire_capacity: float) -> World:
    """A generator wired to one electric machine over an energy pipe.

    The generator makes 100 EU/s of electricity; the machine wants 80 EU/s to run
    at full rate. The energy pipe's movement capacity throttles delivery.
    """

    en_out = SlotGroup(Category.ENERGY, Direction.OUT, 1, 1)
    en_in = SlotGroup(Category.ENERGY, Direction.IN, 1, 1)
    out_s = SlotGroup(Category.SOLID, Direction.OUT, 1, 64)
    gen = MachineDef(
        id="gen",
        energy_source=EnergySource.FUEL,
        source_item_id="coal",
        output_item_id="eu",
        max_fluid_rate=100,
        slots=[en_out],
    )
    mach = MachineDef(
        id="mach",
        energy_source=EnergySource.ELECTRIC,
        source_item_id="eu",
        base_energy_consumption=80,
        slots=[en_in, out_s],
    )
    make = Recipe(id="make", time_seconds=1.0, outputs=[Stack("widget", 1)])
    return World(
        items=[
            Item("coal", "Coal", Category.SOLID, eu=1000),
            Item("eu", "Electricity", Category.ENERGY),
            Item("widget", "Widget", Category.SOLID),
        ],
        recipes=[make],
        machine_defs=[gen, mach],
        nodes=[
            MachineNode(id="gen1", machine_def_id="gen", assume_inputs=True),
            MachineNode(id="mach1", machine_def_id="mach", recipe_id="make"),
        ],
        pipes=[
            Pipe(
                id="wire",
                category=Category.ENERGY,
                movement_capacity=wire_capacity,
                a=PipeEnd(node_id="gen1", role=PipeEndRole.OUT),
                b=PipeEnd(node_id="mach1", role=PipeEndRole.IN),
            )
        ],
    )


def test_electricity_transfer_limit_throttles_consumer() -> None:
    # Ample wire: the machine gets its full 80 EU and runs at 100%.
    full = compute_world(_power_world(1000))
    assert _close(full.node_duty["mach1"], 1.0)
    assert _close(full.external_outputs["widget"], 1.0)
    # A thin wire (40 EU/s) delivers only half the power → the machine runs at
    # 50% duty and its widget yield halves. Electricity is not a feedstock/yield.
    thin = compute_world(_power_world(40))
    assert _close(thin.node_duty["mach1"], 0.5)
    assert _close(thin.external_outputs["widget"], 0.5)
    assert "eu" not in thin.external_inputs and "eu" not in thin.external_outputs
    assert any(b.item_id == "eu" and b.node_id == "mach1" for b in thin.bottlenecks)


def test_storage_node_is_transparent() -> None:
    # A buffer between src and mid must not change the steady-state balance.
    plain = compute_world(_chain_world(0.5, buffer=False))
    buffered = compute_world(_chain_world(0.5, buffer=True))
    assert _close(buffered.external_outputs["c"], plain.external_outputs["c"])
    assert _close(buffered.node_duty["mid"], plain.node_duty["mid"])
    assert _close(buffered.node_duty["sink"], plain.node_duty["sink"])


# --------------------------------------------------------------------------- #
# Pipe junctions — transparent routing dots spliced like storage.
# --------------------------------------------------------------------------- #


def _junction_fanout_world() -> World:
    """src makes 10 w/s → junction → two consumers each wanting 4 w/s."""

    from process_sim.model import PipeNode

    out_s = SlotGroup(Category.SOLID, Direction.OUT, 1, 64)
    in_s = SlotGroup(Category.SOLID, Direction.IN, 1, 64)

    def pipe(pid, a, a_role, b, b_role):
        return Pipe(
            id=pid,
            category=Category.SOLID,
            movement_capacity=100,
            a=PipeEnd(node_id=a, role=a_role),
            b=PipeEnd(node_id=b, role=b_role),
        )

    return World(
        items=[Item("w", "W", Category.SOLID)],
        recipes=[
            Recipe(id="mk", time_seconds=1.0, outputs=[Stack("w", 10)]),
            Recipe(id="use", time_seconds=1.0, inputs=[Stack("w", 4)]),
        ],
        machine_defs=[
            MachineDef(id="S", slots=[out_s], allowed_recipe_ids=["mk"]),
            MachineDef(id="U", slots=[in_s], allowed_recipe_ids=["use"]),
        ],
        nodes=[
            MachineNode(id="src", machine_def_id="S", recipe_id="mk", assume_inputs=True),
            MachineNode(id="u1", machine_def_id="U", recipe_id="use", assume_outputs=True),
            MachineNode(id="u2", machine_def_id="U", recipe_id="use", assume_outputs=True),
        ],
        pipe_nodes=[PipeNode(id="j", category=Category.SOLID)],
        pipes=[
            pipe("p0", "src", PipeEndRole.OUT, "j", PipeEndRole.IO),
            pipe("p1", "j", PipeEndRole.IO, "u1", PipeEndRole.IN),
            pipe("p2", "j", PipeEndRole.IO, "u2", PipeEndRole.IN),
        ],
    )


def test_junction_splices_source_to_many_consumers() -> None:
    report = compute_world(_junction_fanout_world())
    # Both consumers are fully fed through the junction (4 each of the 10 made).
    assert _close(report.balances["u1"].inputs["w"].supplied, 4.0)
    assert _close(report.balances["u2"].inputs["w"].supplied, 4.0)
    assert _close(report.node_duty["u1"], 1.0)
    assert _close(report.node_duty["u2"], 1.0)
    # src throttles to the 8/s actually drawn (its product isn't a feedstock/yield
    # — it's all consumed internally).
    assert _close(report.node_duty["src"], 0.8)
    # Flow is reported on every segment: the trunk carries the sum, branches 4 each.
    assert _close(report.pipe_flows["p0"].flow, 8.0)
    assert _close(report.pipe_flows["p1"].flow, 4.0)
    assert _close(report.pipe_flows["p2"].flow, 4.0)


def test_junction_is_transparent_vs_direct_wiring() -> None:
    # A single consumer fed through a junction matches being wired directly.
    from process_sim.model import PipeNode

    out_s = SlotGroup(Category.SOLID, Direction.OUT, 1, 64)
    in_s = SlotGroup(Category.SOLID, Direction.IN, 1, 64)
    base = dict(
        items=[Item("w", "W", Category.SOLID)],
        recipes=[
            Recipe(id="mk", time_seconds=1.0, outputs=[Stack("w", 3)]),
            Recipe(id="use", time_seconds=1.0, inputs=[Stack("w", 4)]),  # starved
        ],
        machine_defs=[
            MachineDef(id="S", slots=[out_s], allowed_recipe_ids=["mk"]),
            MachineDef(id="U", slots=[in_s], allowed_recipe_ids=["use"]),
        ],
        nodes=[
            MachineNode(id="src", machine_def_id="S", recipe_id="mk", assume_inputs=True),
            MachineNode(id="u", machine_def_id="U", recipe_id="use", assume_outputs=True),
        ],
    )
    direct = World(
        **base,
        pipes=[
            Pipe(
                id="d",
                category=Category.SOLID,
                movement_capacity=100,
                a=PipeEnd("src", PipeEndRole.OUT),
                b=PipeEnd("u", PipeEndRole.IN),
            )
        ],
    )
    via = World(
        **base,
        pipe_nodes=[PipeNode(id="j", category=Category.SOLID)],
        pipes=[
            Pipe(id="a", category=Category.SOLID, movement_capacity=100,
                 a=PipeEnd("src", PipeEndRole.OUT), b=PipeEnd("j", PipeEndRole.IO)),
            Pipe(id="b", category=Category.SOLID, movement_capacity=100,
                 a=PipeEnd("j", PipeEndRole.IO), b=PipeEnd("u", PipeEndRole.IN)),
        ],
    )
    rd, rv = compute_world(direct), compute_world(via)
    assert _close(rv.node_duty["u"], rd.node_duty["u"])  # 3/4 either way
    assert _close(rv.balances["u"].inputs["w"].supplied, 3.0)


def test_trace_process_routes_through_junction() -> None:
    # The two consumers reach each other only via src → junction → consumer, so
    # they are one process even though no pipe names two machines directly.
    proc = set(trace_process(_junction_fanout_world(), "u1"))
    assert proc == {"src", "u1", "u2"}


# --------------------------------------------------------------------------- #
# System scope.
# --------------------------------------------------------------------------- #


def test_system_scope(powered_process: World) -> None:
    powered_process.systems.append(
        System(id="sys1", member_node_ids=["smelter1", "presser1"])
    )
    report = compute_system(powered_process, "sys1")
    assert set(report.node_ids) == {"smelter1", "presser1"}
    # Smelter demand-gates to the presser (duty 0.5): 30×0.5 + 60 = −75.
    assert _close(report.net_energy, -75.0)


def test_system_scope_collapse_invariant(powered_process: World) -> None:
    # `collapsed` is a presentation flag the engine never reads, so a system's
    # report is identical whether it is drawn folded or expanded.
    powered_process.systems.append(
        System(id="sys1", member_node_ids=["smelter1", "presser1"], collapsed=False)
    )
    expanded = compute_system(powered_process, "sys1").to_dict()
    powered_process.systems[0].collapsed = True
    collapsed = compute_system(powered_process, "sys1").to_dict()
    assert expanded == collapsed


def test_nested_system_flattens(powered_process: World) -> None:
    # An outer system holding an inner system plus a loose node flattens to all
    # the real member nodes; its report equals the scope over that node set.
    powered_process.systems.append(
        System(id="inner", member_node_ids=["smelter1", "presser1"])
    )
    powered_process.systems.append(
        System(id="outer", member_system_ids=["inner"], member_node_ids=["gen1"])
    )
    report = compute_system(powered_process, "outer")
    assert set(report.node_ids) == {"smelter1", "presser1", "gen1"}
    # Outer = whole world here (all three nodes), so energy matches the world.
    assert _close(report.net_energy, compute_world(powered_process).net_energy)


# --------------------------------------------------------------------------- #
# Phase 2 — a tag resource routes end-to-end like any other resource.
# --------------------------------------------------------------------------- #


def _tag_world() -> World:
    """Producer outputs a tag resource; consumer inputs the same tag."""

    out_s = SlotGroup(Category.SOLID, Direction.OUT, 1, 64)
    in_s = SlotGroup(Category.SOLID, Direction.IN, 1, 64)
    make = Recipe(
        id="make",
        time_seconds=1.0,
        outputs=[Stack(tag="c:dusts/iron", quantity=5)],
    )
    use = Recipe(
        id="use",
        time_seconds=1.0,
        inputs=[Stack(tag="c:dusts/iron", quantity=5)],
        outputs=[Stack("ingot", 1)],
    )
    return World(
        name="tag",
        items=[
            Item("c:dusts/iron", "Iron Dusts", Category.SOLID, is_tag=True),
            Item("ingot", "Ingot", Category.SOLID),
        ],
        recipes=[make, use],
        machine_defs=[
            MachineDef(id="dmk", allowed_recipe_ids=["make"], slots=[out_s]),
            MachineDef(id="duse", allowed_recipe_ids=["use"], slots=[in_s, out_s]),
        ],
        nodes=[
            MachineNode(id="src", machine_def_id="dmk", recipe_id="make"),
            MachineNode(id="dst", machine_def_id="duse", recipe_id="use"),
        ],
        pipes=[
            Pipe(
                id="p",
                category=Category.SOLID,
                movement_capacity=1000,
                a=PipeEnd(node_id="src", role=PipeEndRole.OUT),
                b=PipeEnd(node_id="dst", role=PipeEndRole.IN),
            )
        ],
    )


def test_tag_resource_routes_through_a_pipe() -> None:
    report = compute_scope(_tag_world(), None)
    # The consumer is fully supplied the tag over the pipe (no shortfall), so it
    # runs at full duty and no tag leaves the scope.
    assert _close(report.balances["dst"].inputs["c:dusts/iron"].supplied, 5.0)
    assert _close(report.external_outputs.get("c:dusts/iron", 0.0), 0.0)
    assert _close(report.external_outputs.get("ingot", 0.0), 1.0)  # ingot is the yield



# --------------------------------------------------------------------------- #
# Run-band status: duty → OK (≥50%) / THROTTLED (<50%) / STALLED (0) / CAPPED.
# --------------------------------------------------------------------------- #


def _banded_chain(src_qty: float) -> World:
    """src makes `src_qty` a/s (assume-fed); mid is a pure consumer wanting 1 a/s,
    so mid duty = src_qty with no surplus of its own (isolating the duty bands)."""

    out_s = SlotGroup(Category.SOLID, Direction.OUT, 1, 64)
    in_s = SlotGroup(Category.SOLID, Direction.IN, 1, 64)
    return World(
        items=[Item("a", "A", Category.SOLID)],
        recipes=[
            Recipe(id="mk", time_seconds=1.0, outputs=[Stack("a", src_qty)]),
            # A pure consumer (no output) → never a surplus of its own.
            Recipe(id="use", time_seconds=1.0, inputs=[Stack("a", 1)]),
        ],
        machine_defs=[
            MachineDef(id="S", slots=[out_s], allowed_recipe_ids=["mk"]),
            MachineDef(id="M", slots=[in_s], allowed_recipe_ids=["use"]),
        ],
        nodes=[
            MachineNode(
                id="src", machine_def_id="S", recipe_id="mk", assume_inputs=True
            ),
            MachineNode(id="mid", machine_def_id="M", recipe_id="use"),
        ],
        pipes=[
            Pipe(
                id="p",
                category=Category.SOLID,
                movement_capacity=1000,
                a=PipeEnd("src", PipeEndRole.OUT),
                b=PipeEnd("mid", PipeEndRole.IN),
            ),
        ],
    )


def test_status_bands_follow_duty() -> None:
    # ≥50% reads OK (green), below 50% THROTTLED (yellow).
    assert compute_world(_banded_chain(0.7)).balances["mid"].status is Status.OK
    assert compute_world(_banded_chain(0.5)).balances["mid"].status is Status.OK
    assert compute_world(_banded_chain(0.3)).balances["mid"].status is Status.THROTTLED
    # Not running at all → STALLED (red).
    assert compute_world(_banded_chain(0.0)).balances["mid"].status is Status.STALLED


def test_capped_status_when_overclock_clamped() -> None:
    # The recipe wants 4 EU/t but the machine's tier ceiling is 2 EU/t, so the
    # overclock is clamped: the machine is "over capacity" → CAPPED (red), even
    # though it is otherwise fully fed and running.
    out_s = SlotGroup(Category.SOLID, Direction.OUT, 1, 64)
    world = World(
        items=[Item("w", "W", Category.SOLID)],
        recipes=[
            Recipe(id="mk", time_seconds=1.0, outputs=[Stack("w", 1)], eu_per_tick=4.0)
        ],
        machine_defs=[
            MachineDef(
                id="D", slots=[out_s], allowed_recipe_ids=["mk"], max_eu_per_tick=2.0
            )
        ],
        nodes=[
            MachineNode(
                id="n",
                machine_def_id="D",
                recipe_id="mk",
                overclock=1.0,
                assume_inputs=True,
                assume_outputs=True,
            )
        ],
    )
    report = compute_world(world)
    assert report.computations["n"].overclock_capped is True
    assert report.balances["n"].status is Status.CAPPED


# --------------------------------------------------------------------------- #
# Bottleneck attribution: a plentiful carrier is never blamed for a shortfall
# that a different, scarce input actually causes.
# --------------------------------------------------------------------------- #


def test_scarce_input_is_blamed_not_the_plentiful_one() -> None:
    out_s = SlotGroup(Category.SOLID, Direction.OUT, 1, 64)
    liq_out = SlotGroup(Category.LIQUID, Direction.OUT, 1, 1)
    in_s = SlotGroup(Category.SOLID, Direction.IN, 1, 64)
    liq_in = SlotGroup(Category.LIQUID, Direction.IN, 1, 1)
    world = World(
        items=[
            Item("ore", "Ore", Category.SOLID),
            Item("steam", "Steam", Category.LIQUID),
            Item("dust", "Dust", Category.SOLID),
        ],
        recipes=[
            Recipe(id="mine", time_seconds=1.0, outputs=[Stack("ore", 0.1)]),  # scarce
            Recipe(
                id="boil", time_seconds=1.0, outputs=[Stack("steam", 1000)]
            ),  # ample
            Recipe(
                id="grind",
                time_seconds=1.0,
                inputs=[Stack("ore", 1), Stack("steam", 40)],
                outputs=[Stack("dust", 1)],
            ),
        ],
        machine_defs=[
            MachineDef(id="MINE", slots=[out_s], allowed_recipe_ids=["mine"]),
            MachineDef(id="BOIL", slots=[liq_out], allowed_recipe_ids=["boil"]),
            MachineDef(
                id="MAC", slots=[in_s, liq_in, out_s], allowed_recipe_ids=["grind"]
            ),
        ],
        nodes=[
            MachineNode(
                id="mine1", machine_def_id="MINE", recipe_id="mine", assume_inputs=True
            ),
            MachineNode(
                id="boil1", machine_def_id="BOIL", recipe_id="boil", assume_inputs=True
            ),
            MachineNode(
                id="mac1", machine_def_id="MAC", recipe_id="grind", assume_outputs=True
            ),
        ],
        pipes=[
            Pipe(
                id="po",
                category=Category.SOLID,
                movement_capacity=1000,
                a=PipeEnd("mine1", PipeEndRole.OUT),
                b=PipeEnd("mac1", PipeEndRole.IN),
            ),
            Pipe(
                id="ps",
                category=Category.LIQUID,
                movement_capacity=1000,
                a=PipeEnd("boil1", PipeEndRole.OUT),
                b=PipeEnd("mac1", PipeEndRole.IN),
            ),
        ],
    )
    report = compute_world(world)
    # Macerator wants 1 ore/s but only 0.1 arrives → 10% duty (THROTTLED), even
    # though steam is 25× plentiful.
    assert _close(report.node_duty["mac1"], 0.1)
    bal = report.balances["mac1"]
    assert bal.status is Status.THROTTLED
    # Only ore is the shortfall / bottleneck — steam is OK despite the node running
    # at 10% (it is not the limiter).
    assert bal.inputs["ore"].status is Status.SHORTFALL
    assert bal.inputs["steam"].status is Status.OK
    assert {b.item_id for b in report.bottlenecks} == {"ore"}


# --------------------------------------------------------------------------- #
# Polymorphic fuel: a source-less FUEL converter burns any wired-in fuel item.
# --------------------------------------------------------------------------- #


def _poly_fuel_world(coal_rate: float, wire_fuel: bool = True) -> World:
    """A source-less FUEL boiler fed `coal_rate` coal/s (eu=1000) + water; it
    needs 0.1 coal/s at full (fuel_drain 100 EU ÷ 1000 eu). Steam is the yield."""

    out_s = SlotGroup(Category.SOLID, Direction.OUT, 1, 64)
    in_s = SlotGroup(Category.SOLID, Direction.IN, 1, 64)
    liq = SlotGroup(Category.LIQUID, Direction.IN, 1, 1)
    liq_o = SlotGroup(Category.LIQUID, Direction.OUT, 1, 1)
    nodes = [
        MachineNode(
            id="coal1", machine_def_id="CM", recipe_id="mkcoal", assume_inputs=True
        ),
        MachineNode(
            id="water1", machine_def_id="WM", recipe_id="mkwater", assume_inputs=True
        ),
        MachineNode(id="boiler", machine_def_id="BOIL"),  # no recipe: a pure converter
    ]
    pipes = [
        Pipe(
            id="pw",
            category=Category.LIQUID,
            movement_capacity=1000,
            a=PipeEnd("water1", PipeEndRole.OUT),
            b=PipeEnd("boiler", PipeEndRole.IN),
        ),
    ]
    if wire_fuel:
        pipes.append(
            Pipe(
                id="pc",
                category=Category.SOLID,
                movement_capacity=1000,
                a=PipeEnd("coal1", PipeEndRole.OUT),
                b=PipeEnd("boiler", PipeEndRole.IN),
            )
        )
    return World(
        items=[
            Item("coal", "Coal", Category.SOLID, eu=1000),
            Item("water", "Water", Category.LIQUID),
            Item("steam", "Steam", Category.LIQUID),
        ],
        recipes=[
            Recipe(id="mkcoal", time_seconds=1.0, outputs=[Stack("coal", coal_rate)]),
            Recipe(id="mkwater", time_seconds=1.0, outputs=[Stack("water", 1000)]),
        ],
        machine_defs=[
            MachineDef(id="CM", slots=[out_s], allowed_recipe_ids=["mkcoal"]),
            MachineDef(id="WM", slots=[liq_o], allowed_recipe_ids=["mkwater"]),
            MachineDef(
                id="BOIL",
                energy_source=EnergySource.FUEL,  # no source_item_id → picks wired fuel
                input_item_id="water",
                output_item_id="steam",
                max_fluid_rate=100,
                material_ratio=16,
                slots=[in_s, liq, liq_o],
            ),
        ],
        nodes=nodes,
        pipes=pipes,
    )


def test_sourceless_fuel_converter_burns_wired_fuel() -> None:
    # Fuel is scarce: 0.05 coal/s vs 0.1/s needed at full → boiler at 50% duty,
    # metered by the coal's eu. Steam yield halves with it.
    report = compute_world(_poly_fuel_world(0.05))
    assert _close(report.node_duty["boiler"], 0.5)
    assert _close(report.external_outputs["steam"], 50.0)
    # The fuel is the bottleneck (metered by eu), not water.
    assert any(
        b.node_id == "boiler" and b.item_id == "coal" for b in report.bottlenecks
    )
    assert report.balances["boiler"].inputs["coal"].status is Status.SHORTFALL
    assert report.balances["boiler"].inputs["water"].status is Status.OK


def test_sourceless_fuel_converter_stalls_with_no_fuel_wired() -> None:
    # Water wired but no fuel at all: the boiler cannot run on nothing.
    report = compute_world(_poly_fuel_world(0.05, wire_fuel=False))
    assert _close(report.node_duty["boiler"], 0.0)
    assert report.balances["boiler"].status is Status.STALLED
    assert report.external_outputs.get("steam", 0.0) == 0.0


# --------------------------------------------------------------------------- #
# Pipe-split fairness: one output split between a concrete consumer and a tag
# consumer must divide by the pipe rules (equal → even), not go wholly to one.
# --------------------------------------------------------------------------- #


def test_shared_output_splits_between_concrete_and_tag_consumers() -> None:
    out_s = SlotGroup(Category.SOLID, Direction.OUT, 1, 64)
    in_s = SlotGroup(Category.SOLID, Direction.IN, 1, 64)

    def pipe(pid, a, b):
        return Pipe(id=pid, category=Category.SOLID, movement_capacity=1000,
                    a=PipeEnd(a, PipeEndRole.OUT), b=PipeEnd(b, PipeEndRole.IN))

    world = World(
        items=[
            Item("dust", "Dust", Category.SOLID),
            Item("c:dusts/x", "X Dusts", Category.SOLID, is_tag=True, members=["dust"]),
            Item("ca", "CA", Category.SOLID),
            Item("cb", "CB", Category.SOLID),
        ],
        recipes=[
            Recipe(id="mk", time_seconds=1.0, outputs=[Stack("dust", 10)]),
            # One consumer names the item directly, the other the tag that holds it.
            Recipe(id="ua", time_seconds=1.0, inputs=[Stack("dust", 10)], outputs=[Stack("ca", 1)]),
            Recipe(id="ub", time_seconds=1.0, inputs=[Stack(tag="c:dusts/x", quantity=10)], outputs=[Stack("cb", 1)]),
        ],
        machine_defs=[
            MachineDef(id="S", slots=[out_s], allowed_recipe_ids=["mk"]),
            MachineDef(id="CA", slots=[in_s, out_s], allowed_recipe_ids=["ua"]),
            MachineDef(id="CB", slots=[in_s, out_s], allowed_recipe_ids=["ub"]),
        ],
        nodes=[
            MachineNode(id="src", machine_def_id="S", recipe_id="mk", assume_inputs=True),
            MachineNode(id="ca", machine_def_id="CA", recipe_id="ua", assume_outputs=True),
            MachineNode(id="cb", machine_def_id="CB", recipe_id="ub", assume_outputs=True),
        ],
        pipes=[pipe("pa", "src", "ca"), pipe("pb", "src", "cb")],
    )
    report = compute_world(world)
    # 10 dust/s split evenly: each consumer gets 5 of the 10 it wants → 50% duty.
    # Neither is starved to 0 by the other claiming the whole output.
    assert _close(report.balances["ca"].inputs["dust"].supplied, 5.0)
    assert _close(report.balances["cb"].inputs["c:dusts/x"].supplied, 5.0)
    assert _close(report.node_duty["ca"], 0.5)
    assert _close(report.node_duty["cb"], 0.5)


def test_surplus_is_blue_overflow_and_throttle_takes_precedence() -> None:
    # src (assume-fed) makes `q` a/s; sink consumes a and makes b with no consumer
    # → b is surplus. At high supply the sink runs ≥50% with surplus → OVERFLOW
    # (blue); starved below 50% the underfed THROTTLED (yellow) wins over surplus.
    out_s = SlotGroup(Category.SOLID, Direction.OUT, 1, 64)
    in_s = SlotGroup(Category.SOLID, Direction.IN, 1, 64)

    def world(q: float) -> World:
        return World(
            items=[Item("a", "A", Category.SOLID), Item("b", "B", Category.SOLID)],
            recipes=[
                Recipe(id="mk", time_seconds=1.0, outputs=[Stack("a", q)]),
                Recipe(id="use", time_seconds=1.0, inputs=[Stack("a", 1)], outputs=[Stack("b", 1)]),
            ],
            machine_defs=[
                MachineDef(id="S", slots=[out_s], allowed_recipe_ids=["mk"]),
                MachineDef(id="M", slots=[in_s, out_s], allowed_recipe_ids=["use"]),
            ],
            nodes=[
                MachineNode(id="src", machine_def_id="S", recipe_id="mk", assume_inputs=True),
                MachineNode(id="sink", machine_def_id="M", recipe_id="use", assume_outputs=True),
            ],
            pipes=[Pipe(id="p", category=Category.SOLID, movement_capacity=1000,
                        a=PipeEnd("src", PipeEndRole.OUT), b=PipeEnd("sink", PipeEndRole.IN))],
        )

    good = compute_world(world(1.0))
    assert _close(good.node_duty["sink"], 1.0)
    assert good.balances["sink"].status is Status.OVERFLOW  # surplus b → blue
    assert good.balances["sink"].outputs["b"].status is Status.OVERFLOW

    starved = compute_world(world(0.3))
    assert _close(starved.node_duty["sink"], 0.3)
    # Underfed (<50%) outranks the incidental surplus → THROTTLED, not OVERFLOW.
    assert starved.balances["sink"].status is Status.THROTTLED

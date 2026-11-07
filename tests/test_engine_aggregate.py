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


def test_furnace_chest_product_is_yield_not_overflow(furnace_chest: World) -> None:
    report = compute_world(furnace_chest)
    furnace = report.balances["furnace1"]
    # Ingots go to a chest (a transparent buffer): they are a product that leaves
    # the scope as yield, not an "overflow". The furnace runs fully supplied
    # (ore manually inserted) with no bottleneck.
    assert furnace.outputs["ingot"].status is Status.OK
    assert furnace.status is Status.OK
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
    # A boiler with nothing wired to its fuel/water inputs can't run: that is a
    # shortfall, not an overflow of steam it cannot actually make.
    report = compute_world(_lone_boiler())
    assert _close(report.node_duty["b"], 0.0)
    assert report.balances["b"].status is Status.SHORTFALL
    assert {b.item_id for b in report.bottlenecks} == {"coal", "water"}
    assert report.external_outputs == {}  # produces nothing


def test_manual_insertion_runs_on_assumed_feedstock() -> None:
    # Flagged for manual insertion, the same boiler assumes its inputs are fed by
    # hand and runs: coal + water become feedstock and steam the yield.
    report = compute_world(_lone_boiler(assume_inputs=True))
    assert _close(report.node_duty["b"], 1.0)
    assert report.balances["b"].status is Status.OK
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

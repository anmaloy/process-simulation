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
    Item,
    MachineDef,
    MachineNode,
    Pipe,
    PipeEnd,
    PipeEndRole,
    Recipe,
    SlotGroup,
    Stack,
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
    # Ore has no internal producer → external input at the furnace's demand rate.
    assert _close(report.external_inputs["ore"], 0.5)
    # The chest doesn't consume, so the furnace's ingots overflow out of scope.
    assert _close(report.external_outputs["ingot"], 0.5)
    # Recipe energy (30) overrides the furnace base (20).
    assert _close(report.net_energy, -30.0)


def test_furnace_chest_overflow_status(furnace_chest: World) -> None:
    report = compute_world(furnace_chest)
    furnace = report.balances["furnace1"]
    assert furnace.outputs["ingot"].status is Status.OVERFLOW
    # Ore is a boundary input (no internal producer) → not flagged a shortfall.
    assert furnace.inputs["ore"].status is Status.OK
    assert report.bottlenecks == []


# --------------------------------------------------------------------------- #
# Powered process: a real chain with a generator.
# --------------------------------------------------------------------------- #


def test_powered_process_chain_flow(powered_process: World) -> None:
    report = compute_world(powered_process)
    # smelter ships 0.5 ingot/s; presser consumes 0.25/s → 0.25 routed internally.
    presser = report.balances["presser1"]
    assert _close(presser.inputs["ingot"].supplied, 0.25)
    assert presser.inputs["ingot"].status is Status.OK
    # Leftover ingot (0.25) and all plate (0.25) leave the scope.
    assert _close(report.external_outputs["ingot"], 0.25)
    assert _close(report.external_outputs["plate"], 0.25)
    # Ore is the only external input.
    assert _close(report.external_inputs["ore"], 0.5)
    assert "ingot" not in report.external_inputs  # demand met internally
    # Energy: 200 produced − 30 − 60 consumed.
    assert _close(report.net_energy, 110.0)
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
    # Still produced internally but under-supplied → also an external input.
    assert _close(report.external_inputs["ingot"], 0.5)


def test_scopes_compose(powered_process: World) -> None:
    # The chain and the isolated generator are two disjoint processes.
    chain = trace_process(powered_process, "smelter1")
    assert set(chain) == {"smelter1", "presser1"}
    gen = trace_process(powered_process, "gen1")
    assert gen == ["gen1"]

    chain_report = compute_scope(powered_process, chain)
    gen_report = compute_scope(powered_process, gen)
    world_report = compute_world(powered_process)

    assert _close(chain_report.net_energy, -90.0)
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
    assert d["netEnergy"] == 110.0


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
# System scope.
# --------------------------------------------------------------------------- #


def test_system_scope(powered_process: World) -> None:
    powered_process.systems.append(
        System(id="sys1", member_node_ids=["smelter1", "presser1"])
    )
    report = compute_system(powered_process, "sys1")
    assert set(report.node_ids) == {"smelter1", "presser1"}
    assert _close(report.net_energy, -90.0)

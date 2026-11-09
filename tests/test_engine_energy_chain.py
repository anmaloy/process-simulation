"""End-to-end energy chain: fuel → steam → electricity → a powered consumer.

The MI import now ships energy-generation converters (boilers, turbines,
generators) and routes electricity as an energy-category carrier. This exercises
that whole chain through the engine (jar-free, hand-built defs mirroring the
imported shapes): a boiler burns fuel to make steam from water, a turbine turns
steam into electricity, and an electric consumer draws that electricity over an
energy pipe. The scope solver must power the consumer, throttle the generators to
demand, and flag a genuine power shortage.
"""

import math

from process_sim.engine import Status, compute_world
from process_sim.model import (
    Category,
    EnergySource,
    Item,
    MachineDef,
    MachineNode,
    Pipe,
    PipeEnd,
    PipeEndRole,
    Recipe,
    Stack,
    World,
)

COAL = "coal"
WATER = "water"
STEAM = "steam"
EU = "electricity"
WIDGET = "widget"


def _close(a: float, b: float) -> bool:
    return math.isclose(a, b, abs_tol=1e-6)


def _items() -> list[Item]:
    return [
        Item(id=COAL, name="Coal", category=Category.SOLID, eu=1600),
        Item(id=WATER, name="Water", category=Category.LIQUID),
        Item(id=STEAM, name="Steam", category=Category.LIQUID),
        Item(id=EU, name="Electricity", category=Category.ENERGY),
        Item(id=WIDGET, name="Widget", category=Category.SOLID),
    ]


def _defs() -> list[MachineDef]:
    boiler = MachineDef(
        id="boiler",
        name="Boiler",
        energy_source=EnergySource.FUEL,
        source_item_id=COAL,
        output_item_id=STEAM,
        input_item_id=WATER,
        material_ratio=16,
        max_fluid_rate=200,  # per second (w = 0 for clean numbers)
    )
    turbine = MachineDef(
        id="turbine",
        name="Turbine",
        energy_source=EnergySource.LIQUID,
        source_item_id=STEAM,
        output_item_id=EU,
        max_fluid_rate=200,  # up to 200 EU/s
    )
    consumer = MachineDef(
        id="consumer",
        name="Consumer",
        energy_source=EnergySource.ELECTRIC,
        source_item_id=EU,  # routed electricity
    )
    return [boiler, turbine, consumer]


def _pipe(id_, cat, src, src_port, sink, sink_port) -> Pipe:
    return Pipe(
        id=id_,
        category=cat,
        a=PipeEnd(node_id=src, role=PipeEndRole.OUT, port=src_port),
        b=PipeEnd(node_id=sink, role=PipeEndRole.IN, port=sink_port),
        movement_capacity=100000.0,
    )


def _world(consumer_eu_per_tick: float) -> World:
    # Consumer: draws power, makes a widget each second (no material inputs).
    consume = Recipe(
        id="r.widget",
        time_seconds=1.0,
        outputs=[Stack(item_id=WIDGET, quantity=1.0)],
        eu_per_tick=consumer_eu_per_tick,
    )
    return World(
        name="energy-chain",
        items=_items(),
        recipes=[consume],
        machine_defs=_defs(),
        nodes=[
            # Boiler is hand-fed coal + water (the scope edge).
            MachineNode(id="b", machine_def_id="boiler", assume_inputs=True),
            MachineNode(id="t", machine_def_id="turbine"),
            MachineNode(id="c", machine_def_id="consumer", recipe_id="r.widget"),
        ],
        pipes=[
            _pipe("p.steam", Category.LIQUID, "b", "liquid-out", "t", "liquid-in"),
            _pipe("p.power", Category.ENERGY, "t", "energy-out", "c", "energy-in"),
        ],
    )


def test_powered_chain_balances() -> None:
    # Consumer draws 5 EU/t = 100 EU/s; the turbine (max 200 EU/s) covers it.
    report = compute_world(_world(consumer_eu_per_tick=5.0))

    assert _close(report.node_duty["c"], 1.0)  # fully powered
    # The widget it makes has no consumer → surplus (routed to storage/void) →
    # OVERFLOW (blue), the good "running with surplus" state.
    assert report.balances["c"].status is Status.OVERFLOW
    assert report.bottlenecks == []

    # Input-driven: the turbine and boiler are never throttled by downstream
    # draw — each runs flat out (the boiler is hand-fed, the turbine has ample
    # steam), making their full 200 EU/s regardless of the 100 the consumer takes.
    assert _close(report.node_duty["t"], 1.0)
    assert _close(report.node_duty["b"], 1.0)

    # Net grid: turbine makes 200 EU/s, consumer draws 100 → 100 EU/s surplus.
    assert _close(report.net_energy, 100.0)

    # Electricity and steam are carriers, not feedstock/yield; coal + water are
    # the manually-inserted feedstock (now at the full 200 EU/s burn), the widget
    # is the product.
    assert EU not in report.external_inputs and EU not in report.external_outputs
    assert STEAM not in report.external_outputs
    assert _close(report.external_inputs[COAL], 200.0 / 1600.0)  # fuel_drain / eu
    assert _close(report.external_inputs[WATER], 200.0 / 16.0)  # steam / ratio
    assert _close(report.external_outputs[WIDGET], 1.0)


def test_underpowered_chain_starves_consumer() -> None:
    # Consumer wants 15 EU/t = 300 EU/s but the turbine maxes at 200 EU/s.
    report = compute_world(_world(consumer_eu_per_tick=15.0))

    # Supplied 200 of 300 wanted → two-thirds duty. Running at 67% with surplus
    # widget reads as OVERFLOW (blue, ≥50% + surplus); the electricity input is
    # still a genuine shortfall and surfaces as a bottleneck + dashed edge (the
    # power limit is reported there, not as an alarm color on the node).
    assert _close(report.node_duty["c"], 200.0 / 300.0)
    assert report.balances["c"].status is Status.OVERFLOW
    assert report.balances["c"].inputs[EU].status is Status.SHORTFALL
    assert any(b.node_id == "c" and b.item_id == EU for b in report.bottlenecks)
    # The generators run flat out to supply what they can.
    assert _close(report.node_duty["t"], 1.0)
    assert _close(report.node_duty["b"], 1.0)


def _loop_defs() -> list[MachineDef]:
    """A boiler (fuel→steam, consuming water) and a steam-powered water pump."""

    boiler = MachineDef(
        id="boiler",
        name="Boiler",
        energy_source=EnergySource.FUEL,
        source_item_id=COAL,
        output_item_id=STEAM,
        input_item_id=WATER,
        material_ratio=16,
        max_fluid_rate=200,
    )
    # Pump: steam-powered consumer running an input-less "pump water" recipe.
    pump = MachineDef(
        id="pump",
        name="Water Pump",
        energy_source=EnergySource.LIQUID,
        source_item_id=STEAM,
        slots=[],
    )
    return [boiler, pump]


def test_generator_pump_feedback_loop_runs_at_capacity() -> None:
    """A boiler↔pump feedback loop must not mutually throttle itself to zero.

    The boiler's steam powers the pump; the pump's water feeds the boiler — a
    cycle. In the input-driven model each starts at full and the other supplies
    it fully, so the loop holds at capacity (rather than collapsing to zero) and
    the surplus of each carrier leaves as yield.
    """

    pump_water = Recipe(
        id="r.pump",
        time_seconds=1.0,
        outputs=[Stack(item_id=WATER, quantity=200.0)],
        eu_per_tick=1.0,  # draws 20 steam/s
    )
    world = World(
        name="loop",
        items=_items(),
        recipes=[pump_water],
        machine_defs=_loop_defs(),
        nodes=[
            # Boiler is hand-fed coal (its fuel); water comes from the pump.
            MachineNode(id="b", machine_def_id="boiler", assume_inputs=True),
            MachineNode(id="p", machine_def_id="pump", recipe_id="r.pump"),
        ],
        pipes=[
            _pipe("p.water", Category.LIQUID, "p", "liquid-out", "b", "liquid-in"),
            _pipe("p.steam", Category.LIQUID, "b", "liquid-out", "p", "liquid-in"),
        ],
    )
    report = compute_world(world)

    # Both run at full capacity rather than collapsing to zero, each making far
    # more of its carrier than the other draws → surplus → OVERFLOW (blue).
    assert _close(report.node_duty["b"], 1.0)
    assert _close(report.node_duty["p"], 1.0)
    assert report.balances["b"].status is Status.OVERFLOW
    assert report.balances["p"].status is Status.OVERFLOW
    assert report.bottlenecks == []
    # Surplus steam and water leave as yield (carriers aside: water is a liquid
    # product, steam the energy carrier is reported but not fed back as a deficit).
    assert report.external_outputs[WATER] > 0.0

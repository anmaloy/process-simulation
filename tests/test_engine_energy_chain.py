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
    assert report.balances["c"].status is Status.OK
    assert report.bottlenecks == []

    # Turbine and boiler throttle to the 100 EU/s the consumer actually draws:
    # 100 / 200 = half duty each.
    assert _close(report.node_duty["t"], 0.5)
    assert _close(report.node_duty["b"], 0.5)

    # Net grid: turbine makes 100 EU/s, consumer draws 100 → balanced at zero.
    assert _close(report.net_energy, 0.0)

    # Electricity and steam are carriers, not feedstock/yield; coal + water are
    # the manually-inserted feedstock, the widget is the product.
    assert EU not in report.external_inputs and EU not in report.external_outputs
    assert STEAM not in report.external_outputs
    assert _close(report.external_inputs[COAL], 100.0 / 1600.0)  # fuel_drain / eu
    assert _close(report.external_inputs[WATER], 100.0 / 16.0)  # steam / ratio
    assert _close(report.external_outputs[WIDGET], 1.0)


def test_underpowered_chain_starves_consumer() -> None:
    # Consumer wants 15 EU/t = 300 EU/s but the turbine maxes at 200 EU/s.
    report = compute_world(_world(consumer_eu_per_tick=15.0))

    # Supplied 200 of 300 wanted → two-thirds duty, flagged as a power shortfall.
    assert _close(report.node_duty["c"], 200.0 / 300.0)
    assert report.balances["c"].status is Status.SHORTFALL
    assert any(b.node_id == "c" and b.item_id == EU for b in report.bottlenecks)
    # The generators run flat out to supply what they can.
    assert _close(report.node_duty["t"], 1.0)
    assert _close(report.node_duty["b"], 1.0)

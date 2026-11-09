"""Tag flow resolution — a concrete producer satisfies a tag consumer.

The engine keys recipe I/O by resource id, where a tag input (e.g.
``c:ingots/iron``) is a distinct id from the concrete items it includes
(``iron_ingot``). Before this, a wired chain whose producer makes the concrete
item and whose consumer demands the tag never balanced — the consumer starved
and the producer's output leaked out as yield. These tests pin the
membership-aware resolution: a tag demand is fed by any concrete supply the tag
includes, that supply backpressures instead of being mistaken for a product,
and a plain concrete chain is unchanged.
"""

import math

from process_sim.engine import Status, compute_world
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
    World,
)


def _close(a: float, b: float) -> bool:
    return math.isclose(a, b, abs_tol=1e-9)


_INGOT = "minecraft:iron_ingot"
_WROUGHT = "minecraft:wrought_iron"
_TAG = "c:ingots/iron"
_GEAR = "mi:iron_gear"


def _solid(id_: str, name: str) -> Item:
    return Item(id=id_, name=name, category=Category.SOLID)


def _tag_item(members: list[str]) -> Item:
    return Item(
        id=_TAG,
        name="Iron Ingots",
        category=Category.SOLID,
        is_tag=True,
        members=members,
    )


def _def(id_: str) -> MachineDef:
    """A plain electric consumer with one solid in and one solid out slot."""

    return MachineDef(
        id=id_,
        name=id_,
        energy_source=EnergySource.ELECTRIC,
        slots=[
            SlotGroup(Category.SOLID, Direction.IN, 1, 64),
            SlotGroup(Category.SOLID, Direction.OUT, 1, 64),
        ],
    )


def _pipe(src: str, sink: str, *, capacity: float = 1000.0) -> Pipe:
    return Pipe(
        id=f"{src}->{sink}",
        category=Category.SOLID,
        a=PipeEnd(node_id=src, role=PipeEndRole.OUT, port="solid-out"),
        b=PipeEnd(node_id=sink, role=PipeEndRole.IN, port="solid-in"),
        movement_capacity=capacity,
    )


def _world(
    *,
    members: list[str],
    producer_item: str = _INGOT,
    producer_overclock: float = 1.0,
) -> World:
    """producer → (tag-input) consumer chain, wired by a solid pipe.

    The producer makes ``producer_item`` (a concrete item); the consumer's recipe
    input is the tag ``c:ingots/iron`` whose ``members`` are given, and it emits a
    concrete gear as its product (``assume_outputs`` so the gear is a clean yield).
    """

    produce = Recipe(
        id="r.produce",
        time_seconds=1.0,
        outputs=[Stack(item_id=producer_item, quantity=1.0)],
    )
    consume = Recipe(
        id="r.consume",
        time_seconds=1.0,
        inputs=[Stack(tag=_TAG, quantity=1.0)],
        outputs=[Stack(item_id=_GEAR, quantity=1.0)],
    )
    items = [
        _solid(_INGOT, "Iron Ingot"),
        _solid(_WROUGHT, "Wrought Iron"),
        _solid(_GEAR, "Iron Gear"),
        _tag_item(members),
    ]
    return World(
        name="tag-chain",
        items=items,
        recipes=[produce, consume],
        machine_defs=[_def("def.producer"), _def("def.consumer")],
        nodes=[
            MachineNode(
                id="producer",
                machine_def_id="def.producer",
                recipe_id="r.produce",
                overclock=producer_overclock,
                assume_inputs=True,  # feedstock hand-fed; isolate the tag link
            ),
            MachineNode(
                id="consumer",
                machine_def_id="def.consumer",
                recipe_id="r.consume",
                assume_outputs=True,  # gear is the product/yield
            ),
        ],
        pipes=[_pipe("producer", "consumer")],
    )


def test_concrete_producer_feeds_tag_consumer() -> None:
    report = compute_world(_world(members=[_INGOT]))

    # The consumer runs fully — its tag demand is met by the concrete ingot. Its
    # gear output has no downstream consumer → surplus → OVERFLOW (blue).
    assert _close(report.node_duty["consumer"], 1.0)
    assert report.balances["consumer"].status is Status.OVERFLOW
    assert report.balances["consumer"].inputs[_TAG].status is Status.OK
    assert _close(report.balances["consumer"].inputs[_TAG].supplied, 1.0)
    assert report.bottlenecks == []

    # The ingot is an intermediate (consumed via the tag), not scope yield; the
    # gear is the product that leaves.
    assert _INGOT not in report.external_outputs
    assert _close(report.external_outputs[_GEAR], 1.0)


def test_tag_demand_with_no_matching_producer_starves() -> None:
    # The tag's only member is wrought iron, but the producer makes plain ingot:
    # nothing satisfies the demand, so the consumer is starved.
    report = compute_world(_world(members=[_WROUGHT]))

    assert _close(report.node_duty["consumer"], 0.0)
    # Not running at all → STALLED (red); the unmet tag input is the shortfall.
    assert report.balances["consumer"].status is Status.STALLED
    assert report.balances["consumer"].inputs[_TAG].status is Status.SHORTFALL
    assert any(
        b.node_id == "consumer" and b.item_id == _TAG for b in report.bottlenecks
    )


def test_concrete_satisfies_one_of_several_tag_members() -> None:
    # Tag includes several items; the one concrete producer is enough.
    report = compute_world(_world(members=[_WROUGHT, _INGOT]))
    assert _close(report.node_duty["consumer"], 1.0)
    assert report.bottlenecks == []


def test_tag_producer_surplus_is_yield() -> None:
    # Producer makes 2/s (overclock 2) but the tag consumer only draws 1/s. In the
    # input-driven model the producer is never throttled by downstream demand: it
    # runs flat out and the unconsumed 1/s of ingot leaves as surplus yield.
    report = compute_world(_world(members=[_INGOT], producer_overclock=2.0))

    assert _close(report.node_duty["consumer"], 1.0)
    assert _close(report.node_duty["producer"], 1.0)
    assert _close(report.external_outputs[_INGOT], 1.0)


def test_plain_concrete_chain_unchanged() -> None:
    # Regression: with the consumer keyed on the concrete item (no tag), the
    # chain still balances exactly as before.
    world = _world(members=[_INGOT])
    consume = next(r for r in world.recipes if r.id == "r.consume")
    consume.inputs = [Stack(item_id=_INGOT, quantity=1.0)]

    report = compute_world(world)
    assert _close(report.node_duty["consumer"], 1.0)
    assert report.balances["consumer"].inputs[_INGOT].status is Status.OK
    assert report.bottlenecks == []
    assert _INGOT not in report.external_outputs
    assert _close(report.external_outputs[_GEAR], 1.0)

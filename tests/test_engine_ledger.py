"""The per-resource ledger (``ScopeReport.item_ledger`` / ``itemLedger``).

The balance sheet in the UI lists every resource a scope touches with what is made,
used, needed at full rate, fed in, yielded and short — plus who makes and uses it.
"""

import math

from process_sim.api import Api
from process_sim.engine import compute_scope, compute_world
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
    World,
)
from tests.test_engine_aggregate import _chain_world


def _close(a: float, b: float) -> bool:
    return math.isclose(a, b, abs_tol=1e-9)


def test_chain_ledger_rows() -> None:
    # src makes 0.5 a/s; mid needs 1 a/s (→ 50% duty); sink follows at 50%.
    led = compute_world(_chain_world(0.5)).item_ledger
    a, b, c = led["a"], led["b"], led["c"]

    assert _close(a.produced, 0.5) and _close(a.capacity, 0.5)
    assert _close(a.consumed, 0.5) and _close(a.demand, 1.0)
    assert _close(a.shortfall, 0.5)  # mid is starved of half its ore
    assert [p["nodeId"] for p in a.producers] == ["src"]
    assert a.consumers == [
        {"nodeId": "mid", "rate": 0.5, "demand": 1.0, "shortfall": 0.5}
    ]

    # b: mid could make 1/s at full rate but makes 0.5; sink wants 1.
    assert _close(b.produced, 0.5) and _close(b.capacity, 1.0)
    assert _close(b.demand, 1.0) and _close(b.consumed, 0.5)

    # c is the product: all of it leaves the scope as yield, nobody consumes it.
    assert _close(c.produced, 0.5) and _close(c.yield_, 0.5)
    assert c.consumers == [] and _close(c.feedstock, 0.0)
    assert c.category == "solid"


def test_scope_slice_reports_feedstock_in_ledger() -> None:
    # A node scope on `mid` alone: its ore comes from outside the slice.
    led = compute_scope(_chain_world(1.0), ["mid"]).item_ledger
    assert _close(led["a"].feedstock, 1.0)
    assert led["a"].producers == []
    assert _close(led["b"].yield_, 1.0)
    assert set(led) == {"a", "b"}


def test_tag_row_lists_member_producers() -> None:
    out_s = SlotGroup(Category.SOLID, Direction.OUT, 1, 64)
    in_s = SlotGroup(Category.SOLID, Direction.IN, 1, 64)
    world = World(
        items=[
            Item("iron", "Iron", Category.SOLID),
            Item("c:metal", "Metal", Category.SOLID, is_tag=True, members=["iron"]),
        ],
        recipes=[
            Recipe(id="mk", time_seconds=1.0, outputs=[Stack("iron", 2)]),
            Recipe(
                id="use", time_seconds=1.0, inputs=[Stack(tag="c:metal", quantity=1)]
            ),
        ],
        machine_defs=[
            MachineDef(id="S", slots=[out_s]),
            MachineDef(id="U", slots=[in_s]),
        ],
        nodes=[
            MachineNode(id="src", machine_def_id="S", recipe_id="mk"),
            MachineNode(id="use", machine_def_id="U", recipe_id="use"),
        ],
        pipes=[
            Pipe(
                id="p",
                category=Category.SOLID,
                movement_capacity=100,
                a=PipeEnd(node_id="src", role=PipeEndRole.OUT),
                b=PipeEnd(node_id="use", role=PipeEndRole.IN),
            )
        ],
    )
    led = compute_world(world).item_ledger
    tag = led["c:metal"]
    assert tag.is_tag
    assert _close(tag.demand, 1.0)
    assert tag.producers == [
        {"nodeId": "src", "rate": 2.0, "capacity": 2.0, "itemId": "iron"}
    ]
    assert _close(tag.produced, 2.0)


def test_api_recompute_carries_item_ledger() -> None:
    api = Api(World())
    api.set_world(_chain_world(0.5).to_dict())
    data = api.recompute({"scope": "world"})["data"]
    row = data["itemLedger"]["a"]
    assert row["itemId"] == "a" and row["category"] == "solid"
    assert _close(row["shortfall"], 0.5) and _close(row["yield"], 0.0)
    assert row["consumers"][0]["nodeId"] == "mid"

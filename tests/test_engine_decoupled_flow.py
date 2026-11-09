"""Regression tests for the decoupled flow + scope-slice fixes.

These pin down the behaviours a real Modern-Industrialization world exposed:

1. A shared carrier fed by **several sources** must deliver its full feasible
   flow — one pass of the even insert/extract split used to strand capacity and
   fabricate a shortfall on a trunk that had surplus (the "false steam
   bottleneck").
2. A machine throttled by one scarce input must not inflate its demand for a
   plentiful shared carrier and starve its siblings of it (duty decoupling), and
   that plentiful carrier must never be blamed as the bottleneck.
3. Each scope is a standalone slice: an input produced elsewhere in the world but
   outside the scope is boundary feedstock (so a single node runs and reports its
   full consumption + yield), while an input nothing in the world produces still
   stalls the node.
4. A memberless ``c:`` tag resolves to a concrete item of the same name (an
   import gap must not silently dead-end a recipe).
"""

import math

from process_sim.engine import Status, compute_scope, compute_world
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


def _close(a: float, b: float) -> bool:
    return math.isclose(a, b, abs_tol=1e-6)


_OUT = SlotGroup(Category.SOLID, Direction.OUT, 1, 64)
_IN = SlotGroup(Category.SOLID, Direction.IN, 1, 64)


def _pipe(pid: str, src: str, dst: str, cat: Category = Category.SOLID) -> Pipe:
    return Pipe(
        id=pid,
        category=cat,
        movement_capacity=10_000,
        a=PipeEnd(src, PipeEndRole.OUT),
        b=PipeEnd(dst, PipeEndRole.IN),
    )


# --------------------------------------------------------------------------- #
# 1. Multi-source carrier delivers its full feasible flow (no stranded capacity).
# --------------------------------------------------------------------------- #


def test_three_sources_fully_feed_one_sink() -> None:
    """Three sources (30/s total, hand-fed) feed one sink wanting 24/s. Even
    though each source alone can't cover the demand, together they must deliver
    all 24 — the single-pass split used to strand some and fake a shortfall."""

    world = World(
        items=[Item("w", "W", Category.SOLID)],
        recipes=[
            Recipe(id="mk", time_seconds=1.0, outputs=[Stack("w", 10)]),
            Recipe(id="use", time_seconds=1.0, inputs=[Stack("w", 24)]),
        ],
        machine_defs=[
            MachineDef(id="S", slots=[_OUT], allowed_recipe_ids=["mk"]),
            MachineDef(id="M", slots=[_IN], allowed_recipe_ids=["use"]),
        ],
        nodes=[
            MachineNode(
                id="s1", machine_def_id="S", recipe_id="mk", assume_inputs=True
            ),
            MachineNode(
                id="s2", machine_def_id="S", recipe_id="mk", assume_inputs=True
            ),
            MachineNode(
                id="s3", machine_def_id="S", recipe_id="mk", assume_inputs=True
            ),
            MachineNode(id="m", machine_def_id="M", recipe_id="use"),
        ],
        pipes=[_pipe("p1", "s1", "m"), _pipe("p2", "s2", "m"), _pipe("p3", "s3", "m")],
    )
    report = compute_world(world)
    # 30 supply ≥ 24 demand → sink runs fully, no shortfall, no bottleneck.
    assert _close(report.node_duty["m"], 1.0)
    assert report.balances["m"].status is Status.OK
    assert report.balances["m"].inputs["w"].status is Status.OK
    assert report.bottlenecks == []


def test_shared_plentiful_carrier_not_blamed_when_another_input_limits() -> None:
    """A machine needs a scarce material and a plentiful shared carrier. It is
    throttled by the material; the carrier (with surplus left) must read OK and
    stay off the bottleneck list — and must not be starved for its siblings."""

    liquid_out = SlotGroup(Category.LIQUID, Direction.OUT, 1, 64)
    liquid_in = SlotGroup(Category.LIQUID, Direction.IN, 1, 64)
    world = World(
        items=[
            Item("ore", "Ore", Category.SOLID),
            Item("steam", "Steam", Category.LIQUID),
            Item("dust", "Dust", Category.SOLID),
        ],
        recipes=[
            # The ore source makes only 1/s — scarce.
            Recipe(id="mkore", time_seconds=1.0, outputs=[Stack("ore", 1)]),
            Recipe(id="mksteam", time_seconds=1.0, outputs=[Stack("steam", 1000)]),
            # Each consumer wants 10 ore + 100 steam per second.
            Recipe(
                id="grind",
                time_seconds=1.0,
                inputs=[Stack("ore", 10), Stack("steam", 100)],
                outputs=[Stack("dust", 10)],
            ),
        ],
        machine_defs=[
            MachineDef(id="O", slots=[_OUT], allowed_recipe_ids=["mkore"]),
            MachineDef(id="B", slots=[liquid_out], allowed_recipe_ids=["mksteam"]),
            MachineDef(
                id="G", slots=[_IN, liquid_in, _OUT], allowed_recipe_ids=["grind"]
            ),
        ],
        nodes=[
            MachineNode(
                id="o", machine_def_id="O", recipe_id="mkore", assume_inputs=True
            ),
            MachineNode(
                id="b", machine_def_id="B", recipe_id="mksteam", assume_inputs=True
            ),
            MachineNode(id="g1", machine_def_id="G", recipe_id="grind"),
            MachineNode(id="g2", machine_def_id="G", recipe_id="grind"),
        ],
        pipes=[
            _pipe("po1", "o", "g1"),
            _pipe("po2", "o", "g2"),
            _pipe("ps1", "b", "g1", Category.LIQUID),
            _pipe("ps2", "b", "g2", Category.LIQUID),
        ],
    )
    report = compute_world(world)
    # 1 ore/s split evenly → 0.5 each → each grinder at 0.5/10 = 5% duty.
    assert _close(report.node_duty["g1"], 0.05)
    assert _close(report.node_duty["g2"], 0.05)
    for g in ("g1", "g2"):
        bal = report.balances[g]
        assert bal.inputs["ore"].status is Status.SHORTFALL  # the real limiter
        assert bal.inputs["steam"].status is Status.OK  # plentiful, not blamed
    # No steam anywhere in the bottleneck list; ore is.
    assert all(b.item_id != "steam" for b in report.bottlenecks)
    assert any(b.item_id == "ore" for b in report.bottlenecks)


# --------------------------------------------------------------------------- #
# 2. Scope is a standalone slice.
# --------------------------------------------------------------------------- #


def _two_stage_world() -> World:
    return World(
        items=[Item("a", "A", Category.SOLID), Item("b", "B", Category.SOLID)],
        recipes=[
            Recipe(id="mk", time_seconds=1.0, outputs=[Stack("a", 1)]),
            Recipe(
                id="use",
                time_seconds=1.0,
                inputs=[Stack("a", 1)],
                outputs=[Stack("b", 1)],
            ),
        ],
        machine_defs=[
            MachineDef(id="S", slots=[_OUT], allowed_recipe_ids=["mk"]),
            MachineDef(id="M", slots=[_IN, _OUT], allowed_recipe_ids=["use"]),
        ],
        nodes=[
            MachineNode(
                id="src", machine_def_id="S", recipe_id="mk", assume_inputs=True
            ),
            MachineNode(id="mid", machine_def_id="M", recipe_id="use"),
        ],
        pipes=[_pipe("p", "src", "mid")],
    )


def test_node_scope_is_a_standalone_slice() -> None:
    """In the single-node view of ``mid``, its input ``a`` is produced elsewhere
    in the world (by ``src``, outside this scope) → boundary feedstock, so the
    node runs at full and reports its own consumption and full yield — the output
    that is 'used later' by ``src``'s downstream is still yield in *this* view."""

    world = _two_stage_world()
    report = compute_scope(world, ["mid"])
    assert _close(report.node_duty["mid"], 1.0)
    assert report.balances["mid"].status is Status.OK
    assert _close(report.external_inputs["a"], 1.0)  # feedstock crossing boundary
    assert _close(report.external_outputs["b"], 1.0)  # full yield
    assert report.bottlenecks == []


def test_world_scope_unsourced_input_still_stalls() -> None:
    """The boundary rule must not leak into the world scope: an input nothing in
    the world produces is not feedstock — the node stalls with a real shortfall."""

    world = World(
        items=[Item("x", "X", Category.SOLID), Item("y", "Y", Category.SOLID)],
        recipes=[
            Recipe(
                id="use",
                time_seconds=1.0,
                inputs=[Stack("x", 1)],
                outputs=[Stack("y", 1)],
            )
        ],
        machine_defs=[
            MachineDef(id="M", slots=[_IN, _OUT], allowed_recipe_ids=["use"])
        ],
        nodes=[MachineNode(id="m", machine_def_id="M", recipe_id="use")],
    )
    report = compute_world(world)
    assert _close(report.node_duty["m"], 0.0)
    assert report.balances["m"].status is Status.STALLED
    assert report.balances["m"].inputs["x"].status is Status.SHORTFALL
    assert report.external_outputs == {}


# --------------------------------------------------------------------------- #
# 3. Memberless tag resolves by naming convention.
# --------------------------------------------------------------------------- #


def test_memberless_tag_matches_concrete_item_of_same_name() -> None:
    """A ``c:creosote`` tag with no members is satisfied by a concrete
    ``mod:creosote`` producer (an import gap must not dead-end the recipe)."""

    world = World(
        items=[
            Item("mod:creosote", "Creosote", Category.LIQUID),
            Item("c:creosote", "Creosote", Category.LIQUID, is_tag=True, members=[]),
            Item("lube", "Lubricant", Category.LIQUID),
        ],
        recipes=[
            Recipe(id="mk", time_seconds=1.0, outputs=[Stack("mod:creosote", 5)]),
            Recipe(
                id="mix",
                time_seconds=1.0,
                inputs=[Stack("c:creosote", 5)],
                outputs=[Stack("lube", 5)],
            ),
        ],
        machine_defs=[
            MachineDef(
                id="K",
                slots=[SlotGroup(Category.LIQUID, Direction.OUT, 1, 64)],
                allowed_recipe_ids=["mk"],
            ),
            MachineDef(
                id="X",
                slots=[
                    SlotGroup(Category.LIQUID, Direction.IN, 1, 64),
                    SlotGroup(Category.LIQUID, Direction.OUT, 1, 64),
                ],
                allowed_recipe_ids=["mix"],
            ),
        ],
        nodes=[
            MachineNode(id="k", machine_def_id="K", recipe_id="mk", assume_inputs=True),
            MachineNode(id="x", machine_def_id="X", recipe_id="mix"),
        ],
        pipes=[_pipe("p", "k", "x", Category.LIQUID)],
    )
    report = compute_world(world)
    assert _close(report.node_duty["x"], 1.0)  # creosote reaches the mixer
    assert report.balances["x"].status is Status.OK
    assert _close(report.external_outputs["lube"], 5.0)

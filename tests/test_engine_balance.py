"""Phase 2b — per-node balance.

Acceptance bar (WORKFLOW P2b): the balance table for a node matches hand calcs
across OK / shortfall / overflow cases.
"""

from process_sim.engine import NodeComputation, Status, balance_node


def _comp(inputs=None, outputs=None) -> NodeComputation:
    return NodeComputation(
        node_id="n1",
        cycles_per_sec=1.0,
        inputs=inputs or {},
        outputs=outputs or {},
    )


def test_input_ok_when_fully_supplied() -> None:
    bal = balance_node(_comp(inputs={"ore": 5.0}), supplied={"ore": 5.0})
    ib = bal.inputs["ore"]
    assert ib.shortfall == 0.0 and ib.status is Status.OK
    assert bal.status is Status.OK


def test_input_shortfall() -> None:
    bal = balance_node(_comp(inputs={"ore": 5.0}), supplied={"ore": 2.0})
    ib = bal.inputs["ore"]
    assert ib.shortfall == 3.0 and ib.status is Status.SHORTFALL
    assert bal.status is Status.SHORTFALL


def test_missing_supply_is_full_shortfall() -> None:
    bal = balance_node(_comp(inputs={"ore": 5.0}))  # nothing supplied
    assert bal.inputs["ore"].shortfall == 5.0
    assert bal.status is Status.SHORTFALL


def test_output_ok_when_fully_demanded() -> None:
    bal = balance_node(_comp(outputs={"ingot": 4.0}), demanded={"ingot": 4.0})
    ob = bal.outputs["ingot"]
    assert ob.overflow == 0.0 and ob.status is Status.OK


def test_output_overflow() -> None:
    bal = balance_node(_comp(outputs={"ingot": 4.0}), demanded={"ingot": 1.0})
    ob = bal.outputs["ingot"]
    assert ob.overflow == 3.0 and ob.status is Status.OVERFLOW
    assert bal.status is Status.OVERFLOW


def test_missing_demand_is_full_overflow() -> None:
    bal = balance_node(_comp(outputs={"ingot": 4.0}))  # nothing demanded
    assert bal.outputs["ingot"].overflow == 4.0
    assert bal.status is Status.OVERFLOW


def test_shortfall_outranks_overflow_overall() -> None:
    bal = balance_node(
        _comp(inputs={"ore": 5.0}, outputs={"ingot": 4.0}),
        supplied={"ore": 1.0},
        demanded={"ingot": 0.0},
    )
    # input starved and output overflowing — the starvation wins the overall state
    assert bal.inputs["ore"].status is Status.SHORTFALL
    assert bal.outputs["ingot"].status is Status.OVERFLOW
    assert bal.status is Status.SHORTFALL

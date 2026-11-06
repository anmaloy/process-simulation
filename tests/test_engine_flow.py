"""Phase 2c — pipe flow resolution, the asymmetric priority rules.

Acceptance bar (WORKFLOW P2c): all five canonical cases in ``CLAUDE.md`` pass as
named tests. Insert favors higher priority, extract favors lower — the asymmetry
is the point and must not collapse into one rule.
"""

import math

from process_sim.engine import (
    Claim,
    pipe_throughput,
    resolve_extract,
    resolve_insert,
    will_reinsert,
)


def _close(a: float, b: float) -> bool:
    return math.isclose(a, b, abs_tol=1e-9)


# --------------------------------------------------------------------------- #
# Case 1 — furnace into two chests: main insert 10, overflow insert 0.
# Main fills first; spills to overflow only when full.
# --------------------------------------------------------------------------- #


def test_case1_furnace_fills_main_then_overflow() -> None:
    claims = [
        Claim("main", capacity=60, priority=10),
        Claim("overflow", capacity=1000, priority=0),
    ]
    got = resolve_insert(100, claims)
    assert _close(got["main"], 60)  # main filled to capacity first
    assert _close(got["overflow"], 40)  # remainder spills to overflow


def test_case1_no_spill_until_main_full() -> None:
    claims = [
        Claim("main", capacity=60, priority=10),
        Claim("overflow", capacity=1000, priority=0),
    ]
    got = resolve_insert(40, claims)
    assert _close(got["main"], 40)
    assert _close(got["overflow"], 0)  # overflow untouched while main has room


# --------------------------------------------------------------------------- #
# Case 2 — one stream into three machines all at insert 0: even round-robin.
# --------------------------------------------------------------------------- #


def test_case2_triple_even_split() -> None:
    claims = [Claim(f"m{i}", capacity=1000, priority=0) for i in range(3)]
    got = resolve_insert(90, claims)
    assert all(_close(v, 30) for v in got.values())


def test_case2_even_split_respects_caps() -> None:
    # One small consumer caps out; its surplus redistributes evenly to the rest.
    claims = [
        Claim("small", capacity=10, priority=0),
        Claim("a", capacity=1000, priority=0),
        Claim("b", capacity=1000, priority=0),
    ]
    got = resolve_insert(90, claims)
    assert _close(got["small"], 10)
    assert _close(got["a"], 40)
    assert _close(got["b"], 40)


# --------------------------------------------------------------------------- #
# Case 3 — io pipe filtering a barrel, extract 5 insert 0: won't re-insert
# (insert < extract).
# --------------------------------------------------------------------------- #


def test_case3_self_loop_guard_blocks_reinsert() -> None:
    assert will_reinsert(insert_priority=0, extract_priority=5) is False


def test_case3_reinserts_when_insert_ge_extract() -> None:
    assert will_reinsert(insert_priority=5, extract_priority=0) is True
    assert (
        will_reinsert(insert_priority=5, extract_priority=5) is True
    )  # equal → allowed
    assert will_reinsert(None, None) is True  # defaults 0/0 → allowed


# --------------------------------------------------------------------------- #
# Case 4 — two sources into one machine, A extract 0, B extract 10:
# A drains fully before B is touched.
# --------------------------------------------------------------------------- #


def test_case4_lower_extract_drains_first() -> None:
    claims = [
        Claim("A", capacity=30, priority=0),
        Claim("B", capacity=1000, priority=10),
    ]
    got = resolve_extract(100, claims)
    assert _close(got["A"], 30)  # A fully drained first
    assert _close(got["B"], 70)  # B supplies the remainder


def test_case4_b_untouched_while_a_suffices() -> None:
    claims = [
        Claim("A", capacity=30, priority=0),
        Claim("B", capacity=1000, priority=10),
    ]
    got = resolve_extract(20, claims)
    assert _close(got["A"], 20)
    assert _close(got["B"], 0)


# --------------------------------------------------------------------------- #
# Case 5 — a chest at insert 127, everything else ≤ 0: always filled first.
# --------------------------------------------------------------------------- #


def test_case5_max_priority_filled_first() -> None:
    claims = [
        Claim("chest", capacity=1000, priority=127),
        Claim("other", capacity=1000, priority=0),
        Claim("low", capacity=1000, priority=-50),
    ]
    got = resolve_insert(50, claims)
    assert _close(got["chest"], 50)
    assert _close(got["other"], 0)
    assert _close(got["low"], 0)


def test_case5_max_priority_overflows_to_next() -> None:
    claims = [
        Claim("chest", capacity=1000, priority=127),
        Claim("other", capacity=1000, priority=0),
    ]
    got = resolve_insert(1050, claims)
    assert _close(got["chest"], 1000)  # filled to capacity first
    assert _close(got["other"], 50)


# --------------------------------------------------------------------------- #
# Asymmetry sanity + helpers.
# --------------------------------------------------------------------------- #


def test_insert_and_extract_order_oppositely() -> None:
    claims = [
        Claim("hi", capacity=10, priority=10),
        Claim("lo", capacity=10, priority=0),
    ]
    ins = resolve_insert(10, claims)
    ext = resolve_extract(10, claims)
    assert _close(ins["hi"], 10) and _close(ins["lo"], 0)  # insert → higher first
    assert _close(ext["lo"], 10) and _close(ext["hi"], 0)  # extract → lower first


def test_none_priority_defaults_to_zero() -> None:
    claims = [
        Claim("a", capacity=100, priority=None),
        Claim("b", capacity=100, priority=0),
    ]
    got = resolve_insert(60, claims)
    assert _close(got["a"], 30) and _close(got["b"], 30)  # both at 0 → even


def test_available_capped_by_total_capacity() -> None:
    claims = [Claim("a", capacity=10, priority=0)]
    got = resolve_insert(999, claims)
    assert _close(got["a"], 10)  # cannot exceed capacity


def test_pipe_throughput_scales_with_overclock() -> None:
    assert pipe_throughput(10, overclock=2.0) == 20
    assert pipe_throughput(10) == 10

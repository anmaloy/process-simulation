"""Pipe flow resolution — the asymmetric priority rules (WORKFLOW P2c).

This is the subtlest module; it is isolated and driven entirely by the five
canonical cases in ``CLAUDE.md``. The asymmetry is intentional and must not be
collapsed into one rule:

- **Insert contention** (one source → several destinations): fill in
  **descending** insert priority (higher first). Equal priority splits evenly.
- **Extract contention** (several sources → one destination): drain in
  **ascending** extract priority (lower first). Equal priority splits evenly.
- **Self-loop guard**: an ``io`` end that extracts from and inserts into the same
  buffer won't re-insert what it pulled when its insert priority is below its
  extract priority.

Two seams keep the policy swappable:

- ``_order_groups`` — the **comparator**: how priorities are ordered (the only
  place the insert/extract asymmetry lives).
- ``_fill_group`` — the **batcher**: how an equal-priority group splits a
  quantity. The steady-state model splits evenly (water-filling with per-claim
  caps); ``batch_size`` (a pipe's movement capacity) is threaded through for a
  future tick-based batcher but does not change the even steady-state result.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

__all__ = [
    "Claim",
    "resolve_insert",
    "resolve_extract",
    "will_reinsert",
    "pipe_throughput",
]

_EPS = 1e-9


@dataclass(frozen=True)
class Claim:
    """A contender for flow.

    ``id`` identifies the destination (insert contention) or source (extract
    contention). ``capacity`` is the most it can take / give (demand or
    available supply). ``priority`` is the relevant signed priority (insert or
    extract); ``None`` defaults to 0.
    """

    id: str
    capacity: float
    priority: int | None = 0


def resolve_insert(
    available: float, claims: list[Claim], *, batch_size: float | None = None
) -> dict[str, float]:
    """Distribute ``available`` across destinations in **descending** priority.

    Higher insert priority fills first (up to its capacity); equal priority
    splits evenly. Used when one source feeds several destinations.
    """

    return _resolve(available, claims, descending=True, batch_size=batch_size)


def resolve_extract(
    demand: float, claims: list[Claim], *, batch_size: float | None = None
) -> dict[str, float]:
    """Draw ``demand`` from sources in **ascending** priority.

    Lower extract priority drains first (fully, up to its capacity); equal
    priority splits evenly. Used when several sources feed one destination.
    """

    return _resolve(demand, claims, descending=False, batch_size=batch_size)


def will_reinsert(insert_priority: int | None, extract_priority: int | None) -> bool:
    """Self-loop guard: may an ``io`` end re-insert what it just extracted?

    An ``io`` end feeding its own buffer won't re-insert when its insert
    priority is **below** its extract priority. ``None`` priorities default to 0.
    """

    ins = 0 if insert_priority is None else insert_priority
    ext = 0 if extract_priority is None else extract_priority
    return not (ins < ext)


def pipe_throughput(movement_capacity: float, overclock: float = 1.0) -> float:
    """Sustained flow cap: ``movementCapacity * overclock`` (storage only buffers)."""

    return movement_capacity * overclock


# --------------------------------------------------------------------------- #
# Internals — the comparator seam and the batcher seam.
# --------------------------------------------------------------------------- #


def _resolve(
    total: float,
    claims: list[Claim],
    *,
    descending: bool,
    batch_size: float | None,
) -> dict[str, float]:
    """Allocate ``total`` over priority-ordered groups, even-splitting ties."""

    result: dict[str, float] = {c.id: 0.0 for c in claims}
    remaining = max(0.0, total)

    for group in _order_groups(claims, descending=descending):
        if remaining <= _EPS:
            break
        remaining = _fill_group(group, remaining, result, batch_size)

    return result


def _order_groups(claims: list[Claim], *, descending: bool) -> list[list[Claim]]:
    """Comparator seam: group claims by priority and order the groups.

    Descending for insert (higher first), ascending for extract (lower first).
    This single function is where the insert/extract asymmetry is expressed.
    """

    buckets: OrderedDict[int, list[Claim]] = OrderedDict()
    for c in claims:
        buckets.setdefault(0 if c.priority is None else c.priority, []).append(c)
    return [buckets[p] for p in sorted(buckets, reverse=descending)]


def _fill_group(
    group: list[Claim],
    remaining: float,
    result: dict[str, float],
    batch_size: float | None,  # noqa: ARG001 — reserved for a tick-based batcher
) -> float:
    """Batcher seam: split ``remaining`` evenly across one equal-priority group.

    Water-filling with per-claim capacity caps: each active claimant takes an
    equal share; any whose capacity is below the share is capped and its surplus
    redistributes to the rest. Returns the quantity still unallocated.
    """

    caps = {c.id: c.capacity for c in group}
    active = {c.id for c in group if caps[c.id] > _EPS}

    while remaining > _EPS and active:
        share = remaining / len(active)
        capped = [cid for cid in active if caps[cid] - result[cid] <= share + _EPS]
        if capped:
            for cid in capped:
                room = caps[cid] - result[cid]
                result[cid] += room
                remaining -= room
                active.discard(cid)
        else:
            for cid in active:
                result[cid] += share
            remaining = 0.0

    return remaining

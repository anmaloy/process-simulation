"""Shared enums and (de)serialization helpers for the domain model.

The JSON wire format uses ``camelCase`` keys (the World crosses to the
TypeScript frontend as JSON), while Python attributes stay ``snake_case``.
Conversion happens in one place here.

Serialization rule — **omit a key iff its value is ``None``**. An unset
optional field is *absent* from the dict, never serialized as ``0``. This is
load-bearing: the engine skips absent terms and the UI hides absent fields, so
"unset" must survive a round-trip as absence, not as a defaulted zero. Empty
lists are emitted as ``[]`` (an empty collection is not an unset scalar).
"""

from __future__ import annotations

from dataclasses import fields as dataclass_fields
from enum import Enum, StrEnum
from typing import Any

__all__ = [
    "Category",
    "Direction",
    "PipeEndRole",
    "Serializable",
    "to_camel",
]


class Category(StrEnum):
    """The kind of resource an item, port, or pipe carries."""

    SOLID = "solid"
    LIQUID = "liquid"
    ENERGY = "energy"


class Direction(StrEnum):
    """Flow direction of a machine slot group."""

    IN = "in"
    OUT = "out"


class PipeEndRole(StrEnum):
    """What a pipe end does at its buffer: insert only, extract only, or both."""

    IN = "in"
    OUT = "out"
    IO = "io"


def to_camel(snake: str) -> str:
    """Convert a ``snake_case`` field name to ``camelCase`` for the JSON wire."""

    head, *rest = snake.split("_")
    return head + "".join(word[:1].upper() + word[1:] for word in rest)


def _serialize(value: Any) -> Any:
    """Recursively convert a value to JSON-friendly form."""

    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Serializable):
        return value.to_dict()
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    return value


class Serializable:
    """Mixin giving dataclasses a sparse, camelCase :meth:`to_dict`.

    Each concrete entity defines its own :meth:`from_dict` because
    reconstructing nested dataclasses and enums needs per-field type knowledge.
    """

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for field in dataclass_fields(self):  # type: ignore[arg-type]
            value = getattr(self, field.name)
            if value is None:
                continue
            out[to_camel(field.name)] = _serialize(value)
        return out

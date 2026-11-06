"""Project files — load/save a whole :class:`World` as a single JSON file.

The project file is the World wrapped in a versioned envelope::

    {"schemaVersion": 1, "kind": "project", "world": { ...World.to_dict()... }}

Save→load is lossless: it reuses the model's own ``to_dict`` / ``from_dict``,
so the Phase-1 round-trip guarantees (including "unset stays absent") hold on
disk too.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..model import World
from ._format import (
    PROJECT_KIND,
    SCHEMA_VERSION,
    read_json,
    validate_envelope,
    write_json,
)
from .errors import MalformedFileError

__all__ = [
    "project_to_dict",
    "project_from_dict",
    "save_project",
    "load_project",
]


def project_to_dict(world: World) -> dict[str, Any]:
    """Wrap a World in a versioned project envelope (no file touched)."""

    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": PROJECT_KIND,
        "world": world.to_dict(),
    }


def project_from_dict(data: Any) -> World:
    """Reconstruct a World from a project envelope, validating it first."""

    validate_envelope(data, PROJECT_KIND)
    world = data.get("world")
    if not isinstance(world, dict):
        raise MalformedFileError("project file has no 'world' object")
    try:
        return World.from_dict(world)
    except (KeyError, TypeError, ValueError) as exc:
        raise MalformedFileError(f"project 'world' is malformed: {exc}") from exc


def save_project(world: World, path: str | Path) -> None:
    """Write ``world`` to ``path`` as a JSON project file."""

    write_json(path, project_to_dict(world))


def load_project(path: str | Path) -> World:
    """Read a JSON project file at ``path`` back into a World."""

    return project_from_dict(read_json(path))

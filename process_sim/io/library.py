"""Libraries — export/import reusable items, recipes, and machine defs.

A library is the *definitional* subset of a World: items, recipes, and machine
defs (the reusable building blocks), without the placed nodes, pipes, or systems
that make up a specific graph. It serializes under its own versioned envelope::

    {"schemaVersion": 1, "kind": "library",
     "name": ..., "items": [...], "recipes": [...], "machineDefs": [...]}

Importing a library into another World must not corrupt it through id
collisions. :func:`import_library` resolves collisions per :class:`ConflictPolicy`
and rewrites internal references (a recipe's stacks point at item ids; a machine
def's ``allowedRecipeIds`` point at recipe ids) so the merged World stays
consistent.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from ..model import Item, MachineDef, Recipe, World
from ._format import (
    LIBRARY_KIND,
    SCHEMA_VERSION,
    read_json,
    validate_envelope,
    write_json,
)
from .errors import MalformedFileError, ProcessSimIOError

__all__ = [
    "Library",
    "ConflictPolicy",
    "ImportResult",
    "export_library",
    "import_library",
    "library_to_dict",
    "library_from_dict",
    "save_library",
    "load_library",
]


@dataclass
class Library:
    """A reusable subset of a World: items, recipes, and machine defs."""

    name: str | None = None
    items: list[Item] = field(default_factory=list)
    recipes: list[Recipe] = field(default_factory=list)
    machine_defs: list[MachineDef] = field(default_factory=list)


class ConflictPolicy(StrEnum):
    """How :func:`import_library` handles an incoming id already in the World.

    - ``RENAME`` (default): give the incoming entity a fresh id and rewrite every
      reference to it within the library.
    - ``SKIP``: drop the incoming entity; references to it resolve to the World's
      existing entity of the same id.
    - ``ERROR``: raise on the first collision.
    """

    RENAME = "rename"
    SKIP = "skip"
    ERROR = "error"


@dataclass
class ImportResult:
    """Outcome of :func:`import_library`.

    ``world`` is the merged World (the input is never mutated). ``id_remap`` maps
    each renamed entity's old id to its new one; ``skipped_ids`` lists ids dropped
    under :attr:`ConflictPolicy.SKIP`.
    """

    world: World
    id_remap: dict[str, str] = field(default_factory=dict)
    skipped_ids: set[str] = field(default_factory=set)


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #


def export_library(
    world: World,
    *,
    name: str | None = None,
    item_ids: list[str] | None = None,
    recipe_ids: list[str] | None = None,
    machine_def_ids: list[str] | None = None,
) -> Library:
    """Build a :class:`Library` from a World.

    Each ``*_ids`` filter, when ``None``, selects *all* entities of that kind;
    when given, selects exactly those ids (unknown ids are ignored). Entities are
    deep-copied, so later edits to the World don't mutate the library. ``name``
    defaults to the World's name.
    """

    def pick(entities: list[Any], ids: list[str] | None) -> list[Any]:
        if ids is None:
            return [copy.deepcopy(e) for e in entities]
        wanted = set(ids)
        return [copy.deepcopy(e) for e in entities if e.id in wanted]

    return Library(
        name=name if name is not None else world.name,
        items=pick(world.items, item_ids),
        recipes=pick(world.recipes, recipe_ids),
        machine_defs=pick(world.machine_defs, machine_def_ids),
    )


# --------------------------------------------------------------------------- #
# Import
# --------------------------------------------------------------------------- #


def _fresh_id(base: str, used: set[str]) -> str:
    """Return ``base`` suffixed with the lowest ``-N`` not already in ``used``."""

    n = 2
    while f"{base}-{n}" in used:
        n += 1
    return f"{base}-{n}"


def import_library(
    world: World,
    library: Library,
    *,
    on_conflict: ConflictPolicy = ConflictPolicy.RENAME,
) -> ImportResult:
    """Merge ``library`` into ``world``, returning a new merged World.

    The input World is not mutated. Id collisions (an incoming id that the World
    already uses for the same kind) are resolved per ``on_conflict``, and intra-
    library references are rewritten to match.
    """

    existing_items = {i.id for i in world.items}
    existing_recipes = {r.id for r in world.recipes}
    existing_defs = {d.id for d in world.machine_defs}

    # Fresh ids must dodge every id already in play, World- and library-wide,
    # across all kinds — so a generated id can never clash with anything.
    used: set[str] = set(existing_items | existing_recipes | existing_defs)
    used.update(i.id for i in library.items)
    used.update(r.id for r in library.recipes)
    used.update(d.id for d in library.machine_defs)

    item_remap: dict[str, str] = {}
    recipe_remap: dict[str, str] = {}
    def_remap: dict[str, str] = {}
    skipped: set[str] = set()

    def plan(ids: list[str], existing: set[str], remap: dict[str, str]) -> None:
        for old in ids:
            if old not in existing:
                continue
            if on_conflict is ConflictPolicy.ERROR:
                raise ProcessSimIOError(
                    f"id collision on import: '{old}' already exists in the World"
                )
            if on_conflict is ConflictPolicy.SKIP:
                skipped.add(old)
            else:  # RENAME
                new = _fresh_id(old, used)
                used.add(new)
                remap[old] = new

    plan([i.id for i in library.items], existing_items, item_remap)
    plan([r.id for r in library.recipes], existing_recipes, recipe_remap)
    plan([d.id for d in library.machine_defs], existing_defs, def_remap)

    new_items: list[Item] = []
    for item in library.items:
        if item.id in skipped:
            continue
        clone = copy.deepcopy(item)
        clone.id = item_remap.get(item.id, item.id)
        new_items.append(clone)

    new_recipes: list[Recipe] = []
    for recipe in library.recipes:
        if recipe.id in skipped:
            continue
        clone = copy.deepcopy(recipe)
        clone.id = recipe_remap.get(recipe.id, recipe.id)
        for stack in (*clone.inputs, *clone.outputs):
            if stack.item_id is not None:
                stack.item_id = item_remap.get(stack.item_id, stack.item_id)
            if stack.tag is not None:
                stack.tag = item_remap.get(stack.tag, stack.tag)
        new_recipes.append(clone)

    new_defs: list[MachineDef] = []
    for mdef in library.machine_defs:
        if mdef.id in skipped:
            continue
        clone = copy.deepcopy(mdef)
        clone.id = def_remap.get(mdef.id, mdef.id)
        clone.allowed_recipe_ids = [
            recipe_remap.get(r, r) for r in clone.allowed_recipe_ids
        ]
        new_defs.append(clone)

    merged = World(
        name=world.name,
        items=[*world.items, *new_items],
        recipes=[*world.recipes, *new_recipes],
        machine_defs=[*world.machine_defs, *new_defs],
        nodes=list(world.nodes),
        pipes=list(world.pipes),
        pipe_nodes=list(world.pipe_nodes),
        systems=list(world.systems),
    )
    return ImportResult(
        world=merged,
        id_remap={**item_remap, **recipe_remap, **def_remap},
        skipped_ids=skipped,
    )


# --------------------------------------------------------------------------- #
# Serialization
# --------------------------------------------------------------------------- #


def library_to_dict(library: Library) -> dict[str, Any]:
    """Wrap a Library in a versioned library envelope (no file touched)."""

    out: dict[str, Any] = {"schemaVersion": SCHEMA_VERSION, "kind": LIBRARY_KIND}
    if library.name is not None:
        out["name"] = library.name
    out["items"] = [i.to_dict() for i in library.items]
    out["recipes"] = [r.to_dict() for r in library.recipes]
    out["machineDefs"] = [d.to_dict() for d in library.machine_defs]
    return out


def library_from_dict(data: Any) -> Library:
    """Reconstruct a Library from a library envelope, validating it first."""

    validate_envelope(data, LIBRARY_KIND)
    try:
        return Library(
            name=data.get("name"),
            items=[Item.from_dict(x) for x in data.get("items", [])],
            recipes=[Recipe.from_dict(x) for x in data.get("recipes", [])],
            machine_defs=[MachineDef.from_dict(x) for x in data.get("machineDefs", [])],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise MalformedFileError(f"library payload is malformed: {exc}") from exc


def save_library(library: Library, path: str | Path) -> None:
    """Write ``library`` to ``path`` as a JSON library file."""

    write_json(path, library_to_dict(library))


def load_library(path: str | Path) -> Library:
    """Read a JSON library file at ``path`` back into a Library."""

    return library_from_dict(read_json(path))

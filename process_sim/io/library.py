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
    "LIBRARY_KINDS",
    "library_ids",
    "sync_library",
    "remove_from_library",
    "merge_libraries",
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
      existing entity of the same id. The kept entity is **extended** — never
      changed otherwise — with references to entities this same import newly
      adds: a skipped machine def gains the import's *new* recipe ids, a skipped
      tag gains its *new* member items. Something new can't have been removed by
      the user, so this never reverts an edit; without it a re-import's new
      recipes would exist with no machine able to run them.
    - ``UPDATE``: overwrite the World's existing entity **in place** with the
      incoming one (same id). Used to *refresh* canonical reference data — a
      re-import of a mod jar then propagates corrected values (a fixed fuel ``eu``,
      a changed machine def) to a project without duplicating anything or
      disturbing its nodes/pipes (those aren't part of a library). It overwrites
      any manual edits to a reference entity of that id.
    - ``ERROR``: raise on the first collision.
    """

    RENAME = "rename"
    SKIP = "skip"
    UPDATE = "update"
    ERROR = "error"


@dataclass
class ImportResult:
    """Outcome of :func:`import_library`.

    ``world`` is the merged World (the input is never mutated). ``id_remap`` maps
    each renamed entity's old id to its new one; ``skipped_ids`` lists ids dropped
    under :attr:`ConflictPolicy.SKIP`; ``updated_ids`` lists ids overwritten in
    place under :attr:`ConflictPolicy.UPDATE`; ``extended_ids`` lists skipped
    machine defs / tags that gained newly-imported recipe ids / members.
    """

    world: World
    id_remap: dict[str, str] = field(default_factory=dict)
    skipped_ids: set[str] = field(default_factory=set)
    updated_ids: set[str] = field(default_factory=set)
    extended_ids: set[str] = field(default_factory=set)


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
    updated: set[str] = set()

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
            elif on_conflict is ConflictPolicy.UPDATE:
                updated.add(old)  # overwrite the World's entity in place (same id)
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

    # Under UPDATE a colliding id is overwritten in place: drop the World's old
    # entity of that id so the refreshed incoming one (appended in new_*) is the
    # only copy. Non-colliding World entities and the whole node/pipe/system graph
    # are always kept.
    kept_items = [i for i in world.items if i.id not in updated]
    kept_recipes = [r for r in world.recipes if r.id not in updated]
    kept_defs = [d for d in world.machine_defs if d.id not in updated]

    # Under SKIP, a kept def/tag still gains what this import newly brings.
    extended: set[str] = set()
    if on_conflict is ConflictPolicy.SKIP and skipped:
        added_recipes = {r.id for r in new_recipes}
        added_items = {i.id for i in new_items}
        incoming_defs = {d.id: d for d in library.machine_defs if d.id in skipped}
        incoming_tags = {i.id: i for i in library.items if i.id in skipped and i.is_tag}
        kept_defs = [
            _extend(
                d,
                "allowed_recipe_ids",
                incoming_defs.get(d.id),
                added_recipes,
                extended,
            )
            for d in kept_defs
        ]
        kept_items = [
            _extend(i, "members", incoming_tags.get(i.id), added_items, extended)
            for i in kept_items
        ]

    merged = World(
        name=world.name,
        items=[*kept_items, *new_items],
        recipes=[*kept_recipes, *new_recipes],
        machine_defs=[*kept_defs, *new_defs],
        nodes=list(world.nodes),
        pipes=list(world.pipes),
        pipe_nodes=list(world.pipe_nodes),
        systems=list(world.systems),
    )
    return ImportResult(
        world=merged,
        id_remap={**item_remap, **recipe_remap, **def_remap},
        skipped_ids=skipped,
        updated_ids=updated,
        extended_ids=extended,
    )


def _extend(
    entity: Any, attr: str, incoming: Any, added: set[str], extended: set[str]
) -> Any:
    """``entity`` with ``incoming``'s new references (ids in ``added``) appended
    to its ``attr`` list — a copy when anything changes, else the same object."""

    if incoming is None:
        return entity
    have = list(getattr(entity, attr) or [])
    extra = [x for x in (getattr(incoming, attr) or []) if x in added and x not in have]
    if not extra:
        return entity
    clone = copy.deepcopy(entity)
    setattr(clone, attr, have + extra)
    extended.add(entity.id)
    return clone


# --------------------------------------------------------------------------- #
# A persisted catalog as an editable copy
# --------------------------------------------------------------------------- #
#
# An imported catalog (the MI jar, read once) is saved as a library file in the
# app folder. That file is *our* copy, not a cache of the source: the user's
# edits to its items/recipes/machine defs are written back into it, and it never
# overwrites the World's (possibly edited) entities when merged in.

#: Wire entity kind → the Library/World attribute that holds it.
LIBRARY_KINDS: dict[str, str] = {
    "item": "items",
    "recipe": "recipes",
    "machineDef": "machine_defs",
}


def library_ids(library: Library, kind: str) -> set[str]:
    """The ids of ``kind`` entities held by ``library``."""

    return {e.id for e in getattr(library, LIBRARY_KINDS[kind])}


def sync_library(library: Library, world: World, ids: set[str] | None = None) -> bool:
    """Copy the World's version of each entity the library holds into it.

    Only entities the library already holds are touched (a World-only entity is
    the user's own, not part of the catalog); one the World lacks is left as is.
    ``ids`` narrows the sync to those ids (an edit touches one). Returns whether
    anything changed.
    """

    changed = False
    for attr in LIBRARY_KINDS.values():
        held: list[Any] = getattr(library, attr)
        current = {e.id: e for e in getattr(world, attr)}
        for i, entity in enumerate(held):
            if ids is not None and entity.id not in ids:
                continue
            live = current.get(entity.id)
            if live is not None and live.to_dict() != entity.to_dict():
                held[i] = copy.deepcopy(live)
                changed = True
    return changed


def remove_from_library(library: Library, kind: str, entity_id: str) -> bool:
    """Drop the ``kind`` entity ``entity_id`` from the library; return if found."""

    if kind not in LIBRARY_KINDS:
        return False
    held: list[Any] = getattr(library, LIBRARY_KINDS[kind])
    kept = [e for e in held if e.id != entity_id]
    if len(kept) == len(held):
        return False
    held[:] = kept
    return True


def merge_libraries(
    base: Library, incoming: Library, *, overwrite: bool = False
) -> Library:
    """Merge ``incoming`` into ``base`` by id (a re-import into a saved copy).

    New ids are appended. A colliding id keeps ``base``'s (possibly edited)
    entity unless ``overwrite`` is set. Returns a new Library named after
    ``incoming`` (falling back to ``base``)."""

    out = Library(name=incoming.name if incoming.name is not None else base.name)
    added: dict[str, set[str]] = {}
    kept: dict[str, list[tuple[int, Any]]] = {}
    for attr in LIBRARY_KINDS.values():
        mine: list[Any] = [copy.deepcopy(e) for e in getattr(base, attr)]
        index = {e.id: i for i, e in enumerate(mine)}
        added[attr] = set()
        kept[attr] = []
        for entity in getattr(incoming, attr):
            if entity.id not in index:
                index[entity.id] = len(mine)
                mine.append(copy.deepcopy(entity))
                added[attr].add(entity.id)
            elif overwrite:
                mine[index[entity.id]] = copy.deepcopy(entity)
            else:
                kept[attr].append((index[entity.id], entity))
        setattr(out, attr, mine)
    # A kept (possibly edited) def/tag still gains the references this merge
    # newly adds — the same rule as ``import_library``'s SKIP.
    scratch: set[str] = set()
    for i, inc in kept["machine_defs"]:
        out.machine_defs[i] = _extend(
            out.machine_defs[i], "allowed_recipe_ids", inc, added["recipes"], scratch
        )
    for i, inc in kept["items"]:
        if inc.is_tag:
            out.items[i] = _extend(
                out.items[i], "members", inc, added["items"], scratch
            )
    return out


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

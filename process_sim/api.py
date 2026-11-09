"""pywebview JS API surface (WORKFLOW Phase 4) — the frozen bridge contract.

This is the single boundary between the TypeScript frontend and the headless
Python core. pywebview exposes an :class:`Api` instance so the frontend calls
each method as ``window.pywebview.api.<method>(...)``; arguments arrive as plain
JSON (dicts/lists/scalars) and return values are serialized straight back to
JSON.

**Marshaling only.** Every method converts JSON ↔ model and delegates to
:mod:`process_sim.engine` (computation) or :mod:`process_sim.io` (files). No
rate math, flow resolution, or file-format logic lives here — if that creeps in,
it belongs one layer down. The only state the API holds is *the current World*;
edits mutate it and reads/recomputes run against it.

Uniform result envelope
------------------------

Every method returns a JSON object with an ``ok`` flag so the frontend never has
to interpret a raised Python exception across the bridge:

- success: ``{"ok": true, "data": <payload>}``
- failure: ``{"ok": false, "error": {"type": "<ErrorName>", "message": "<str>"}}``

Expected failures — a malformed file, an unreadable payload, a bad request shape,
an unknown entity kind, an id collision under ``error`` policy — are mapped to the
failure envelope; they are the contract, not crashes. A payload is always itself
JSON-friendly (World dicts use the model's camelCase wire format).

Method contract (stable — a changed shape is a deliberate, documented bump; the
shapes are mirrored by the P5 TS client and recorded in ``CLAUDE.md``). Each
``data`` payload is listed; the envelope wraps it:

- ``get_world()`` → World
- ``set_world(world: World dict)`` → World (normalized)
- ``apply_edit(edit: {op, kind, entity|id})`` → World (after the edit)
- ``load_project(path)`` → World
- ``save_project(path)`` → ``{"path": str}``
- ``recompute(request: {scope, nodeId?|systemId?})`` → ScopeReport
- ``export_library(options: {name?, itemIds?, recipeIds?, machineDefIds?})``
  → Library envelope
- ``save_library(path, options)`` → ``{"path": str}``
- ``load_library(path)`` → Library envelope
- ``import_library(library, on_conflict="rename")``
  → ``{"world", "idRemap", "skippedIds", "updatedIds"}``
- ``import_library_file(path, on_conflict="rename")``
  → ``{"world", "idRemap", "skippedIds", "updatedIds"}``

``edit`` ops: ``{"op": "upsert", "kind": <kind>, "entity": {...}}`` replaces the
entity of that id in place (or appends it); ``{"op": "remove", "kind": <kind>,
"id": <id>}`` drops it. ``kind`` is one of ``item``, ``recipe``, ``machineDef``,
``node``, ``pipe``, ``pipeNode``, ``system``.

``recompute`` scopes: ``world`` (default), ``node`` (``nodeId`` — a single
machine), ``process`` (``nodeId`` — its connected component), ``system``
(``systemId``).

``on_conflict`` is a :class:`~process_sim.io.ConflictPolicy` value: ``rename``
(default), ``skip``, or ``error``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .engine import compute_scope, compute_system, compute_world, trace_process
from .io import (
    ConflictPolicy,
    ProcessSimIOError,
    export_library,
    import_library,
    import_mi_library,
    library_from_dict,
    library_to_dict,
    load_library,
    load_project,
    save_library,
    save_project,
)
from .model import (
    Item,
    MachineDef,
    MachineNode,
    Pipe,
    PipeNode,
    Recipe,
    System,
    World,
)

__all__ = ["Api"]

#: Editable World collections keyed by the wire ``kind``: attribute + factory.
#: The single place the api maps an entity kind to its home and constructor.
_KINDS: dict[str, tuple[str, Any]] = {
    "item": ("items", Item),
    "recipe": ("recipes", Recipe),
    "machineDef": ("machine_defs", MachineDef),
    "node": ("nodes", MachineNode),
    "pipe": ("pipes", Pipe),
    "pipeNode": ("pipe_nodes", PipeNode),
    "system": ("systems", System),
}

# Failures that are part of the contract rather than bugs: bad JSON payloads
# (KeyError/TypeError/ValueError from a model ``from_dict``) and typed IO errors.
_EXPECTED_ERRORS = (ProcessSimIOError, KeyError, TypeError, ValueError)


def _slug(name: str | None) -> str:
    """A filesystem-safe stem for a persisted library (e.g. its name)."""

    base = "".join(c if c.isalnum() else "_" for c in (name or "library")).strip("_")
    return base.lower() or "library"


def _ok(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data}


def _err(exc: Exception) -> dict[str, Any]:
    return {"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}}


class Api:
    """The pywebview JS API. Holds the current :class:`World`; methods marshal.

    Construct with an optional starting World (the app shell passes a freshly
    loaded project, or nothing for an empty World). The instance is long-lived:
    pywebview binds it once and routes every ``window.pywebview.api`` call to it.
    """

    def __init__(
        self,
        world: World | None = None,
        autosave_path: Path | None = None,
        library_dir: Path | None = None,
    ) -> None:
        self._world: World = world if world is not None else World()
        #: When set, the current World is written here after every change, so the
        #: app restores its last state on the next launch (persistence).
        self._autosave_path: Path | None = autosave_path
        #: A folder of persisted libraries (e.g. an imported MI catalog). Its
        #: contents are merged into the World at startup and whenever a project is
        #: loaded, so the machine catalog is always available without re-importing;
        #: ``import_mi_dir`` writes the imported library here.
        self._library_dir: Path | None = library_dir
        # Make the persisted catalog available in the starting World too.
        self._merge_persisted_libraries()

    # ------------------------------------------------------------------ #
    # Persistent library catalog (the app folder's saved libraries).
    # ------------------------------------------------------------------ #

    def _merge_persisted_libraries(self) -> None:
        """Merge every library saved under ``library_dir`` into the World.

        Keeps an imported catalog (MI machines/recipes/items) available in any
        project — a fresh World, a loaded project, or a restored autosave — so it
        never has to be re-imported. Uses ``UPDATE`` so re-running is idempotent
        and a corrected catalog refreshes stale entities in place. Unreadable
        files are skipped (a bad catalog must not stop the app launching).
        """

        if self._library_dir is None or not self._library_dir.is_dir():
            return
        for path in sorted(self._library_dir.glob("*.psim-lib.json")):
            try:
                lib = load_library(path)
                self._world = import_library(
                    self._world, lib, on_conflict=ConflictPolicy.UPDATE
                ).world
            except (OSError, *_EXPECTED_ERRORS):
                continue

    def _save_library(self, library: Any, slug: str) -> None:
        """Persist ``library`` under ``library_dir`` as ``<slug>.psim-lib.json``.

        Failures are swallowed — a disk hiccup must not break an import that has
        already merged into the live World."""

        if self._library_dir is None:
            return
        try:
            self._library_dir.mkdir(parents=True, exist_ok=True)
            save_library(library, self._library_dir / f"{slug}.psim-lib.json")
        except OSError:
            pass

    def _persist(self) -> None:
        """Write the current World to the autosave path, if one is configured.

        Failures are swallowed: a disk hiccup must not break an in-memory edit
        (the next successful change re-persists the whole World anyway).
        """

        if self._autosave_path is None:
            return
        try:
            save_project(self._world, self._autosave_path)
        except OSError:
            pass

    # ------------------------------------------------------------------ #
    # World state — read / replace / edit.
    # ------------------------------------------------------------------ #

    def get_world(self) -> dict[str, Any]:
        """Return the current World as a JSON dict."""

        return _ok(self._world.to_dict())

    def set_world(self, world: dict[str, Any]) -> dict[str, Any]:
        """Replace the current World from a JSON dict; return it normalized."""

        try:
            self._world = World.from_dict(world)
        except _EXPECTED_ERRORS as exc:
            return _err(exc)
        self._persist()
        return _ok(self._world.to_dict())

    def apply_edit(self, edit: dict[str, Any]) -> dict[str, Any]:
        """Apply one upsert/remove edit to the current World; return the result.

        Mechanical collection CRUD by ``kind`` + ``id`` — the engine recompute is
        a separate call (:meth:`recompute`), matching "edits go through the API
        and trigger recompute" without coupling the two here.
        """

        try:
            self._mutate(edit)
        except _EXPECTED_ERRORS as exc:
            return _err(exc)
        self._persist()
        return _ok(self._world.to_dict())

    def _mutate(self, edit: dict[str, Any]) -> None:
        op = edit.get("op")
        kind = edit.get("kind")
        if kind not in _KINDS:
            raise ValueError(f"unknown entity kind: {kind!r}")
        attr, factory = _KINDS[kind]
        collection: list[Any] = getattr(self._world, attr)

        if op == "upsert":
            entity = factory.from_dict(edit["entity"])
            replaced = False
            for i, existing in enumerate(collection):
                if existing.id == entity.id:
                    collection[i] = entity
                    replaced = True
                    break
            if not replaced:
                collection.append(entity)
        elif op == "remove":
            target = edit["id"]
            collection[:] = [e for e in collection if e.id != target]
        else:
            raise ValueError(f"unknown edit op: {op!r}")

    # ------------------------------------------------------------------ #
    # Project files.
    # ------------------------------------------------------------------ #

    def load_project(self, path: str) -> dict[str, Any]:
        """Load a project file, make it the current World, and return it."""

        try:
            self._world = load_project(Path(path))
        except (FileNotFoundError, *_EXPECTED_ERRORS) as exc:
            return _err(exc)
        # A loaded project may predate (or lack) the catalog; merge it so its
        # machines are placeable without a re-import.
        self._merge_persisted_libraries()
        self._persist()
        return _ok(self._world.to_dict())

    def save_project(self, path: str) -> dict[str, Any]:
        """Write the current World to ``path`` as a project file."""

        try:
            save_project(self._world, Path(path))
        except (OSError, *_EXPECTED_ERRORS) as exc:
            return _err(exc)
        return _ok({"path": str(path)})

    # ------------------------------------------------------------------ #
    # Recompute — delegate to the engine's scope aggregation.
    # ------------------------------------------------------------------ #

    def recompute(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        """Compute a scope report for the requested scope (default: world)."""

        request = request or {}
        scope = request.get("scope", "world")
        try:
            report = self._report_for(scope, request)
        except _EXPECTED_ERRORS as exc:
            return _err(exc)
        return _ok(report.to_dict())

    def _report_for(self, scope: str, request: dict[str, Any]) -> Any:
        if scope == "world":
            return compute_world(self._world)
        if scope == "node":
            return compute_scope(self._world, [request["nodeId"]])
        if scope == "process":
            ids = trace_process(self._world, request["nodeId"])
            return compute_scope(self._world, ids)
        if scope == "system":
            return compute_system(self._world, request["systemId"])
        raise ValueError(f"unknown scope: {scope!r}")

    # ------------------------------------------------------------------ #
    # Library exchange.
    # ------------------------------------------------------------------ #

    def export_library(self, options: dict[str, Any] | None = None) -> dict[str, Any]:
        """Export a library (a reusable subset) from the current World."""

        options = options or {}
        try:
            library = export_library(
                self._world,
                name=options.get("name"),
                item_ids=options.get("itemIds"),
                recipe_ids=options.get("recipeIds"),
                machine_def_ids=options.get("machineDefIds"),
            )
        except _EXPECTED_ERRORS as exc:
            return _err(exc)
        return _ok(library_to_dict(library))

    def save_library(
        self, path: str, options: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Export a library from the current World and write it to ``path``."""

        options = options or {}
        try:
            library = export_library(
                self._world,
                name=options.get("name"),
                item_ids=options.get("itemIds"),
                recipe_ids=options.get("recipeIds"),
                machine_def_ids=options.get("machineDefIds"),
            )
            save_library(library, Path(path))
        except (OSError, *_EXPECTED_ERRORS) as exc:
            return _err(exc)
        return _ok({"path": str(path)})

    def load_library(self, path: str) -> dict[str, Any]:
        """Read a library file at ``path`` and return it (does not import)."""

        try:
            library = load_library(Path(path))
        except (FileNotFoundError, *_EXPECTED_ERRORS) as exc:
            return _err(exc)
        return _ok(library_to_dict(library))

    def import_library(
        self, library: dict[str, Any], on_conflict: str = "rename"
    ) -> dict[str, Any]:
        """Merge a library envelope into the current World (collisions resolved)."""

        try:
            lib = library_from_dict(library)
            result = import_library(
                self._world, lib, on_conflict=ConflictPolicy(on_conflict)
            )
        except _EXPECTED_ERRORS as exc:
            return _err(exc)
        self._world = result.world
        self._persist()
        return _ok(self._import_payload(result))

    def import_library_file(
        self, path: str, on_conflict: str = "rename"
    ) -> dict[str, Any]:
        """Load a library file and merge it into the current World."""

        try:
            lib = load_library(Path(path))
            result = import_library(
                self._world, lib, on_conflict=ConflictPolicy(on_conflict)
            )
        except (FileNotFoundError, *_EXPECTED_ERRORS) as exc:
            return _err(exc)
        self._world = result.world
        self._persist()
        return _ok(self._import_payload(result))

    def import_mi_dir(
        self, path: str, options: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Import Modern Industrialization recipes from an extracted jar dir.

        Builds a library from the MI recipe data under ``path`` (for the chosen
        ``machineTypes``, default the phase-3 subset) and merges it into the
        current World, resolving id collisions per ``onConflict`` — which here
        defaults to ``skip`` (not the library default ``rename``) so re-importing
        the same jar is idempotent rather than duplicating every entity. The
        payload is the usual import shape plus ``stats`` describing what was read.
        """

        options = options or {}
        machine_types = options.get("machineTypes")
        try:
            imported = import_mi_library(
                path,
                # None → discover and import every machine recipe type found
                # (any mod); an explicit list narrows it to those bare type names.
                machine_types=(
                    tuple(machine_types) if machine_types is not None else None
                ),
                name=options.get("name"),
            )
            result = import_library(
                self._world,
                imported.library,
                # MI entities are canonical reference data keyed by stable ids
                # (e.g. ``modern_industrialization:iron_dust``), so re-importing
                # the same jar **refreshes** them in place (``update``): no
                # duplication, and corrected values (a fixed fuel ``eu``, a
                # changed machine def) reach a project already built on the old
                # data without touching its nodes/pipes. New recipes are added as
                # before. (The autosave persists the merged World across launches.)
                on_conflict=ConflictPolicy(options.get("onConflict", "update")),
            )
        except (FileNotFoundError, *_EXPECTED_ERRORS) as exc:
            return _err(exc)
        self._world = result.world
        # Persist the catalog to the app's library folder so it is auto-loaded on
        # every future launch / project load — no re-import needed.
        self._save_library(imported.library, _slug(imported.library.name))
        self._persist()
        payload = self._import_payload(result)
        payload["stats"] = imported.stats
        return _ok(payload)

    @staticmethod
    def _import_payload(result: Any) -> dict[str, Any]:
        return {
            "world": result.world.to_dict(),
            "idRemap": dict(result.id_remap),
            "skippedIds": sorted(result.skipped_ids),
            "updatedIds": sorted(result.updated_ids),
        }

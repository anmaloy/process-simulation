"""Import Modern Industrialization recipe data into a process-sim library.

Modern Industrialization (MI) ships its machine recipes as data JSON inside its
mod jar; this reads an **extracted** jar directory and turns those recipes (for a
chosen set of machine types) into a :class:`~process_sim.io.library.Library` — the
reusable subset of items, recipes, and machine defs — that
:func:`~process_sim.io.library.import_library` then merges into a World with the
usual id-collision handling.

What a MI recipe looks like (the shape this parser normalizes)::

    {"type": "modern_industrialization:macerator",   # the machine that runs it
     "duration": 200,                                 # ticks (20/s)
     "eu": 2,                                          # EU per tick while running
     "item_inputs":  [{"amount": 1, "item"|"tag": "...", "probability"?: n}],
     "item_outputs": [...], "fluid_inputs": [...], "fluid_outputs": [...]}

Each of the four I/O keys may be a **single object or a list**; ``amount``
defaults to 1; an entry names an ``item``, a ``fluid``, or a ``tag`` (a group
reference, imported as an ``is_tag`` item); ids may be namespaced
(``modern_industrialization:iron_dust``) or bare (``water`` → ``minecraft:water``).

Units follow the engine's conventions: ``duration`` ticks → ``time_seconds``
(÷20), ``eu`` is kept **per tick** on ``Recipe.eu_per_tick`` (the engine scales
×20), item amounts are counts, fluid amounts are mB — both stored as the stack
quantity, matching the per-category display units.

Machine defs are grouped by recipe ``type`` and modelled as electric consumers
(``energy_source = electric``, scalar mains draw — a carrier can be wired later);
their slot layout is derived from the recipes (the max item/fluid in/out counts
seen). The MI **power-tier ceiling** (``voltage_tier`` / ``max_eu_per_tick``,
which clamps overclock) is left unset here — tier EU caps are authored in the
machine catalog (phase 5), verified against the jar — so imported machines simply
don't clamp yet.

This module is file-format logic (it belongs in :mod:`process_sim.io`, not the
thin API layer); it builds a Library and never touches an existing World.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..model import (
    Category,
    Direction,
    EnergySource,
    Item,
    MachineDef,
    Recipe,
    SlotGroup,
    Stack,
)
from .errors import MalformedFileError
from .library import Library

__all__ = [
    "DEFAULT_MACHINE_TYPES",
    "MiImport",
    "import_mi_library",
]

#: The machine types imported by default (the phase-3 targeted subset). The
#: parser is generic — pass any MI recipe ``type`` names to widen the import.
DEFAULT_MACHINE_TYPES: tuple[str, ...] = (
    "macerator",
    "mixer",
    "assembler",
    "chemical_reactor",
)

_MI_NS = "modern_industrialization"
#: Default stack sizes per category for a derived slot (a solid stack; a tank).
_SOLID_STACK = 64
_FLUID_STACK = 16000


@dataclass
class MiImport:
    """A built MI :class:`Library` plus counts describing what was imported."""

    library: Library
    stats: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Directory / support-file resolution.
# --------------------------------------------------------------------------- #


def _resolve_recipe_dir(root: Path) -> Path:
    """Find the MI recipe directory under ``root`` (an extracted jar, or it)."""

    candidates = [
        root / "data" / _MI_NS / "recipe",
        root / _MI_NS / "recipe",
        root,  # root already points at the recipe dir
    ]
    for c in candidates:
        if c.is_dir():
            return c
    raise MalformedFileError(
        f"no MI recipe directory found under {root} (expected data/{_MI_NS}/recipe)"
    )


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _load_lang(root: Path) -> dict[str, str]:
    """Load the English lang map (display names), or empty if absent."""

    for c in [
        root / "assets" / _MI_NS / "lang" / "en_us.json",
        root / _MI_NS / "lang" / "en_us.json",
    ]:
        if c.is_file():
            data = _load_json(c)
            if isinstance(data, dict):
                return {k: v for k, v in data.items() if isinstance(v, str)}
    return {}


def _load_fluid_fuels(root: Path) -> dict[str, float]:
    """Map fluid id → ``eu_per_mb`` from the fluid-fuels data map (fuel content)."""

    for c in [
        root / "data" / _MI_NS / "data_maps" / "fluid" / "fluid_fuels.json",
        root / _MI_NS / "data_maps" / "fluid" / "fluid_fuels.json",
    ]:
        if c.is_file():
            data = _load_json(c)
            values = data.get("values") if isinstance(data, dict) else None
            if isinstance(values, dict):
                out: dict[str, float] = {}
                for fid, spec in values.items():
                    if isinstance(spec, dict) and "eu_per_mb" in spec:
                        out[_resolve_id(fid, "minecraft")] = float(spec["eu_per_mb"])
                return out
    return {}


# --------------------------------------------------------------------------- #
# Id / name helpers.
# --------------------------------------------------------------------------- #


def _resolve_id(raw: str, default_ns: str) -> str:
    """Namespace a bare id; a leading ``#`` (tag) is stripped by the caller."""

    return raw if ":" in raw else f"{default_ns}:{raw}"


def _prettify(path: str) -> str:
    """Turn an id path into a human label (``plutonium_dust`` → ``Plutonium Dust``)."""

    words = path.replace("/", " ").replace("_", " ").split()
    return " ".join(w[:1].upper() + w[1:] for w in words) if words else path


def _split(full_id: str) -> tuple[str, str]:
    ns, _, path = full_id.partition(":")
    return ns, path


def _item_name(full_id: str, lang: dict[str, str]) -> str:
    ns, path = _split(full_id)
    return (
        lang.get(f"item.{ns}.{path}")
        or lang.get(f"block.{ns}.{path}")
        or _prettify(path)
    )


def _fluid_name(full_id: str, lang: dict[str, str]) -> str:
    ns, path = _split(full_id)
    bucket = lang.get(f"item.{ns}.{path}_bucket")
    if bucket:
        return bucket[: -len(" Bucket")] if bucket.endswith(" Bucket") else bucket
    return lang.get(f"fluid.{ns}.{path}") or _prettify(path)


def _machine_name(machine_type: str, lang: dict[str, str]) -> str:
    return lang.get(f"block.{_MI_NS}.{machine_type}") or _prettify(machine_type)


# --------------------------------------------------------------------------- #
# Recipe-entry normalization.
# --------------------------------------------------------------------------- #


def _entries(recipe: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """Normalize an I/O key that may be absent, a single object, or a list."""

    v = recipe.get(key)
    if v is None:
        return []
    if isinstance(v, dict):
        return [v]
    if isinstance(v, list):
        return [e for e in v if isinstance(e, dict)]
    return []


class _ItemRegistry:
    """Accumulates the unique items/fluids/tags referenced, as model Items."""

    def __init__(self, lang: dict[str, str], fluid_fuels: dict[str, float]) -> None:
        self._lang = lang
        self._fuels = fluid_fuels
        self.items: dict[str, Item] = {}

    def ref(self, entry: dict[str, Any], category: Category) -> str | None:
        """Register the entry's resource, returning its flow id (or ``None``)."""

        if "tag" in entry:
            raw = str(entry["tag"]).lstrip("#")
            tag_id = _resolve_id(raw, "c")
            if tag_id not in self.items:
                self.items[tag_id] = Item(
                    id=tag_id,
                    name=_prettify(_split(tag_id)[1]),
                    category=category,
                    is_tag=True,
                )
            return tag_id
        key = "fluid" if category is Category.LIQUID else "item"
        raw = entry.get(key)
        if raw is None:
            return None
        full = _resolve_id(str(raw), "minecraft")
        if full not in self.items:
            if category is Category.LIQUID:
                name = _fluid_name(full, self._lang)
                eu = self._fuels.get(full)
            else:
                name = _item_name(full, self._lang)
                eu = None
            self.items[full] = Item(id=full, name=name, category=category, eu=eu)
        return full


def _stack(entry: dict[str, Any], resource_id: str, is_tag: bool) -> Stack:
    qty = float(entry.get("amount", 1))
    prob = entry.get("probability")
    s = Stack(quantity=qty, probability=float(prob) if prob is not None else None)
    if is_tag:
        s.tag = resource_id
    else:
        s.item_id = resource_id
    return s


# --------------------------------------------------------------------------- #
# Main import.
# --------------------------------------------------------------------------- #


def import_mi_library(
    root: str | Path,
    *,
    machine_types: tuple[str, ...] = DEFAULT_MACHINE_TYPES,
    name: str | None = None,
) -> MiImport:
    """Build a :class:`Library` from the MI recipes under ``root``.

    ``root`` is an extracted MI jar directory (or the recipe directory itself).
    Only recipes whose ``type`` is one of ``machine_types`` are imported; every
    other file (vanilla crafting recipes, other machines) is skipped. The result
    is never merged here — hand its ``library`` to
    :func:`~process_sim.io.library.import_library`.
    """

    root = Path(root)
    recipe_dir = _resolve_recipe_dir(root)
    lang = _load_lang(root)
    fuels = _load_fluid_fuels(root)
    wanted = {f"{_MI_NS}:{t}" for t in machine_types}

    registry = _ItemRegistry(lang, fuels)
    recipes: list[Recipe] = []
    # Per type: collected recipe ids + max I/O counts (for the slot layout).
    by_type: dict[str, dict[str, Any]] = {
        t: {"recipe_ids": [], "sin": 0, "sout": 0, "lin": 0, "lout": 0}
        for t in machine_types
    }
    files_scanned = 0
    recipes_skipped = 0

    for path in sorted(recipe_dir.rglob("*.json")):
        files_scanned += 1
        data = _load_json(path)
        if not isinstance(data, dict):
            recipes_skipped += 1
            continue
        rtype = data.get("type")
        if rtype not in wanted:
            recipes_skipped += 1
            continue
        machine_type = str(rtype).split(":", 1)[1]

        rel = path.relative_to(recipe_dir).with_suffix("").as_posix()
        recipe_id = f"mi:{rel}"

        item_in = _entries(data, "item_inputs")
        item_out = _entries(data, "item_outputs")
        fluid_in = _entries(data, "fluid_inputs")
        fluid_out = _entries(data, "fluid_outputs")

        inputs: list[Stack] = []
        outputs: list[Stack] = []
        for e in item_in:
            rid = registry.ref(e, Category.SOLID)
            if rid is not None:
                inputs.append(_stack(e, rid, "tag" in e))
        for e in fluid_in:
            rid = registry.ref(e, Category.LIQUID)
            if rid is not None:
                inputs.append(_stack(e, rid, "tag" in e))
        for e in item_out:
            rid = registry.ref(e, Category.SOLID)
            if rid is not None:
                outputs.append(_stack(e, rid, "tag" in e))
        for e in fluid_out:
            rid = registry.ref(e, Category.LIQUID)
            if rid is not None:
                outputs.append(_stack(e, rid, "tag" in e))

        duration = data.get("duration")
        eu = data.get("eu")
        time_seconds = float(duration) / 20.0 if duration else 0.0

        recipes.append(
            Recipe(
                id=recipe_id,
                time_seconds=time_seconds,
                name=_recipe_name(machine_type, outputs, registry, lang),
                inputs=inputs,
                outputs=outputs,
                eu_per_tick=float(eu) if eu is not None else None,
                machine_type=machine_type,
            )
        )

        agg = by_type[machine_type]
        agg["recipe_ids"].append(recipe_id)
        agg["sin"] = max(agg["sin"], len(item_in))
        agg["sout"] = max(agg["sout"], len(item_out))
        agg["lin"] = max(agg["lin"], len(fluid_in))
        agg["lout"] = max(agg["lout"], len(fluid_out))

    machine_defs = [
        _machine_def(t, agg, lang) for t, agg in by_type.items() if agg["recipe_ids"]
    ]

    library = Library(
        name=name or "Modern Industrialization",
        items=sorted(registry.items.values(), key=lambda i: i.id),
        recipes=recipes,
        machine_defs=machine_defs,
    )
    stats = {
        "filesScanned": files_scanned,
        "recipesImported": len(recipes),
        "recipesSkipped": recipes_skipped,
        "items": sum(1 for i in registry.items.values() if not i.is_tag),
        "tags": sum(1 for i in registry.items.values() if i.is_tag),
        "machineDefs": len(machine_defs),
        "byType": {t: len(agg["recipe_ids"]) for t, agg in by_type.items()},
    }
    return MiImport(library=library, stats=stats)


def _recipe_name(
    machine_type: str,
    outputs: list[Stack],
    registry: _ItemRegistry,
    lang: dict[str, str],
) -> str:
    """A readable label: ``<Machine>: <primary output>`` (else just the machine)."""

    primary = next((s.resource_id for s in outputs if s.resource_id), None)
    machine = _machine_name(machine_type, lang)
    if primary is None:
        return machine
    out_item = registry.items.get(primary)
    label = out_item.name if out_item is not None else _prettify(_split(primary)[1])
    return f"{machine}: {label}"


def _machine_def(
    machine_type: str, agg: dict[str, Any], lang: dict[str, str]
) -> MachineDef:
    """An electric-consumer def for ``machine_type`` with a data-derived layout."""

    slots: list[SlotGroup] = []

    def add(category: Category, direction: Direction, count: int, stack: int) -> None:
        if count > 0:
            slots.append(SlotGroup(category, direction, count, stack))

    add(Category.SOLID, Direction.IN, agg["sin"], _SOLID_STACK)
    add(Category.SOLID, Direction.OUT, agg["sout"], _SOLID_STACK)
    add(Category.LIQUID, Direction.IN, agg["lin"], _FLUID_STACK)
    add(Category.LIQUID, Direction.OUT, agg["lout"], _FLUID_STACK)

    return MachineDef(
        id=f"mi.machine.{machine_type}",
        name=_machine_name(machine_type, lang),
        energy_source=EnergySource.ELECTRIC,
        allowed_recipe_ids=list(agg["recipe_ids"]),
        slots=slots,
    )

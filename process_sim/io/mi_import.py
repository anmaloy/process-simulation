"""Import Modern Industrialization recipe data into a process-sim library.

Modern Industrialization (MI) ships its machine recipes as data JSON inside its
mod jar; this reads an **extracted** jar directory and turns those recipes into a
:class:`~process_sim.io.library.Library` — the reusable subset of items, recipes,
and machine defs — that :func:`~process_sim.io.library.import_library` then merges
into a World with the usual id-collision handling. By default **every** MI machine
recipe type is imported (:data:`DEFAULT_MACHINE_TYPES`); a caller may pass a
narrower tuple. Energy *generation* (boilers, steam turbines, diesel generators)
is not recipe-driven in MI and is added separately as converter defs.

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
The **forge hammer** is the one targeted type using a vanilla-style single
``ingredient`` → ``result`` shape (``count``/``id`` instead of the arrays); it is
a manual tool with no ``eu``/``duration``, so it imports its I/O with a zero cycle
time (no automatic rate).

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
    "CABLE_TIER_EU",
    "MACHINE_CATALOG",
    "MachineSpec",
    "ENERGY_MACHINES",
    "EnergyMachine",
    "ELECTRICITY_ID",
    "STEAM_ID",
    "MiImport",
    "import_mi_library",
]

#: Every Modern Industrialization machine recipe ``type`` (the ``modern_
#: industrialization:*`` recipes in the jar). Importing all of them is the
#: default — a *full* import. Vanilla crafting/smelting types are not MI machines
#: and are always skipped. The parser is generic, so a caller may still pass a
#: narrower tuple. (Energy generation — boilers, steam turbines, diesel
#: generators — is **not** recipe-driven in MI; those are added as converter
#: defs from :data:`ENERGY_MACHINES`, not parsed here.)
DEFAULT_MACHINE_TYPES: tuple[str, ...] = (
    # Single-block electric crafting machines (LV tier — see MACHINE_CATALOG).
    "macerator",
    "mixer",
    "assembler",
    "packer",
    "unpacker",
    "chemical_reactor",
    "cutting_machine",
    "compressor",
    "distillery",
    "centrifuge",
    "wiremill",
    "electrolyzer",
    "polarizer",
    # Manual tool recipes (no powered machine): forge hammer.
    "forge_hammer",
    # Multiblock machines (no fixed EU ceiling — scaled by energy hatches).
    "blast_furnace",
    "heat_exchanger",
    "vacuum_freezer",
    "implosion_compressor",
    "pressurizer",
    "quarry",
    "distillation_tower",
    "fusion_reactor",
    "oil_drilling_rig",
    "coke_oven",
)

#: Modern Industrialization voltage-tier EU/tick, read verbatim from the jar's
#: ``CableTier`` static initializer (``api/energy/CableTier.class``) — not
#: guessed. The ``eu`` is the per-tick power a tier's machine/cable carries,
#: i.e. the overclock ceiling a machine of that tier draws at.
CABLE_TIER_EU: dict[str, float] = {
    "LV": 32,  # Low Voltage
    "MV": 128,  # Medium Voltage
    "HV": 1024,  # High Voltage
    "EV": 8192,  # Extreme Voltage
    "SV": 128_000_000,  # Superconductor
}


@dataclass(frozen=True)
class MachineSpec:
    """Hand-authored metadata the recipe data doesn't carry, keyed by type.

    A MI machine's tier isn't in its recipes (the same ``macerator`` recipe runs
    on the bronze, steel, or electric macerator), so the base **electric** tier
    is authored here: ``voltage_tier`` labels it and ``max_eu_per_tick`` is that
    tier's EU/tick (:data:`CABLE_TIER_EU`), which clamps overclock in the engine.
    """

    voltage_tier: str | None = None
    max_eu_per_tick: float | None = None


#: Per-type machine metadata. MI's basic electric processing machines are
#: single-block Low-Voltage blocks (upgradable), so each gets the LV ceiling
#: (32 EU/t, the ``electric`` variant registered in the jar's
#: ``SingleBlockCraftingMachines``); a node can be retiered in the inspector. A
#: type absent here imports with no ceiling (overclock unclamped) — used for the
#: multiblocks (which scale with their energy hatches, not a fixed tier) and the
#: manual forge hammer.
_LV = MachineSpec("LV", CABLE_TIER_EU["LV"])
MACHINE_CATALOG: dict[str, MachineSpec] = {
    "macerator": _LV,
    "mixer": _LV,
    "assembler": _LV,
    "packer": _LV,
    "unpacker": _LV,
    "chemical_reactor": _LV,
    "cutting_machine": _LV,
    "compressor": _LV,
    "distillery": _LV,
    "centrifuge": _LV,
    "wiremill": _LV,
    "electrolyzer": _LV,
    "polarizer": _LV,
}

_MI_NS = "modern_industrialization"
#: Default stack sizes per category for a derived slot (a solid stack; a tank).
_SOLID_STACK = 64
_FLUID_STACK = 16000
# Energy slots are ports only; the engine ignores stack size, so a nominal 1.
_ENERGY_STACK = 1

#: Ticks per second. MI energy/fluid machine rates are authored **per tick**; the
#: engine is canonical **per second**, so a rated EU/t or mB/t becomes ``× 20``.
_TICKS_PER_SEC = 20.0

# --------------------------------------------------------------------------- #
# Energy generation (boilers / turbines / generators).
#
# MI's power machines are NOT recipe-driven — their rates live in the jar's Java
# (``SingleBlockSpecialMachines`` / ``MultiblockMachines`` / ``SteamHeaterComponent``),
# read verbatim below. The planner models each as a **converter** (see
# ``engine/energy.py``): a boiler burns fuel to turn water into steam; a turbine
# or generator spends its source carrier to make **electricity**. Electricity is
# a routed energy-category carrier (resolved decision #3), so generators emit it
# as an item over energy pipes and the imported consumers draw it (see
# ``_machine_def``). All rates are MI's per-tick figures, stored ``× 20`` for the
# per-second engine.
# --------------------------------------------------------------------------- #

#: Canonical electricity carrier. MI has no "electricity item" (EU is a scalar
#: in-game); the planner routes it as an energy-category item, defined here.
ELECTRICITY_ID = "mi:electricity"
STEAM_ID = f"{_MI_NS}:steam"
WATER_ID = "minecraft:water"
PLASMA_ID = f"{_MI_NS}:helium_plasma"
DIESEL_ID = f"{_MI_NS}:diesel"

#: steamPerWater, MIServerConfig default (verbatim): 1 mB water → 16 mB steam.
STEAM_PER_WATER = 16.0
#: Steam energy density: 1 mB steam = 1 EU (turbine ``EUProductionMap`` for STEAM).
_EU_PER_MB_STEAM = 1.0
#: Helium-plasma energy density (plasma turbine ``ofSingleFluid`` arg): 100000 EU/mB.
_EU_PER_MB_PLASMA = 100_000.0
#: The multiblock steam boilers idle hot and lose unused surplus heat — the
#: SteamHeater waste fraction. The single-block bronze/steel boilers do **not**:
#: they are 100% efficient and simply stop burning fuel when their steam buffer
#: is full (modelled as no waste, ``w = 0`` → fuel drain follows actual draw).
_BOILER_WASTE = 0.8
_SINGLE_BLOCK_BOILER_WASTE = None


@dataclass(frozen=True)
class EnergyMachine:
    """A hand-authored energy-generation converter (rates verbatim from the jar).

    ``eu_per_tick`` is the machine's rated EU/t (steam mB/t for a boiler, since
    1 mB = 1 EU); it is stored ``× 20`` as the per-second ``max_fluid_rate``.
    ``source_item_id`` is the carrier consumed (a fuel by its ``eu``, or steam
    1:1); a boiler leaves it unset so the player picks which fuel to burn, while
    its steam output and water draw are fixed. ``input_item_id`` /
    ``material_ratio`` are the material a boiler converts (water, 16:1).
    """

    key: str
    name: str
    energy_source: EnergySource
    eu_per_tick: float
    output_item_id: str
    source_item_id: str | None = None
    input_item_id: str | None = None
    material_ratio: float | None = None
    waste: float | None = None


#: Every MI energy-generation machine, with its rated EU/t read from the 2.5.11
#: jar. Boilers → steam (fuel chosen by the player); steam turbines → electricity
#: (steam 1:1); diesel generators → electricity (fuel burned by its ``eu``); the
#: plasma turbine burns helium plasma. Diesel/plasma "generator efficiency"
#: multipliers in MI are not modelled — the planner is energy-conserving (1 EU of
#: fuel → 1 EU out), so a generator's fuel draw is its output ÷ the fuel's EU/mB.
ENERGY_MACHINES: tuple[EnergyMachine, ...] = (
    # Boilers: fuel → steam, consuming water at 16 steam : 1 water (w = 0.8).
    EnergyMachine(
        "bronze_boiler",
        "Bronze Boiler",
        EnergySource.FUEL,
        8,
        STEAM_ID,
        None,
        WATER_ID,
        STEAM_PER_WATER,
        _SINGLE_BLOCK_BOILER_WASTE,
    ),
    EnergyMachine(
        "steel_boiler",
        "Steel Boiler",
        EnergySource.FUEL,
        16,
        STEAM_ID,
        None,
        WATER_ID,
        STEAM_PER_WATER,
        _SINGLE_BLOCK_BOILER_WASTE,
    ),
    EnergyMachine(
        "large_steam_boiler",
        "Large Steam Boiler",
        EnergySource.FUEL,
        256,
        STEAM_ID,
        None,
        WATER_ID,
        STEAM_PER_WATER,
        _BOILER_WASTE,
    ),
    EnergyMachine(
        "advanced_large_steam_boiler",
        "Advanced Large Steam Boiler",
        EnergySource.FUEL,
        1024,
        STEAM_ID,
        None,
        WATER_ID,
        STEAM_PER_WATER,
        _BOILER_WASTE,
    ),
    EnergyMachine(
        "high_pressure_large_steam_boiler",
        "High Pressure Large Steam Boiler",
        EnergySource.FUEL,
        2048,
        STEAM_ID,
        None,
        WATER_ID,
        STEAM_PER_WATER,
        _BOILER_WASTE,
    ),
    EnergyMachine(
        "high_pressure_advanced_large_steam_boiler",
        "High Pressure Advanced Large Steam Boiler",
        EnergySource.FUEL,
        8192,
        STEAM_ID,
        None,
        WATER_ID,
        STEAM_PER_WATER,
        _BOILER_WASTE,
    ),
    # Steam turbines: steam → electricity (LV/MV/HV = 32/128/512; large = 16384).
    EnergyMachine(
        "lv_steam_turbine",
        "Steam Turbine",
        EnergySource.LIQUID,
        32,
        ELECTRICITY_ID,
        STEAM_ID,
    ),
    EnergyMachine(
        "mv_steam_turbine",
        "Advanced Steam Turbine",
        EnergySource.LIQUID,
        128,
        ELECTRICITY_ID,
        STEAM_ID,
    ),
    EnergyMachine(
        "hv_steam_turbine",
        "HV Steam Turbine",
        EnergySource.LIQUID,
        512,
        ELECTRICITY_ID,
        STEAM_ID,
    ),
    EnergyMachine(
        "large_steam_turbine",
        "Large Steam Turbine",
        EnergySource.LIQUID,
        16384,
        ELECTRICITY_ID,
        STEAM_ID,
    ),
    # Plasma turbine: helium plasma → electricity (plasma = 100000 EU/mB).
    EnergyMachine(
        "plasma_turbine",
        "Plasma Turbine",
        EnergySource.FUEL,
        1048576,
        ELECTRICITY_ID,
        PLASMA_ID,
    ),
    # Diesel generators: fluid fuel → electricity (LV/MV/HV = 64/256/1024;
    # large = 16384). Diesel is the default fuel; swap it in the inspector.
    EnergyMachine(
        "lv_diesel_generator",
        "LV Diesel Generator",
        EnergySource.FUEL,
        64,
        ELECTRICITY_ID,
        DIESEL_ID,
    ),
    EnergyMachine(
        "mv_diesel_generator",
        "MV Diesel Generator",
        EnergySource.FUEL,
        256,
        ELECTRICITY_ID,
        DIESEL_ID,
    ),
    EnergyMachine(
        "hv_diesel_generator",
        "HV Diesel Generator",
        EnergySource.FUEL,
        1024,
        ELECTRICITY_ID,
        DIESEL_ID,
    ),
    EnergyMachine(
        "large_diesel_generator",
        "Large Diesel Generator",
        EnergySource.FUEL,
        16384,
        ELECTRICITY_ID,
        DIESEL_ID,
    ),
)


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


def _normalize_count(entry: dict[str, Any]) -> dict[str, Any]:
    """Map a vanilla-style ``count`` onto the ``amount`` key the parser reads."""

    if "count" in entry and "amount" not in entry:
        entry = {**entry, "amount": entry["count"]}
    return entry


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
    include_energy: bool = True,
) -> MiImport:
    """Build a :class:`Library` from the MI recipes under ``root``.

    ``root`` is an extracted MI jar directory (or the recipe directory itself).
    Only recipes whose ``type`` is one of ``machine_types`` are imported; every
    other file (vanilla crafting recipes, other machines) is skipped. The result
    is never merged here — hand its ``library`` to
    :func:`~process_sim.io.library.import_library`.

    ``include_energy`` (default on) adds the energy-generation machines
    (:data:`ENERGY_MACHINES`: boilers, steam turbines, diesel/plasma generators)
    and the carriers they route — these are not recipe-driven in MI, so they ride
    alongside the parsed recipes rather than being parsed from files.
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
        # The forge hammer uses a vanilla-style single ``ingredient`` → ``result``
        # shape instead of the ``*_inputs``/``*_outputs`` arrays. Normalize it to
        # the item-entry shape the loops below expect (``count`` → ``amount``,
        # ``result.id`` → ``item``).
        ingredient = data.get("ingredient")
        if isinstance(ingredient, dict):
            item_in = [*item_in, _normalize_count(ingredient)]
        result = data.get("result")
        if isinstance(result, dict) and result.get("id") is not None:
            item_out = [
                *item_out,
                {"item": result["id"], "amount": result.get("count", 1)},
            ]

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

    # Energy generation (not recipe-driven in MI): add the converter defs and the
    # carriers they reference (electricity, steam, water, plasma). Done before tag
    # membership so a newly-added carrier still gets any tag wiring.
    energy_defs: list[MachineDef] = []
    if include_energy:
        _ensure_carrier_items(registry)
        energy_defs = [_energy_machine_def(m) for m in ENERGY_MACHINES]

    # Resolve tag membership from the game's tag definitions, so a concrete item
    # can later stand in for a recipe that references a tag (e.g. iron_ore → a
    # recipe keyed on c:ores/iron). Also register any member item not already
    # imported, so it is a named, selectable resource.
    member_items_added = _resolve_tag_membership(root, registry, lang)

    library = Library(
        name=name or "Modern Industrialization",
        items=sorted(registry.items.values(), key=lambda i: i.id),
        recipes=recipes,
        machine_defs=machine_defs + energy_defs,
    )
    stats = {
        "filesScanned": files_scanned,
        "recipesImported": len(recipes),
        "recipesSkipped": recipes_skipped,
        "items": sum(1 for i in registry.items.values() if not i.is_tag),
        "tags": sum(1 for i in registry.items.values() if i.is_tag),
        "memberItemsAdded": member_items_added,
        "machineDefs": len(machine_defs),
        "energyMachines": len(energy_defs),
        "byType": {t: len(agg["recipe_ids"]) for t, agg in by_type.items()},
    }
    return MiImport(library=library, stats=stats)


def _tag_file(root: Path, tag_id: str) -> Path:
    """Path to a tag's definition: ``<ns>:<path>`` → data/<ns>/tags/item/<path>.json."""

    ns, _, path = tag_id.partition(":")
    return root / "data" / ns / "tags" / "item" / f"{path}.json"


def _tag_members(root: Path, tag_id: str, seen: set[str]) -> set[str]:
    """Flatten a tag's member item ids, recursing into nested ``#tag`` refs."""

    if tag_id in seen:
        return set()
    seen.add(tag_id)
    data = _load_json(_tag_file(root, tag_id))
    values = data.get("values") if isinstance(data, dict) else data
    if not isinstance(values, list):
        return set()
    out: set[str] = set()
    for v in values:
        if isinstance(v, dict):
            v = v.get("id") or v.get("tag")
        if not isinstance(v, str):
            continue
        if v.startswith("#"):
            out |= _tag_members(root, v[1:], seen)
        else:
            out.add(_resolve_id(v, "minecraft"))
    return out


def _resolve_tag_membership(
    root: Path, registry: _ItemRegistry, lang: dict[str, str]
) -> int:
    """Populate each tag item's ``members`` and register any missing member item.

    Returns the count of member items newly created. Member items inherit the
    tag's category (MI item tags are solids; a fluid tag's members are liquids).
    """

    added = 0
    tag_items = [i for i in registry.items.values() if i.is_tag]
    for tag in tag_items:
        members = sorted(_tag_members(root, tag.id, set()))
        if not members:
            continue
        tag.members = members
        for mid in members:
            if mid in registry.items:
                continue
            if tag.category is Category.LIQUID:
                name = _fluid_name(mid, lang)
            else:
                name = _item_name(mid, lang)
            registry.items[mid] = Item(id=mid, name=name, category=tag.category)
            added += 1
    return added


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
    """An electric-consumer def for ``machine_type`` with a data-derived layout.

    Electricity is a routed carrier (resolved decision #3): the consumer draws it
    as the :data:`ELECTRICITY_ID` item through an energy **in** port, so a
    generator must feed it over an energy pipe (an unwired machine is unpowered).
    """

    slots: list[SlotGroup] = []

    def add(category: Category, direction: Direction, count: int, stack: int) -> None:
        if count > 0:
            slots.append(SlotGroup(category, direction, count, stack))

    add(Category.SOLID, Direction.IN, agg["sin"], _SOLID_STACK)
    add(Category.SOLID, Direction.OUT, agg["sout"], _SOLID_STACK)
    add(Category.LIQUID, Direction.IN, agg["lin"], _FLUID_STACK)
    add(Category.LIQUID, Direction.OUT, agg["lout"], _FLUID_STACK)
    add(Category.ENERGY, Direction.IN, 1, _ENERGY_STACK)  # routed electricity in

    spec = MACHINE_CATALOG.get(machine_type, MachineSpec())
    return MachineDef(
        id=f"mi.machine.{machine_type}",
        name=_machine_name(machine_type, lang),
        energy_source=EnergySource.ELECTRIC,
        source_item_id=ELECTRICITY_ID,
        allowed_recipe_ids=list(agg["recipe_ids"]),
        slots=slots,
        voltage_tier=spec.voltage_tier,
        max_eu_per_tick=spec.max_eu_per_tick,
    )


def _energy_machine_def(m: EnergyMachine) -> MachineDef:
    """Build a converter :class:`MachineDef` for one energy-generation machine.

    The rated EU/t (``eu_per_tick``) becomes the per-second ``max_fluid_rate``
    (``× 20``). Ports: a boiler takes fuel (solid + liquid in) and water (liquid
    in) and emits steam (liquid out); a turbine/generator takes its source fluid
    (liquid in) and emits electricity (energy out).
    """

    slots: list[SlotGroup] = []
    out_cat = Category.ENERGY if m.output_item_id == ELECTRICITY_ID else Category.LIQUID
    if out_cat is Category.ENERGY:
        # Turbine / generator: one liquid-fuel in, electricity out.
        slots.append(SlotGroup(Category.LIQUID, Direction.IN, 1, _FLUID_STACK))
        slots.append(SlotGroup(Category.ENERGY, Direction.OUT, 1, _ENERGY_STACK))
    else:
        # Boiler: solid + liquid fuel in, water in, steam out.
        slots.append(SlotGroup(Category.SOLID, Direction.IN, 1, _SOLID_STACK))
        slots.append(SlotGroup(Category.LIQUID, Direction.IN, 2, _FLUID_STACK))
        slots.append(SlotGroup(Category.LIQUID, Direction.OUT, 1, _FLUID_STACK))

    return MachineDef(
        id=f"mi.energy.{m.key}",
        name=m.name,
        energy_source=m.energy_source,
        source_item_id=m.source_item_id,
        output_item_id=m.output_item_id,
        input_item_id=m.input_item_id,
        material_ratio=m.material_ratio,
        efficiency=m.waste,
        max_fluid_rate=m.eu_per_tick * _TICKS_PER_SEC,
        slots=slots,
    )


def _ensure_carrier_items(registry: _ItemRegistry) -> None:
    """Register the energy carriers the generated defs reference, if absent.

    Electricity is always added (the planner's grid carrier). Steam, water,
    diesel and helium plasma may already be imported as MI fluids; only a missing
    one is created, and a fuel's ``eu`` (plasma's energy density) is filled in.
    """

    def ensure(item_id: str, name: str, category: Category, eu: float | None) -> None:
        existing = registry.items.get(item_id)
        if existing is None:
            registry.items[item_id] = Item(
                id=item_id, name=name, category=category, eu=eu
            )
        elif eu is not None and existing.eu is None:
            existing.eu = eu

    ensure(ELECTRICITY_ID, "Electricity", Category.ENERGY, None)
    ensure(STEAM_ID, "Steam", Category.LIQUID, None)
    ensure(WATER_ID, "Water", Category.LIQUID, None)
    ensure(PLASMA_ID, "Helium Plasma", Category.LIQUID, _EU_PER_MB_PLASMA)

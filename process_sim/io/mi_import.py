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
    StorageSpec,
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
    "STEAM_MACHINE_TIERS",
    "STEAM_TIER_MAX_EU",
    "MachineVariant",
    "MACHINE_VARIANTS",
    "PUMP_MACHINES",
    "PumpMachine",
    "STORAGE_MACHINES",
    "StorageMachine",
    "SPECIAL_MACHINES",
    "SpecialMachine",
    "ELECTRICITY_ID",
    "STEAM_ID",
    "MiImport",
    "import_mi_library",
]

#: The Modern Industrialization machine recipe ``type`` names (bare, without the
#: ``modern_industrialization:`` prefix), kept for reference and as a ready-made
#: subset a caller can pass as ``machine_types``. It is **no longer the import
#: gate**: by default :func:`import_mi_library` discovers *every* namespaced
#: machine recipe type it finds (see its ``machine_types`` docs), so other mods
#: with the same recipe structure import with no edit here. Energy generation
#: (boilers, turbines, generators) is not recipe-driven and is added from
#: :data:`ENERGY_MACHINES`, not parsed.
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

#: EU per furnace **burn tick**. A solid fuel's ``burn_time`` (furnace ticks) is
#: NOT its EU directly: MI values each burn tick at 20 EU, so a fuel's stored
#: energy is ``burn_time × 20`` (coal's 1600 burn ticks = 32 000 EU). This mirrors
#: the per-tick → per-second ``× 20`` everywhere else; fluid fuels already carry
#: ``eu_per_mb`` as EU directly, so only solid burn-times take this factor.
_EU_PER_BURN_TICK = 20.0

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


# --------------------------------------------------------------------------- #
# Steam machine tiers (bronze / steel) for the single-block crafting machines.
#
# The same recipe runs on the bronze, steel, and electric variant of a machine;
# the importer builds the **electric** def from the recipes (``_machine_def``),
# and these add the lower **steam** tiers for the machines that have them. The
# per-tier EU/tick ceiling is the jar's ``MachineTier`` enum (BRONZE max 2, STEEL
# max 4); the tier roster is ``SingleBlockCraftingMachines.init`` (its tier
# bitmask: Bronze 1, Steel 2, Electric 4). A steam machine draws its recipe's
# ``eu_per_tick`` as **steam** (1 mB = 1 EU), so ``energy_source = liquid`` with
# ``source_item_id = steam``; the ceiling clamps overclock exactly as the tier
# gates speed in-game.
# --------------------------------------------------------------------------- #

#: MachineTier EU/tick ceiling per steam tier (jar: ``MachineTier`` enum maxEu).
STEAM_TIER_MAX_EU: dict[str, float] = {"bronze": 2, "steel": 4}

#: Which single-block crafting machines also have steam tiers (besides the
#: electric variant the recipes build). Read from the tier bitmask passed to
#: ``SingleBlockCraftingMachines.registerMachineTiers`` for each machine.
STEAM_MACHINE_TIERS: dict[str, tuple[str, ...]] = {
    "macerator": ("bronze", "steel"),
    "mixer": ("bronze", "steel"),
    "compressor": ("bronze", "steel"),
    "cutting_machine": ("bronze", "steel"),
    "packer": ("steel",),
    "unpacker": ("steel",),
    "wiremill": ("steel",),
}


# --------------------------------------------------------------------------- #
# Machines that come in distinct **blocks** sharing one recipe type.
#
# MI's Steam/Electric Quarry both run ``quarry`` recipes; the Steam/Electric Blast
# Furnace both run ``blast_furnace`` recipes; the Bronze/Steel/Electric Furnace all
# run vanilla ``minecraft:smelting`` recipes (mapped to the ``furnace`` type). The
# recipe JSON can't tell these blocks apart (the block is Java, not data), so a
# purely data-driven import makes a single def per type and silently drops every
# other block. This table restores the named blocks: a type listed here yields its
# variant defs instead of one generic def, each powered by its own carrier (steam
# vs routed electricity) and clamped to its own tier. Multiblocks (quarry / blast
# furnace) have no fixed EU tier — they scale with their energy hatches — so their
# ceiling is left unset; the single-block furnaces carry the bronze/steel/LV cap.
# --------------------------------------------------------------------------- #

#: Per-tick draw (as ``base_energy_consumption`` EU/s) for a furnace running a
#: vanilla smelting recipe — those recipes carry no ``eu``, so the block supplies
#: the base draw. 2 EU/t is MI's single-block base; the tier cap governs overclock.
_FURNACE_BASE_EU = 2 * _TICKS_PER_SEC


@dataclass(frozen=True)
class MachineVariant:
    """A named machine block that runs a shared recipe type on its own carrier.

    ``key`` is the jar block id (its def id and lang name); ``energy_source`` /
    ``source_item_id`` is how that block is powered. ``voltage_tier`` /
    ``max_eu_per_tick`` clamp overclock (absent on a hatch-scaled multiblock);
    ``base_energy_consumption`` (EU/s) is the draw for a recipe that carries no
    ``eu`` of its own (a vanilla smelting recipe), absent otherwise.
    """

    key: str
    name: str
    energy_source: EnergySource
    source_item_id: str
    voltage_tier: str | None = None
    max_eu_per_tick: float | None = None
    base_energy_consumption: float | None = None


MACHINE_VARIANTS: dict[str, tuple[MachineVariant, ...]] = {
    "blast_furnace": (
        MachineVariant(
            "steam_blast_furnace", "Steam Blast Furnace", EnergySource.LIQUID, STEAM_ID
        ),
        MachineVariant(
            "electric_blast_furnace",
            "Electric Blast Furnace",
            EnergySource.ELECTRIC,
            ELECTRICITY_ID,
        ),
    ),
    "quarry": (
        MachineVariant("steam_quarry", "Steam Quarry", EnergySource.LIQUID, STEAM_ID),
        MachineVariant(
            "electric_quarry", "Electric Quarry", EnergySource.ELECTRIC, ELECTRICITY_ID
        ),
    ),
    # The Coke Oven is a steam-age multiblock (there is no electric variant block
    # in the jar), so it runs its ``coke_oven`` recipes on **steam**, not the
    # routed electricity the generic electric def would wrongly draw. Keyed
    # ``coke_oven`` so its def id stays ``mi.machine.coke_oven``.
    "coke_oven": (
        MachineVariant("coke_oven", "Coke Oven", EnergySource.LIQUID, STEAM_ID),
    ),
    # The smelting furnaces: bronze/steel on steam, electric on electricity, each
    # clamped to its tier and drawing the base smelt rate (smelting recipes carry
    # no eu). Vanilla ``minecraft:smelting`` is mapped to the ``furnace`` type.
    "furnace": (
        MachineVariant(
            "bronze_furnace",
            "Bronze Furnace",
            EnergySource.LIQUID,
            STEAM_ID,
            "Bronze",
            STEAM_TIER_MAX_EU["bronze"],
            _FURNACE_BASE_EU,
        ),
        MachineVariant(
            "steel_furnace",
            "Steel Furnace",
            EnergySource.LIQUID,
            STEAM_ID,
            "Steel",
            STEAM_TIER_MAX_EU["steel"],
            _FURNACE_BASE_EU,
        ),
        MachineVariant(
            "electric_furnace",
            "Electric Furnace",
            EnergySource.ELECTRIC,
            ELECTRICITY_ID,
            "LV",
            CABLE_TIER_EU["LV"],
            _FURNACE_BASE_EU,
        ),
    ),
}

#: Vanilla recipe types that a mod's machines run, mapped to the machine type they
#: belong to. Discovery normally skips the whole ``minecraft`` namespace; an entry
#: here is the exception — ``minecraft:smelting`` recipes are the furnaces' recipes.
_VANILLA_MACHINE_TYPES: dict[str, str] = {"minecraft:smelting": "furnace"}


# --------------------------------------------------------------------------- #
# Water pumps (jar: AbstractWaterPumpBlockEntity + Steam/Electric subclasses).
#
# A pump runs one operation every 100 ticks (5 s), outputting
# ``waterMultiplier × waterSourceCount × 125 mB``; it scans the 8 adjacent
# blocks, so with a full ring of water sources that is ``multiplier × 1000`` mB
# per op → ``multiplier × 200`` mB/s (the max, used as the default rate — it is
# placement-dependent and editable). Water multipliers: bronze 1, steel 2,
# electric 16. Every pump draws a fixed 1 EU/tick (steam for bronze/steel,
# electricity for electric) regardless of output, so a pump is modelled as a
# consumer running a "pump water" recipe (``eu_per_tick = 1``) — not a converter,
# whose source draw would wrongly scale with the water made.
# --------------------------------------------------------------------------- #

#: mB/s of water a bronze (×1) pump makes at the best placement (8 water sources).
_PUMP_BASE_WATER_RATE = 200.0


@dataclass(frozen=True)
class PumpMachine:
    key: str
    name: str
    energy_source: EnergySource
    source_item_id: str
    water_multiplier: int


PUMP_MACHINES: tuple[PumpMachine, ...] = (
    PumpMachine(
        "bronze_water_pump", "Bronze Water Pump", EnergySource.LIQUID, STEAM_ID, 1
    ),
    PumpMachine(
        "steel_water_pump", "Steel Water Pump", EnergySource.LIQUID, STEAM_ID, 2
    ),
    PumpMachine(
        "electric_water_pump",
        "Electric Water Pump",
        EnergySource.ELECTRIC,
        ELECTRICITY_ID,
        16,
    ),
)


# --------------------------------------------------------------------------- #
# Storage — tanks (liquid) and barrels (solid), plus the single-block
# Configurable Tank/Chest and the Large Tank multiblock. Capacities are read
# from the jar (material parts in ``MIMaterials``: tanks in buckets ×1000 mB,
# barrels in stacks ×64 items; ``ConfigurableTankMachineBlockEntity`` 16 000 mB;
# ``ConfigurableChestMachineBlockEntity`` 27 slots; ``LargeTankMultiblock``
# 64 000 mB per inner block). A storage node is a transparent buffer in the
# balance — the capacity is an editable annotation.
# --------------------------------------------------------------------------- #

_TANK_BUCKETS: dict[str, int] = {
    "bronze": 4,
    "steel": 16,
    "aluminum": 64,
    "silicon": 256,
    "titanium": 1024,
}
_BARREL_STACKS: dict[str, int] = {
    "bronze": 32,
    "steel": 128,
    "aluminum": 512,
    "silicon": 2048,
    "titanium": 8192,
}
_STACK_ITEMS = 64  # items per stack, for barrel/chest item capacity


@dataclass(frozen=True)
class StorageMachine:
    key: str
    name: str
    category: Category
    capacity: float


def _storage_machines() -> tuple[StorageMachine, ...]:
    out: list[StorageMachine] = []
    for mat, buckets in _TANK_BUCKETS.items():
        out.append(
            StorageMachine(
                f"{mat}_tank", f"{mat.title()} Tank", Category.LIQUID, buckets * 1000
            )
        )
    for mat, stacks in _BARREL_STACKS.items():
        out.append(
            StorageMachine(
                f"{mat}_barrel",
                f"{mat.title()} Barrel",
                Category.SOLID,
                stacks * _STACK_ITEMS,
            )
        )
    out.append(
        StorageMachine("configurable_tank", "Configurable Tank", Category.LIQUID, 16000)
    )
    out.append(
        StorageMachine(
            "configurable_chest",
            "Configurable Chest",
            Category.SOLID,
            27 * _STACK_ITEMS,
        )
    )
    # Large Tank scales at 64 000 mB per inner block; a 27-inner build is a
    # representative default (editable).
    out.append(StorageMachine("large_tank", "Large Tank", Category.LIQUID, 64000 * 27))
    return tuple(out)


STORAGE_MACHINES: tuple[StorageMachine, ...] = _storage_machines()


# --------------------------------------------------------------------------- #
# Special machines with no recipe data.
#
# A few MI machine BLOCKS are not recipe-driven: the Nuclear Reactor runs on fuel
# rods + a heat/coolant simulation, and the Replicator turns UU-matter into any
# item — neither ships standard ``*_inputs``/``*_outputs`` recipes, so there is
# nothing to parse. They are added as recipe-less, placeable defs (item in + out
# ports, no energy model) so the machine roster is complete; bind/model them by
# hand if a project needs them. Everything else in the jar that *does* carry
# recipe or generator data is imported automatically.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SpecialMachine:
    key: str
    name: str


SPECIAL_MACHINES: tuple[SpecialMachine, ...] = (
    SpecialMachine("nuclear_reactor", "Nuclear Reactor"),
    SpecialMachine("replicator", "Replicator"),
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
    """Merge every mod's English lang map (display names) under the jar.

    Globs ``assets/*/lang/en_us.json`` so a generic import names another mod's
    items, fluids, and machines from *its* lang file, not just MI's. The MI path
    is tried first (and an MI-rooted fallback), so MI names win on any clash.
    """

    out: dict[str, str] = {}
    candidates = [
        root / "assets" / _MI_NS / "lang" / "en_us.json",
        root / _MI_NS / "lang" / "en_us.json",
        *sorted((root / "assets").glob("*/lang/en_us.json")),
    ]
    for c in candidates:
        if c.is_file():
            data = _load_json(c)
            if isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(v, str):
                        out.setdefault(k, v)
    return out


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


def _load_item_fuels(root: Path) -> tuple[dict[str, float], dict[str, float]]:
    """Solid-fuel stored energy, from NeoForge's ``furnace_fuels`` data map.

    A solid fuel's ``burn_time`` (furnace ticks) becomes its stored energy as
    ``burn_time × 20`` EU (:data:`_EU_PER_BURN_TICK` — MI values each furnace burn
    tick at 20 EU, so coal's 1600 burn ticks = 32 000 EU), mapped onto
    :attr:`Item.eu`. (Fluid fuels already carry ``eu_per_mb`` as EU directly, so
    only these solid burn-times take the ×20.) A key may name a concrete item or a
    ``#tag`` (``#c:dusts/coal``), so two maps are returned: ``{item_id: eu}``
    applied as items are registered, and ``{tag_id: eu}`` propagated to the tag's
    members once membership is resolved.
    """

    items: dict[str, float] = {}
    tags: dict[str, float] = {}
    for c in [
        root / "data" / "neoforge" / "data_maps" / "item" / "furnace_fuels.json",
        root / "neoforge" / "data_maps" / "item" / "furnace_fuels.json",
    ]:
        if c.is_file():
            data = _load_json(c)
            values = data.get("values") if isinstance(data, dict) else None
            if isinstance(values, dict):
                for key, spec in values.items():
                    if not isinstance(spec, dict) or "burn_time" not in spec:
                        continue
                    eu = float(spec["burn_time"]) * _EU_PER_BURN_TICK
                    if key.startswith("#"):
                        tags[_resolve_id(key[1:], "c")] = eu
                    else:
                        items[_resolve_id(key, "minecraft")] = eu
            break
    return items, tags


#: Vanilla ``c:`` material tags the MI jar references but doesn't ship — their
#: membership lives in NeoForge/vanilla data, absent from the mod jar, so a
#: ``c:ores/iron`` tag imports with no members and a producer of
#: ``minecraft:iron_ore`` can't satisfy a recipe keyed on it (and the item never
#: shows in the recipe finder). The canonical vanilla member ids are filled in
#: here by material × form. Only forms that exist in vanilla are listed, so no
#: phantom items are created; a modded material (osmium, zinc) stays unresolved.
_VANILLA_MATERIALS: dict[str, dict[str, list[str]]] = {
    "coal": {
        "ores": ["minecraft:coal_ore", "minecraft:deepslate_coal_ore"],
        "storage_blocks": ["minecraft:coal_block"],
    },
    "iron": {
        "ores": ["minecraft:iron_ore", "minecraft:deepslate_iron_ore"],
        "raw_materials": ["minecraft:raw_iron"],
        "ingots": ["minecraft:iron_ingot"],
        "nuggets": ["minecraft:iron_nugget"],
        "storage_blocks": ["minecraft:iron_block", "minecraft:raw_iron_block"],
    },
    "gold": {
        "ores": [
            "minecraft:gold_ore",
            "minecraft:deepslate_gold_ore",
            "minecraft:nether_gold_ore",
        ],
        "raw_materials": ["minecraft:raw_gold"],
        "ingots": ["minecraft:gold_ingot"],
        "nuggets": ["minecraft:gold_nugget"],
        "storage_blocks": ["minecraft:gold_block", "minecraft:raw_gold_block"],
    },
    "copper": {
        "ores": ["minecraft:copper_ore", "minecraft:deepslate_copper_ore"],
        "raw_materials": ["minecraft:raw_copper"],
        "ingots": ["minecraft:copper_ingot"],
        "storage_blocks": ["minecraft:copper_block", "minecraft:raw_copper_block"],
    },
    "redstone": {
        "ores": ["minecraft:redstone_ore", "minecraft:deepslate_redstone_ore"],
        "dusts": ["minecraft:redstone"],
        "storage_blocks": ["minecraft:redstone_block"],
    },
    "lapis": {
        "ores": ["minecraft:lapis_ore", "minecraft:deepslate_lapis_ore"],
        "gems": ["minecraft:lapis_lazuli"],
        "storage_blocks": ["minecraft:lapis_block"],
    },
    "diamond": {
        "ores": ["minecraft:diamond_ore", "minecraft:deepslate_diamond_ore"],
        "gems": ["minecraft:diamond"],
        "storage_blocks": ["minecraft:diamond_block"],
    },
    "emerald": {
        "ores": ["minecraft:emerald_ore", "minecraft:deepslate_emerald_ore"],
        "gems": ["minecraft:emerald"],
        "storage_blocks": ["minecraft:emerald_block"],
    },
    "quartz": {
        "ores": ["minecraft:nether_quartz_ore"],
        "gems": ["minecraft:quartz"],
        "storage_blocks": ["minecraft:quartz_block"],
    },
    "netherite": {
        "ingots": ["minecraft:netherite_ingot"],
        "storage_blocks": ["minecraft:netherite_block"],
    },
}


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


def _machine_name(machine_type: str, lang: dict[str, str], ns: str = _MI_NS) -> str:
    return lang.get(f"block.{ns}.{machine_type}") or _prettify(machine_type)


def _electric_name(machine_type: str, lang: dict[str, str], ns: str = _MI_NS) -> str:
    """Name the generic electric def off the ``electric_<type>`` block when one
    exists (a tiered machine like the macerator, whose electric variant is
    "Electric Macerator"), else the plain machine name (assembler, a multiblock)."""

    return lang.get(f"block.{ns}.electric_{machine_type}") or _machine_name(
        machine_type, lang, ns
    )


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


def _is_machine_recipe(data: dict[str, Any]) -> bool:
    """Does this recipe carry a powered-machine shape (vs a crafting recipe)?

    Any of the item/fluid I/O arrays, an ``eu`` / ``duration`` field, or the
    forge-hammer ``ingredient``→``result`` shape marks a machine recipe. Vanilla
    crafting (``pattern`` / ``key`` / plural ``ingredients``) has none of these.
    """

    if any(
        k in data
        for k in ("item_inputs", "item_outputs", "fluid_inputs", "fluid_outputs")
    ):
        return True
    if "eu" in data or "duration" in data:
        return True
    return isinstance(data.get("ingredient"), dict) and "result" in data


def _machine_type_of(
    rtype: Any, data: dict[str, Any], wanted: set[str] | None
) -> tuple[str, str] | None:
    """Return ``(namespace, bare_type)`` if this recipe is an importable machine.

    Skips the whole ``minecraft`` namespace and bare (un-namespaced) vanilla
    types. With ``wanted`` given, keeps only those bare type names; otherwise
    discovers any namespaced type whose recipe has a machine shape — so a
    different mod's machines import with no allowlist edit.
    """

    if not isinstance(rtype, str) or ":" not in rtype:
        return None
    # A vanilla type a machine runs (smelting → furnace) is mapped, not skipped;
    # its namespace stays so naming resolves the real block ids.
    mapped = _VANILLA_MACHINE_TYPES.get(rtype)
    if mapped is not None:
        if wanted is not None and mapped not in wanted:
            return None
        return (rtype.partition(":")[0], mapped)
    ns, _, name = rtype.partition(":")
    if ns == "minecraft" or not name:
        return None
    if wanted is not None:
        return (ns, name) if name in wanted else None
    return (ns, name) if _is_machine_recipe(data) else None


class _ItemRegistry:
    """Accumulates the unique items/fluids/tags referenced, as model Items."""

    def __init__(
        self,
        lang: dict[str, str],
        fluid_fuels: dict[str, float],
        item_fuels: dict[str, float] | None = None,
    ) -> None:
        self._lang = lang
        self._fuels = fluid_fuels
        self._item_fuels = item_fuels or {}
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
                eu = self._item_fuels.get(full)
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
    machine_types: tuple[str, ...] | None = None,
    name: str | None = None,
    include_energy: bool = True,
) -> MiImport:
    """Build a :class:`Library` from the machine recipes under ``root``.

    ``root`` is an extracted MI jar directory (or the recipe directory itself).
    By **default every powered-machine recipe type is discovered and imported** —
    any namespaced type (not ``minecraft:``, not a bare vanilla type) whose
    recipes carry a machine shape (``eu`` / ``duration`` / item-fluid I/O arrays,
    or the forge-hammer ``ingredient``→``result`` shape). So a *different mod*
    whose machines use the same recipe structure imports with no code change —
    the type list is no longer a hand-maintained allowlist. Vanilla
    crafting/smelting recipes are always skipped. Pass ``machine_types`` (bare
    type names, e.g. ``("macerator", "mixer")``) to import only a subset.

    The result is never merged here — hand its ``library`` to
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
    item_fuels, tag_fuels = _load_item_fuels(root)
    wanted = set(machine_types) if machine_types is not None else None

    registry = _ItemRegistry(lang, fuels, item_fuels)
    recipes: list[Recipe] = []
    # Per bare machine-type name: its namespace, collected recipe ids, and the max
    # I/O counts (for the derived slot layout). Populated lazily as types are
    # discovered, so the importable set is not a fixed allowlist.
    by_type: dict[str, dict[str, Any]] = {}
    files_scanned = 0
    recipes_skipped = 0

    for path in sorted(recipe_dir.rglob("*.json")):
        files_scanned += 1
        data = _load_json(path)
        if not isinstance(data, dict):
            recipes_skipped += 1
            continue
        rtype = data.get("type")
        parsed = _machine_type_of(rtype, data, wanted)
        if parsed is None:
            recipes_skipped += 1
            continue
        ns, machine_type = parsed

        rel = path.relative_to(recipe_dir).with_suffix("").as_posix()
        recipe_id = f"mi:{rel}"

        item_in = _entries(data, "item_inputs")
        item_out = _entries(data, "item_outputs")
        fluid_in = _entries(data, "fluid_inputs")
        fluid_out = _entries(data, "fluid_outputs")
        # The forge hammer and the vanilla smelting furnaces use a single
        # ``ingredient`` → ``result`` shape instead of the ``*_inputs``/
        # ``*_outputs`` arrays. Normalize it to the item-entry shape the loops
        # below expect (``count`` → ``amount``; a ``result`` that is a dict with
        # an ``id`` or a bare item-id string → ``item``).
        ingredient = data.get("ingredient")
        if isinstance(ingredient, dict):
            item_in = [*item_in, _normalize_count(ingredient)]
        result = data.get("result")
        if isinstance(result, dict) and result.get("id") is not None:
            item_out = [
                *item_out,
                {"item": result["id"], "amount": result.get("count", 1)},
            ]
        elif isinstance(result, str):
            item_out = [*item_out, {"item": result, "amount": 1}]

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

        # ``duration`` is MI's per-recipe tick count; a vanilla smelting recipe
        # uses ``cookingtime`` instead (the furnace runs it at that speed).
        duration = data.get("duration")
        if duration is None:
            duration = data.get("cookingtime")
        eu = data.get("eu")
        time_seconds = float(duration) / 20.0 if duration else 0.0

        recipes.append(
            Recipe(
                id=recipe_id,
                time_seconds=time_seconds,
                name=_recipe_name(machine_type, outputs, registry, lang, ns),
                inputs=inputs,
                outputs=outputs,
                eu_per_tick=float(eu) if eu is not None else None,
                machine_type=machine_type,
            )
        )

        agg = by_type.setdefault(
            machine_type,
            {"ns": ns, "recipe_ids": [], "sin": 0, "sout": 0, "lin": 0, "lout": 0},
        )
        agg["recipe_ids"].append(recipe_id)
        agg["sin"] = max(agg["sin"], len(item_in))
        agg["sout"] = max(agg["sout"], len(item_out))
        agg["lin"] = max(agg["lin"], len(fluid_in))
        agg["lout"] = max(agg["lout"], len(fluid_out))

    # One def per discovered machine type. A type whose recipes run on several
    # named blocks (a steam vs electric quarry / blast furnace) yields those
    # variants; every other type yields a single generic electric-consumer def.
    machine_defs: list[MachineDef] = []
    needs_steam = False
    for t, agg in by_type.items():
        if not agg["recipe_ids"]:
            continue
        variants = MACHINE_VARIANTS.get(t)
        if variants:
            for v in variants:
                machine_defs.append(_variant_machine_def(t, v, agg, lang))
                needs_steam = needs_steam or v.energy_source is EnergySource.LIQUID
        else:
            machine_defs.append(_machine_def(t, agg, lang))

    # Steam (bronze/steel) tiers for the single-block machines that have them,
    # beside the electric variant the recipes built. These are processing machines
    # (not energy generation), so they ride every import; each consumes steam.
    steam_defs: list[MachineDef] = []
    for t, agg in by_type.items():
        if not agg["recipe_ids"]:
            continue
        for tier in STEAM_MACHINE_TIERS.get(t, ()):
            steam_defs.append(_steam_machine_def(t, tier, agg, lang))
    if steam_defs or needs_steam:
        _ensure_steam_item(registry)

    # Energy generation (not recipe-driven in MI): add the converter defs and the
    # carriers they reference (electricity, steam, water, plasma). The water pumps
    # ride with it — they need the electricity carrier (electric pump) and steam
    # (steam pumps), which the energy carriers provide. Done before tag membership
    # so a newly-added carrier still gets any tag wiring.
    energy_defs: list[MachineDef] = []
    pump_defs: list[MachineDef] = []
    pump_recipes: list[Recipe] = []
    if include_energy:
        _ensure_carrier_items(registry)
        energy_defs = [_energy_machine_def(m) for m in ENERGY_MACHINES]
        pump_defs = [_pump_def(m) for m in PUMP_MACHINES]
        pump_recipes = [_pump_recipe(m) for m in PUMP_MACHINES]

    # Storage (tanks / barrels / configurable / large tank): transparent buffers,
    # no carriers needed, so always added.
    storage_defs = [_storage_def(m) for m in STORAGE_MACHINES]

    # Recipe-less special machines (Nuclear Reactor, Replicator) — present for a
    # complete roster, named from lang, inert until modelled by hand.
    special_defs = [_special_def(m, lang) for m in SPECIAL_MACHINES]

    # Resolve tag membership from the game's tag definitions, so a concrete item
    # can later stand in for a recipe that references a tag (e.g. iron_ore → a
    # recipe keyed on c:ores/iron). Also register any member item not already
    # imported, so it is a named, selectable resource.
    member_items_added = _resolve_tag_membership(root, registry, lang)
    # Fill vanilla c: material tags (ores/ingots/raw/…) the jar references but
    # NeoForge — not the mod — defines, so iron ore & co. resolve and are findable.
    member_items_added += _resolve_vanilla_tags(registry, lang)
    # A solid fuel named by a #tag (e.g. #c:dusts/coal) carries its burn-time EU to
    # every member, now that membership is resolved.
    _apply_tag_fuels(registry, tag_fuels)

    library = Library(
        name=name or "Modern Industrialization",
        items=sorted(registry.items.values(), key=lambda i: i.id),
        recipes=recipes + pump_recipes,
        machine_defs=(
            machine_defs
            + steam_defs
            + energy_defs
            + pump_defs
            + storage_defs
            + special_defs
        ),
    )
    stats = {
        "filesScanned": files_scanned,
        "recipesImported": len(recipes),
        "recipesSkipped": recipes_skipped,
        "items": sum(1 for i in registry.items.values() if not i.is_tag),
        "tags": sum(1 for i in registry.items.values() if i.is_tag),
        "memberItemsAdded": member_items_added,
        "machineDefs": len(machine_defs),
        "steamMachines": len(steam_defs),
        "energyMachines": len(energy_defs),
        "pumps": len(pump_defs),
        "storage": len(storage_defs),
        "special": len(special_defs),
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


def _resolve_vanilla_tags(registry: _ItemRegistry, lang: dict[str, str]) -> int:
    """Fill the vanilla ``c:`` material tags the jar leaves without members.

    MI references ``c:ores/iron`` and the like but relies on NeoForge/vanilla to
    populate them, so they import empty (:data:`_VANILLA_MATERIALS` holds the
    canonical vanilla member ids). For each still-empty tag whose
    ``c:<group>/<material>`` matches the table, set its members and register any
    missing member item. Returns the count of member items newly created.
    """

    added = 0
    for tag in [i for i in registry.items.values() if i.is_tag and not i.members]:
        ns, _, path = tag.id.partition(":")
        if ns != "c" or "/" not in path:
            continue
        group, _, material = path.partition("/")
        members = _VANILLA_MATERIALS.get(material, {}).get(group)
        if not members:
            continue
        tag.members = list(members)
        for mid in members:
            if mid not in registry.items:
                registry.items[mid] = Item(
                    id=mid,
                    name=_item_name(mid, lang),
                    category=tag.category,
                )
                added += 1
    return added


def _apply_tag_fuels(registry: _ItemRegistry, tag_fuels: dict[str, float]) -> None:
    """Propagate a tag-keyed solid fuel's stored EU to each resolved member."""

    for tag_id, eu in tag_fuels.items():
        tag = registry.items.get(tag_id)
        if tag is None or not tag.members:
            continue
        for mid in tag.members:
            member = registry.items.get(mid)
            if member is not None and member.eu is None:
                member.eu = eu


def _recipe_name(
    machine_type: str,
    outputs: list[Stack],
    registry: _ItemRegistry,
    lang: dict[str, str],
    ns: str = _MI_NS,
) -> str:
    """A readable label: ``<Machine>: <primary output>`` (else just the machine)."""

    primary = next((s.resource_id for s in outputs if s.resource_id), None)
    machine = _machine_name(machine_type, lang, ns)
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
        name=_electric_name(machine_type, lang, agg.get("ns", _MI_NS)),
        energy_source=EnergySource.ELECTRIC,
        source_item_id=ELECTRICITY_ID,
        allowed_recipe_ids=list(agg["recipe_ids"]),
        slots=slots,
        voltage_tier=spec.voltage_tier,
        max_eu_per_tick=spec.max_eu_per_tick,
    )


def _variant_machine_def(
    machine_type: str,
    variant: MachineVariant,
    agg: dict[str, Any],
    lang: dict[str, str],
) -> MachineDef:
    """A named machine-block variant def (steam / electric) for a shared type.

    Same recipes and item slots the type's recipes derive, but powered by the
    variant's carrier: a steam block adds a liquid-in port for steam; an electric
    block an energy-in port for routed electricity. A multiblock has no fixed EU
    tier (it scales with its energy hatches), so no ``max_eu_per_tick`` ceiling.
    """

    slots: list[SlotGroup] = []

    def add(category: Category, direction: Direction, count: int, stack: int) -> None:
        if count > 0:
            slots.append(SlotGroup(category, direction, count, stack))

    is_steam = variant.energy_source is EnergySource.LIQUID
    add(Category.SOLID, Direction.IN, agg["sin"], _SOLID_STACK)
    add(Category.SOLID, Direction.OUT, agg["sout"], _SOLID_STACK)
    add(
        Category.LIQUID, Direction.IN, agg["lin"] + (1 if is_steam else 0), _FLUID_STACK
    )
    add(Category.LIQUID, Direction.OUT, agg["lout"], _FLUID_STACK)
    if not is_steam:
        add(Category.ENERGY, Direction.IN, 1, _ENERGY_STACK)  # routed electricity

    name = _machine_name(variant.key, lang, agg.get("ns", _MI_NS)) or variant.name
    return MachineDef(
        id=f"mi.machine.{variant.key}",
        name=name,
        energy_source=variant.energy_source,
        source_item_id=variant.source_item_id,
        base_energy_consumption=variant.base_energy_consumption,
        allowed_recipe_ids=list(agg["recipe_ids"]),
        slots=slots,
        voltage_tier=variant.voltage_tier,
        max_eu_per_tick=variant.max_eu_per_tick,
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


def _steam_machine_def(
    machine_type: str, tier: str, agg: dict[str, Any], lang: dict[str, str]
) -> MachineDef:
    """A steam-tier (bronze/steel) consumer def for ``machine_type``.

    Same recipes and item slots as the electric variant, but powered by **steam**
    (``energy_source = liquid``, ``source_item_id = steam``) through an added
    liquid-in port, and clamped to the tier's EU/tick ceiling so it overclocks
    only as far as the tier allows.
    """

    slots: list[SlotGroup] = []

    def add(category: Category, direction: Direction, count: int, stack: int) -> None:
        if count > 0:
            slots.append(SlotGroup(category, direction, count, stack))

    add(Category.SOLID, Direction.IN, agg["sin"], _SOLID_STACK)
    add(Category.SOLID, Direction.OUT, agg["sout"], _SOLID_STACK)
    # Recipe fluids plus one liquid-in port for the steam carrier.
    add(Category.LIQUID, Direction.IN, agg["lin"] + 1, _FLUID_STACK)
    add(Category.LIQUID, Direction.OUT, agg["lout"], _FLUID_STACK)

    base_name = _machine_name(machine_type, lang, agg.get("ns", _MI_NS))
    return MachineDef(
        id=f"mi.machine.{machine_type}.{tier}",
        name=f"{tier.title()} {base_name}",
        energy_source=EnergySource.LIQUID,
        source_item_id=STEAM_ID,
        allowed_recipe_ids=list(agg["recipe_ids"]),
        slots=slots,
        voltage_tier=tier.title(),
        max_eu_per_tick=STEAM_TIER_MAX_EU[tier],
    )


def _pump_recipe(m: PumpMachine) -> Recipe:
    """A 'pump water' recipe: no material in, water out, a fixed 1 EU/tick draw."""

    rate = _PUMP_BASE_WATER_RATE * m.water_multiplier
    return Recipe(
        id=f"mi.pump.{m.key}",
        time_seconds=1.0,
        name=f"{m.name}: Water",
        inputs=[],
        outputs=[Stack(item_id=WATER_ID, quantity=rate)],
        eu_per_tick=1,
        machine_type=m.key,
    )


def _pump_def(m: PumpMachine) -> MachineDef:
    """A water-pump consumer: water out, powered by steam or electricity."""

    slots: list[SlotGroup] = [
        SlotGroup(Category.LIQUID, Direction.OUT, 1, _FLUID_STACK)
    ]
    if m.energy_source is EnergySource.ELECTRIC:
        slots.append(SlotGroup(Category.ENERGY, Direction.IN, 1, _ENERGY_STACK))
    else:
        slots.append(SlotGroup(Category.LIQUID, Direction.IN, 1, _FLUID_STACK))
    return MachineDef(
        id=f"mi.pump.{m.key}.machine",
        name=m.name,
        energy_source=m.energy_source,
        source_item_id=m.source_item_id,
        allowed_recipe_ids=[f"mi.pump.{m.key}"],
        slots=slots,
    )


def _storage_def(m: StorageMachine) -> MachineDef:
    """A transparent storage buffer def with in/out ports of its category."""

    return MachineDef(
        id=f"mi.storage.{m.key}",
        name=m.name,
        storage=StorageSpec(category=m.category, capacity=m.capacity),
        slots=[
            SlotGroup(
                m.category,
                Direction.IN,
                1,
                _FLUID_STACK if m.category is Category.LIQUID else _SOLID_STACK,
            ),
            SlotGroup(
                m.category,
                Direction.OUT,
                1,
                _FLUID_STACK if m.category is Category.LIQUID else _SOLID_STACK,
            ),
        ],
    )


def _special_def(m: SpecialMachine, lang: dict[str, str]) -> MachineDef:
    """A recipe-less placeable def (item in + out ports) for a special machine.

    Named from lang; no energy source and no ``allowed_recipe_ids`` — MI's Nuclear
    Reactor and Replicator aren't recipe-driven, so they're inert in the balance
    until a project models them by hand."""

    return MachineDef(
        id=f"mi.machine.{m.key}",
        name=_machine_name(m.key, lang) or m.name,
        slots=[
            SlotGroup(Category.SOLID, Direction.IN, 1, _SOLID_STACK),
            SlotGroup(Category.SOLID, Direction.OUT, 1, _SOLID_STACK),
        ],
    )


def _ensure_steam_item(registry: _ItemRegistry) -> None:
    """Register the steam carrier if absent (steam machines consume it)."""

    if STEAM_ID not in registry.items:
        registry.items[STEAM_ID] = Item(
            id=STEAM_ID, name="Steam", category=Category.LIQUID
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

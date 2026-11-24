"""Import a whole Minecraft modpack instance — every mod plus the pack's own edits.

Importing one mod jar at a time breaks down on a real pack, for general reasons:

* **Data lives across jars.** A recipe in one mod names a tag another mod (or
  NeoForge) defines — Extended Industrialization's invar alloy takes
  ``c:ingots/nickel``, which Modern Industrialization's jar populates — and an
  item's display name sits in its own mod's lang file. Read alone, the tag is
  empty and the input "can't be made".
* **Ingredients are richer than ``{"item"}``.** NeoForge compound / difference /
  intersection ingredients and vanilla arrays ("any of these") must be read as a
  set of acceptable inputs (see :mod:`process_sim.io.ingredients`).
* **Recipes are conditional.** ``neoforge:conditions`` (``mod_loaded`` …) decide
  whether a recipe exists in *this* pack, and a recipe whose type belongs to an
  absent mod is never loaded by the game.
* **The pack rewrites content at runtime.** KubeJS scripts add, remove and edit
  recipes, register new items and materials (MI materials registered by a
  script get their recipes generated in-game), and change tags. None of that is
  in any jar.

So this reads the instance the way the game assembles its datapacks — every jar
in ``mods/``, then the pack's ``kubejs/`` data, then any global datapack folders
— merging recipes by id, tags (honouring ``replace``), lang, and the fuel data
maps; it evaluates load conditions against the jars' mod ids. That **static**
view is complete for packs without scripts and is the fallback otherwise.

For script-driven packs the authoritative recipe list only exists inside the
running game, so the importer also reads a **runtime export** written by the
KubeJS script shipped at ``tools/kubejs/process_sim_export.js``
(:data:`EXPORTER_SCRIPT`): the final recipe JSON after every script ran, the
resolved item/fluid tags, and in-game display names. When that export exists it
replaces the static recipe set and tags (names overlay the lang files); the jars
still supply fuel values and lang. :func:`install_exporter` copies the script
into the instance's ``local/kubejs/local_server_scripts`` (a folder pack updates
don't touch); the export is written on the next world load or ``/reload``.

Recipe ids are the game's (``create:crushing/…``) except Modern
Industrialization's, which keep the ``mi:<path>`` form the single-jar importer
has always used — so a pack import over an existing MI catalog adds what is new
rather than duplicating every MI recipe.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..model import Category
from .errors import MalformedFileError
from .mi_import import MiImport, RecipeSource, build_import

__all__ = [
    "EXPORT_FORMAT",
    "EXPORT_RELPATH",
    "EXPORTER_SCRIPT",
    "EXPORTER_INSTALL_RELPATH",
    "PackData",
    "find_game_dir",
    "find_base_jars",
    "read_pack",
    "import_pack",
    "install_exporter",
]

#: The runtime export's ``format`` marker and where (under the game dir) the
#: exporter script writes it.
EXPORT_FORMAT = "process-sim-pack-export"
EXPORT_RELPATH = Path("local") / "process_sim" / "pack_export.json"
#: The KubeJS exporter shipped with the app, and where it is installed.
EXPORTER_SCRIPT = (
    Path(__file__).resolve().parents[2] / "tools" / "kubejs" / "process_sim_export.js"
)
EXPORTER_INSTALL_RELPATH = (
    Path("local") / "kubejs" / "local_server_scripts" / "process_sim_export.js"
)

_MI_NS = "modern_industrialization"
#: Namespaces always present (the game, the loader, and the shared ``c:`` tags).
_BASE_MODS = {"minecraft", "neoforge", "c", "forge", "kubejs"}

_RECIPE_RE = re.compile(r"^data/([^/]+)/recipes?/(.+)\.json$")
_TAG_RE = re.compile(r"^data/([^/]+)/tags/(items?|fluids?)/(.+)\.json$")
_LANG_RE = re.compile(r"^assets/([^/]+)/lang/en_us\.json$")
_FURNACE_FUELS = "data/neoforge/data_maps/item/furnace_fuels.json"
_FLUID_FUELS = f"data/{_MI_NS}/data_maps/fluid/fluid_fuels.json"
#: MI values each furnace burn tick at 20 EU (coal: 1600 ticks = 32 000 EU).
_EU_PER_BURN_TICK = 20.0


# --------------------------------------------------------------------------- #
# Locating the instance.
# --------------------------------------------------------------------------- #


def find_game_dir(path: str | Path) -> Path:
    """The game directory (the one holding ``mods/``) for an instance path.

    Accepts the game dir itself or a launcher instance folder containing a
    ``minecraft`` / ``.minecraft`` game dir (Prism, MultiMC, CurseForge…).
    """

    p = Path(path)
    for cand in (p, p / "minecraft", p / ".minecraft"):
        if (cand / "mods").is_dir():
            return cand
    raise MalformedFileError(
        f"not a modpack instance: no mods/ folder in {p} (or its minecraft/ dir)"
    )


def _pack_name(game_dir: Path) -> str:
    if game_dir.name.lower() in ("minecraft", ".minecraft") and game_dir.parent.name:
        return game_dir.parent.name
    return game_dir.name


def _instance_versions(game_dir: Path) -> dict[str, str]:
    """Minecraft / NeoForge versions from the launcher's instance metadata.

    Reads Prism/MultiMC ``mmc-pack.json`` and CurseForge ``minecraftinstance.json``
    (whichever sits beside or inside the game dir). Missing → ``{}``.
    """

    out: dict[str, str] = {}
    for cand in (game_dir.parent / "mmc-pack.json", game_dir / "mmc-pack.json"):
        data = _parse_json(cand.read_bytes()) if cand.is_file() else None
        if isinstance(data, dict):
            for comp in data.get("components", []):
                if not isinstance(comp, dict):
                    continue
                uid, ver = comp.get("uid"), comp.get("version")
                if uid == "net.minecraft" and isinstance(ver, str):
                    out["minecraft"] = ver
                elif uid == "net.neoforged" and isinstance(ver, str):
                    out["neoforge"] = ver
            return out
    for cand in (
        game_dir / "minecraftinstance.json",
        game_dir.parent / "minecraftinstance.json",
    ):
        data = _parse_json(cand.read_bytes()) if cand.is_file() else None
        if isinstance(data, dict):
            mc = data.get("gameVersion")
            if isinstance(mc, str):
                out["minecraft"] = mc
            loader = (data.get("baseModLoader") or {}).get("name", "")
            m = re.match(r"neoforge-(.+)$", str(loader))
            if m:
                out["neoforge"] = m.group(1)
            return out
    return out


def _library_roots(game_dir: Path) -> list[Path]:
    """Folders that may hold the launcher's ``libraries/`` tree.

    The instance's own ancestors first (a Prism/MultiMC instance lives at
    ``<launcher>/instances/<name>/minecraft``), then the usual launcher homes.
    """

    roots: list[Path] = []
    for anc in list(game_dir.resolve().parents)[:4]:
        roots.append(anc / "libraries")
    home = Path.home()
    appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
    for launcher in (
        "PrismLauncher",
        "FreesmLauncher",
        "MultiMC",
        "PolyMC",
        ".minecraft",
    ):
        roots.append(appdata / launcher / "libraries")
    roots += [
        home / ".local" / "share" / "PrismLauncher" / "libraries",
        home / "Library" / "Application Support" / "PrismLauncher" / "libraries",
        home / ".minecraft" / "libraries",
        home / "curseforge" / "minecraft" / "Install" / "libraries",
    ]
    seen: set[Path] = set()
    return [r for r in roots if r.is_dir() and not (r in seen or seen.add(r))]


def find_base_jars(game_dir: Path) -> list[Path]:
    """The vanilla game jar and NeoForge jar the instance runs on, if found.

    These hold vanilla's recipes/tags and NeoForge's ``c:`` common tags (which
    every mod's recipes reference) — data that is in no ``mods/`` jar. Returned
    in load order (vanilla, then NeoForge); an empty list when the launcher's
    libraries can't be located (the runtime export doesn't need them).
    """

    versions = _instance_versions(game_dir)
    mc, nf = versions.get("minecraft"), versions.get("neoforge")
    out: list[Path] = []
    for root in _library_roots(game_dir):
        if mc and not any("minecraft" in p.name for p in out):
            for cand in (
                root
                / "com"
                / "mojang"
                / "minecraft"
                / mc
                / f"minecraft-{mc}-client.jar",
                root.parent / "versions" / mc / f"{mc}.jar",
            ):
                if cand.is_file():
                    out.append(cand)
                    break
        if nf and not any("neoforge" in p.name for p in out):
            cand = (
                root
                / "net"
                / "neoforged"
                / "neoforge"
                / nf
                / f"neoforge-{nf}-universal.jar"
            )
            if cand.is_file():
                out.append(cand)
    out.sort(key=lambda p: 0 if "minecraft" in p.name else 1)
    return out


# --------------------------------------------------------------------------- #
# Reading datapack-shaped sources (jars, zips, folders).
# --------------------------------------------------------------------------- #


def _iter_zip(path: Path) -> Iterator[tuple[str, bytes]]:
    try:
        z = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile):
        return
    with z:
        for name in z.namelist():
            if name.endswith("/"):
                continue
            if (
                _RECIPE_RE.match(name)
                or _TAG_RE.match(name)
                or _LANG_RE.match(name)
                or name in (_FURNACE_FUELS, _FLUID_FUELS)
                or name in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml")
            ):
                try:
                    yield name, z.read(name)
                except (OSError, zipfile.BadZipFile, KeyError):
                    continue


def _iter_dir(root: Path) -> Iterator[tuple[str, bytes]]:
    for sub in ("data", "assets"):
        base = root / sub
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*.json")):
            rel = f"{sub}/{f.relative_to(base).as_posix()}"
            if (
                _RECIPE_RE.match(rel)
                or _TAG_RE.match(rel)
                or _LANG_RE.match(rel)
                or rel in (_FURNACE_FUELS, _FLUID_FUELS)
            ):
                try:
                    yield rel, f.read_bytes()
                except OSError:
                    continue


def _parse_json(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _mod_ids(raw: bytes) -> set[str]:
    try:
        data = tomllib.loads(raw.decode("utf-8", "replace"))
    except tomllib.TOMLDecodeError:
        # Some jars ship TOML the strict parser rejects; fall back to a regex.
        return set(re.findall(r'modId\s*=\s*"([^"]+)"', raw.decode("utf-8", "replace")))
    mods = data.get("mods")
    if not isinstance(mods, list):
        return set()
    return {
        m["modId"]
        for m in mods
        if isinstance(m, dict) and isinstance(m.get("modId"), str)
    }


@dataclass
class PackData:
    """A modpack's data merged the way the game layers its datapacks."""

    name: str
    game_dir: Path
    mods: set[str] = field(default_factory=set)
    #: recipe id → (json, source label)
    recipes: dict[str, tuple[Any, str]] = field(default_factory=dict)
    #: (kind "item"|"fluid", tag id) → raw entry list (after ``replace``)
    tags: dict[tuple[str, str], list[Any]] = field(default_factory=dict)
    lang: dict[str, str] = field(default_factory=dict)
    furnace_fuels: dict[str, Any] = field(default_factory=dict)
    fluid_fuels: dict[str, Any] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    base_jars: list[Path] = field(default_factory=list)
    #: The parsed runtime export, when present.
    export: dict[str, Any] | None = None
    export_path: Path | None = None
    export_stale: bool = False

    # -- merging ----------------------------------------------------------- #

    def add_file(self, rel: str, raw: bytes, source: str) -> None:
        if rel.startswith("META-INF/"):
            self.mods |= _mod_ids(raw)
            return
        m = _RECIPE_RE.match(rel)
        if m:
            self.recipes[f"{m.group(1)}:{m.group(2)}"] = (_parse_json(raw), source)
            return
        m = _TAG_RE.match(rel)
        if m:
            data = _parse_json(raw)
            if not isinstance(data, dict):
                return
            kind = "fluid" if m.group(2).startswith("fluid") else "item"
            key = (kind, f"{m.group(1)}:{m.group(3)}")
            values = data.get("values")
            values = values if isinstance(values, list) else []
            if data.get("replace") is True or key not in self.tags:
                self.tags[key] = list(values)
            else:
                self.tags[key].extend(values)
            return
        if _LANG_RE.match(rel):
            data = _parse_json(raw)
            if isinstance(data, dict):
                # Later sources (the pack's kubejs assets) override earlier ones,
                # as a resource pack layered on top would.
                self.lang.update({k: v for k, v in data.items() if isinstance(v, str)})
            return
        if rel in (_FURNACE_FUELS, _FLUID_FUELS):
            data = _parse_json(raw)
            values = data.get("values") if isinstance(data, dict) else None
            target = self.furnace_fuels if rel == _FURNACE_FUELS else self.fluid_fuels
            if isinstance(data, dict) and data.get("replace") is True:
                target.clear()
            if isinstance(values, dict):
                target.update(values)

    # -- queries ----------------------------------------------------------- #

    def mod_present(self, namespace: str) -> bool:
        return namespace in self.mods or namespace in _BASE_MODS

    def static_tag_members(self, tag_id: str, kind: str) -> set[str]:
        """Flatten a merged tag (nested ``#tag`` refs; optional entries kept only
        when their mod is present)."""

        out: set[str] = set()
        seen: set[str] = set()
        stack = [tag_id]
        while stack:
            t = stack.pop()
            if t in seen:
                continue
            seen.add(t)
            for v in self.tags.get((kind, t), ()):
                required = True
                if isinstance(v, dict):
                    required = v.get("required", True) is not False
                    v = v.get("id")
                if not isinstance(v, str) or not v:
                    continue
                if v.startswith("#"):
                    stack.append(v[1:] if ":" in v else f"minecraft:{v[1:]}")
                    continue
                full = v if ":" in v else f"minecraft:{v}"
                if not required and not self.mod_present(full.partition(":")[0]):
                    continue
                out.add(full)
        return out


def read_pack(path: str | Path, *, export_path: str | Path | None = None) -> PackData:
    """Read and merge every datapack source of the instance at ``path``.

    Order (later wins on a recipe id clash, tags merge): every ``mods/*.jar``
    by file name, then the pack's ``kubejs/`` folder, then global datapack
    folders (``datapacks/``, ``global_packs/``, ``config/openloader/data/``).
    The runtime export is read from ``export_path`` or its default location.
    """

    game_dir = find_game_dir(path)
    pack = PackData(name=_pack_name(game_dir), game_dir=game_dir)

    base_jars = find_base_jars(game_dir)
    for jar in base_jars:
        for rel, raw in _iter_zip(jar):
            pack.add_file(rel, raw, jar.name)
    pack.sources.extend(j.name for j in base_jars)
    pack.base_jars = base_jars

    jars = sorted((game_dir / "mods").glob("*.jar"), key=lambda p: p.name.lower())
    for jar in jars:
        for rel, raw in _iter_zip(jar):
            pack.add_file(rel, raw, jar.name)
    pack.sources.extend(j.name for j in jars)

    extra: list[Path] = []
    if (game_dir / "kubejs").is_dir():
        extra.append(game_dir / "kubejs")
    for folder in ("datapacks", "global_packs", "config/openloader/data"):
        d = game_dir / folder
        if d.is_dir():
            extra.extend(sorted(p for p in d.rglob("pack.mcmeta")))
    for src in extra:
        root = src.parent if src.name == "pack.mcmeta" else src
        for rel, raw in _iter_dir(root):
            pack.add_file(rel, raw, root.name)
        pack.sources.append(str(root.relative_to(game_dir)))
    # Zipped datapacks in those folders.
    for folder in ("datapacks", "global_packs", "config/openloader/data"):
        d = game_dir / folder
        if d.is_dir():
            for z in sorted(d.rglob("*.zip")):
                for rel, raw in _iter_zip(z):
                    pack.add_file(rel, raw, z.name)
                pack.sources.append(str(z.relative_to(game_dir)))

    exp = Path(export_path) if export_path else game_dir / EXPORT_RELPATH
    if exp.is_file():
        data = _parse_json(exp.read_bytes())
        if not (isinstance(data, dict) and data.get("format") == EXPORT_FORMAT):
            raise MalformedFileError(f"{exp} is not a process-sim pack export")
        pack.export = data
        pack.export_path = exp
        newest = max(
            [j.stat().st_mtime for j in jars]
            + [
                f.stat().st_mtime
                for f in (game_dir / "kubejs").rglob("*.js")
                if (game_dir / "kubejs").is_dir()
            ],
            default=0.0,
        )
        pack.export_stale = exp.stat().st_mtime < newest
    elif export_path:
        raise MalformedFileError(f"pack export not found: {exp}")
    return pack


# --------------------------------------------------------------------------- #
# Load conditions.
# --------------------------------------------------------------------------- #


def _conditions_of(data: dict[str, Any]) -> list[Any]:
    for key in ("neoforge:conditions", "conditions", "forge:conditions"):
        v = data.get(key)
        if isinstance(v, list):
            return v
    return []


def _condition_holds(c: Any, pack: PackData, tag_empty: Any) -> bool:
    """Evaluate one NeoForge load condition against the pack.

    Mod-presence logic is exact; ``item_exists`` checks the item's mod is
    present; ``tag_empty`` checks the merged tags. A mod's own condition types
    (config flags) can't be read from data, so they are assumed enabled — the
    pack's defaults.
    """

    if not isinstance(c, dict):
        return True
    t = c.get("type")
    if t in ("neoforge:mod_loaded", "forge:mod_loaded"):
        return pack.mod_present(str(c.get("modid", "")))
    if t in ("neoforge:not", "forge:not"):
        return not _condition_holds(c.get("value"), pack, tag_empty)
    if t in ("neoforge:and", "forge:and"):
        return all(_condition_holds(v, pack, tag_empty) for v in c.get("values", []))
    if t in ("neoforge:or", "forge:or"):
        return any(_condition_holds(v, pack, tag_empty) for v in c.get("values", []))
    if t in ("neoforge:item_exists", "forge:item_exists"):
        item = str(c.get("item", ""))
        return pack.mod_present(item.partition(":")[0] if ":" in item else "minecraft")
    if t in ("neoforge:tag_empty", "forge:tag_empty"):
        return tag_empty(str(c.get("tag", "")))
    if t in ("neoforge:false", "forge:false"):
        return False
    return True


# --------------------------------------------------------------------------- #
# Building the recipe source.
# --------------------------------------------------------------------------- #


def _game_to_recipe_id(game_id: str) -> str:
    ns, _, path = game_id.partition(":")
    return f"mi:{path}" if ns == _MI_NS else game_id


def _export_recipes(export: dict[str, Any]) -> dict[str, Any]:
    raw = export.get("recipes")
    out: dict[str, Any] = {}
    if not isinstance(raw, dict):
        return out
    for rid, v in raw.items():
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except json.JSONDecodeError:
                v = None
        out[str(rid)] = v
    return out


def _export_tags(export: dict[str, Any], key: str) -> dict[str, set[str]]:
    raw = export.get(key)
    if not isinstance(raw, dict):
        return {}
    return {
        str(t): {str(m) for m in members if isinstance(m, str)}
        for t, members in raw.items()
        if isinstance(members, list)
    }


def pack_source(pack: PackData) -> RecipeSource:
    """Turn merged pack data (plus any runtime export) into a :class:`RecipeSource`."""

    export = pack.export
    if export is not None:
        game_recipes = _export_recipes(export)
        item_tags = _export_tags(export, "itemTags")
        fluid_tags = _export_tags(export, "fluidTags")
    else:
        game_recipes = {rid: data for rid, (data, _src) in pack.recipes.items()}
        item_tags, fluid_tags = {}, {}

    cache: dict[tuple[str, str], set[str]] = {}

    def members(tag_id: str, kind: str) -> set[str]:
        key = (kind, tag_id)
        if key not in cache:
            runtime = (fluid_tags if kind == "fluid" else item_tags).get(tag_id)
            cache[key] = (
                set(runtime) if runtime else pack.static_tag_members(tag_id, kind)
            )
        return cache[key]

    def lookup(tag_id: str, category: Category) -> set[str]:
        if category is Category.LIQUID:
            return members(tag_id, "fluid") or members(tag_id, "item")
        return members(tag_id, "item")

    def tag_empty(tag_id: str) -> bool:
        return not members(tag_id, "item")

    def recipe_ok(data: dict[str, Any]) -> bool:
        rtype = data.get("type")
        if isinstance(rtype, str) and ":" in rtype:
            if not pack.mod_present(rtype.partition(":")[0]):
                return False  # the game can't load a type whose mod is absent
        return all(_condition_holds(c, pack, tag_empty) for c in _conditions_of(data))

    names: dict[str, str] = {}
    if export is not None:
        for key in ("itemNames", "fluidNames"):
            raw = export.get(key)
            if isinstance(raw, dict):
                names.update(
                    {
                        str(k): v
                        for k, v in raw.items()
                        if isinstance(v, str) and v and not _looks_like_key(v)
                    }
                )

    item_fuels: dict[str, float] = {}
    tag_fuels: dict[str, float] = {}
    for key, spec in pack.furnace_fuels.items():
        if not isinstance(spec, dict) or "burn_time" not in spec:
            continue
        try:
            eu = float(spec["burn_time"]) * _EU_PER_BURN_TICK
        except (TypeError, ValueError):
            continue
        if key.startswith("#"):
            t = key[1:]
            tag_fuels[t if ":" in t else f"c:{t}"] = eu
        else:
            item_fuels[key if ":" in key else f"minecraft:{key}"] = eu
    fluid_fuels: dict[str, float] = {}
    for fid, spec in pack.fluid_fuels.items():
        if isinstance(spec, dict) and "eu_per_mb" in spec:
            try:
                fluid_fuels[fid if ":" in fid else f"minecraft:{fid}"] = float(
                    spec["eu_per_mb"]
                )
            except (TypeError, ValueError):
                continue

    return RecipeSource(
        recipes=(
            (_game_to_recipe_id(rid), data)
            for rid, data in sorted(game_recipes.items())
        ),
        lang=pack.lang,
        names=names,
        fluid_fuels=fluid_fuels,
        item_fuels=item_fuels,
        tag_fuels=tag_fuels,
        tag_members=lookup,
        condition_ok=recipe_ok,
    )


def _looks_like_key(name: str) -> bool:
    """An untranslated key (``item.foo.bar``) — a dedicated server has no lang."""

    return bool(re.fullmatch(r"(item|block|fluid|fluid_type)\.[\w.]+", name))


def import_pack(
    path: str | Path,
    *,
    export_path: str | Path | None = None,
    machine_types: tuple[str, ...] | None = None,
    name: str | None = None,
    include_energy: bool = True,
) -> MiImport:
    """Import every machine recipe of the modpack instance at ``path``.

    Returns the usual :class:`MiImport`; ``stats`` adds ``pack`` (the instance
    name), ``mods`` (mod count), ``sourcesRead``, ``usedExport`` (whether the
    runtime export supplied the recipes), ``exportPath``, ``exportStale`` (the
    export predates a jar or script change — re-export in game), and
    ``exporterInstalled``.
    """

    pack = read_pack(path, export_path=export_path)
    result = build_import(
        pack_source(pack),
        machine_types=machine_types,
        name=name or pack.name,
        include_energy=include_energy,
    )
    result.stats.update(
        {
            "pack": pack.name,
            "gameDir": str(pack.game_dir),
            "mods": len(pack.mods),
            "sourcesRead": len(pack.sources),
            "baseJars": [p.name for p in pack.base_jars],
            "usedExport": pack.export is not None,
            "exportPath": str(pack.export_path or pack.game_dir / EXPORT_RELPATH),
            "exportStale": pack.export_stale,
            "exportedAt": (pack.export or {}).get("exportedAt"),
            "exporterInstalled": (pack.game_dir / EXPORTER_INSTALL_RELPATH).is_file(),
        }
    )
    return result


def install_exporter(path: str | Path) -> Path:
    """Copy the KubeJS exporter script into the instance; return where it went.

    Installs to ``local/kubejs/local_server_scripts/`` — KubeJS loads it like a
    server script, but it sits outside the pack's own ``kubejs/`` folder, so a
    pack update neither removes it nor conflicts with it. Requires KubeJS in the
    pack. Overwrites an older copy of the same script.
    """

    game_dir = find_game_dir(path)
    if not any(
        p.name.lower().startswith("kubejs") for p in (game_dir / "mods").glob("*.jar")
    ):
        raise MalformedFileError(
            "this pack has no KubeJS mod, so the runtime exporter can't run; "
            "the static import (jars + data) is the complete picture for it"
        )
    if not EXPORTER_SCRIPT.is_file():
        raise MalformedFileError(f"exporter script missing: {EXPORTER_SCRIPT}")
    dest = game_dir / EXPORTER_INSTALL_RELPATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(EXPORTER_SCRIPT.read_bytes())
    return dest

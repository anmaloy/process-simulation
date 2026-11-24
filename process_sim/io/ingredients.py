"""Normalize Minecraft / NeoForge ingredient JSON into a small expression tree.

A recipe input is rarely just ``{"item": id}``. Across a modpack it can be:

* a concrete id — ``{"item": …}``, ``{"fluid": …}``, ``{"id": …}`` or a bare
  string (``"#c:dusts/iron"`` is a tag);
* a tag — ``{"tag": …}`` (an item tag, or a fluid tag under ``neoforge:tag``);
* a vanilla **array** — ``[{…}, {…}]``, any one of which satisfies the slot;
* a NeoForge custom ingredient — ``neoforge:compound`` (any of ``children`` /
  ``ingredients``), ``neoforge:difference`` (``base`` minus ``subtracted``),
  ``neoforge:intersection`` (all of ``children``), ``neoforge:components``
  (``items`` / ``fluids`` carrying data components — read as the base ids);
* a NeoForge fluid ingredient — ``neoforge:single`` / ``neoforge:tag`` /
  ``neoforge:empty``;
* a sized wrapper — ``{"ingredient": {…}, "count": n}``.

:func:`parse_ingredient` turns any of these into an :class:`Ingredient` — a leaf
(``item`` / ``tag``) or a set expression (``union`` / ``diff`` / ``inter``) —
and :func:`is_fluid_ingredient` tells a fluid entry from an item one in a mixed
list (Create's ``ingredients``). A composite is resolved later to the concrete
member set (:func:`resolve_members`) once tag membership is known, so the
importer can model it as one tag-like item whose members are every accepted
input — which is exactly how the engine treats a tag.

Pure data logic, no model or file IO.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

__all__ = [
    "Ingredient",
    "parse_ingredient",
    "is_fluid_ingredient",
    "resolve_members",
    "entry_amount",
]

#: NeoForge fluid-ingredient types that only ever describe fluids.
_FLUID_TYPES = {"neoforge:single", "neoforge:tag", "neoforge:empty"}
_UNION_TYPES = {"neoforge:compound", "compound", "forge:compound"}
_DIFF_TYPES = {"neoforge:difference", "forge:difference"}
_INTER_TYPES = {"neoforge:intersection", "forge:intersection"}
_COMPONENT_TYPES = {"neoforge:components", "neoforge:nbt", "forge:nbt"}
#: Keys that carry the sized amount, not the ingredient itself.
_SIZE_KEYS = ("amount", "count")


@dataclass(frozen=True)
class Ingredient:
    """A leaf (``item`` / ``tag`` with ``id``) or a set expression over ``parts``.

    ``diff`` has exactly two parts (base, subtracted). Frozen and hashable so the
    same composite seen in many recipes maps to one synthetic item.
    """

    kind: str  # "item" | "tag" | "union" | "diff" | "inter"
    id: str | None = None
    parts: tuple[Ingredient, ...] = ()

    @property
    def is_leaf(self) -> bool:
        return self.kind in ("item", "tag")

    def canonical(self) -> str:
        """A stable, readable key: ``#c:dusts/nickel|#c:ingots/nickel``."""

        if self.kind == "item":
            return str(self.id)
        if self.kind == "tag":
            return f"#{self.id}"
        if self.kind == "union":
            return "|".join(sorted(p.canonical() for p in self.parts))
        if self.kind == "inter":
            return "&".join(sorted(_wrap(p) for p in self.parts))
        base, sub = self.parts
        return f"{_wrap(base)}-{_wrap(sub)}"


def _wrap(p: Ingredient) -> str:
    return p.canonical() if p.is_leaf else f"({p.canonical()})"


def _ns(raw: str, default_ns: str) -> str:
    return raw if ":" in raw else f"{default_ns}:{raw}"


def _union(parts: list[Ingredient]) -> Ingredient | None:
    """Flatten nested unions, drop duplicates; a single part stands alone."""

    flat: list[Ingredient] = []
    for p in parts:
        flat.extend(p.parts if p.kind == "union" else (p,))
    uniq = list(dict.fromkeys(flat))
    if not uniq:
        return None
    if len(uniq) == 1:
        return uniq[0]
    return Ingredient("union", parts=tuple(uniq))


def parse_ingredient(obj: Any, *, fluid: bool = False) -> Ingredient | None:
    """Parse one ingredient (any shape listed in the module doc).

    ``fluid`` picks the default namespace for a bare id (fluids and items both
    default to ``minecraft``; tags default to ``c``). Returns ``None`` for an
    empty / unreadable ingredient.
    """

    if isinstance(obj, str):
        if obj.startswith("#"):
            return Ingredient("tag", _ns(obj[1:], "c"))
        return Ingredient("item", _ns(obj, "minecraft")) if obj else None
    if isinstance(obj, list):
        return _union([p for p in (parse_ingredient(o, fluid=fluid) for o in obj) if p])
    if not isinstance(obj, dict):
        return None

    t = obj.get("type")
    if t in _UNION_TYPES:
        children = obj.get("children", obj.get("ingredients", []))
        return parse_ingredient(
            children if isinstance(children, list) else [], fluid=fluid
        )
    if t in _DIFF_TYPES:
        base = parse_ingredient(obj.get("base"), fluid=fluid)
        sub = parse_ingredient(obj.get("subtracted"), fluid=fluid)
        if base is None:
            return None
        return base if sub is None else Ingredient("diff", parts=(base, sub))
    if t in _INTER_TYPES:
        parts = [
            p
            for p in (parse_ingredient(c, fluid=fluid) for c in obj.get("children", []))
            if p
        ]
        if not parts:
            return None
        return parts[0] if len(parts) == 1 else Ingredient("inter", parts=tuple(parts))
    if t in _COMPONENT_TYPES:
        # A component-qualified stack (a specific potion fluid, an enchanted
        # book): the planner tracks the base id.
        inner = obj.get("items", obj.get("fluids", obj.get("item", obj.get("fluid"))))
        return parse_ingredient(inner, fluid=fluid)
    if t == "neoforge:empty":
        return None

    # A sized wrapper around a nested ingredient.
    nested = obj.get("ingredient")
    if isinstance(nested, (dict, list)) and not any(
        k in obj for k in ("item", "fluid", "tag", "id")
    ):
        return parse_ingredient(nested, fluid=fluid)

    if "tag" in obj and isinstance(obj["tag"], str):
        return Ingredient("tag", _ns(obj["tag"].lstrip("#"), "c"))
    if "fluid_tag" in obj and isinstance(obj["fluid_tag"], str):
        return Ingredient("tag", _ns(obj["fluid_tag"].lstrip("#"), "c"))
    for key in ("fluid", "item", "id"):
        v = obj.get(key)
        if isinstance(v, str) and v:
            if v.startswith("#"):
                return Ingredient("tag", _ns(v[1:], "c"))
            return Ingredient("item", _ns(v, "minecraft"))
        if isinstance(v, (dict, list)):  # {"fluid": {"tag": …}} and similar
            return parse_ingredient(v, fluid=fluid)
    # A fluid-ingredient list under "fluids" (neoforge:components without type).
    if "fluids" in obj:
        return parse_ingredient(obj["fluids"], fluid=True)
    return None


def is_fluid_ingredient(obj: Any) -> bool:
    """Is this entry of a *mixed* ingredient list a fluid (vs an item)?

    True for NeoForge fluid-ingredient types, a ``fluid`` / ``fluids`` /
    ``fluid_tag`` key, or a compound whose children are all fluids. Used only
    where one list holds both (Create-style ``ingredients``); MI-shaped recipes
    say the category by key (``fluid_inputs``).
    """

    if not isinstance(obj, dict):
        return False
    t = obj.get("type")
    if t in _FLUID_TYPES:
        return True
    if any(k in obj for k in ("fluid", "fluids", "fluid_tag")):
        return True
    if t in _UNION_TYPES:
        kids = obj.get("children", obj.get("ingredients"))
        return (
            isinstance(kids, list)
            and bool(kids)
            and all(is_fluid_ingredient(k) for k in kids)
        )
    if t in _DIFF_TYPES:
        return is_fluid_ingredient(obj.get("base"))
    return False


def entry_amount(obj: Any, default: float = 1.0) -> float:
    """The sized amount of an entry (``amount`` or ``count``), default ``1``."""

    if isinstance(obj, dict):
        for k in _SIZE_KEYS:
            v = obj.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return float(v)
    return default


def resolve_members(
    ing: Ingredient, tag_members: Callable[[str], set[str]]
) -> set[str]:
    """The concrete ids an ingredient accepts, given a tag → members lookup."""

    if ing.kind == "item":
        return {str(ing.id)}
    if ing.kind == "tag":
        return set(tag_members(str(ing.id)))
    sets = [resolve_members(p, tag_members) for p in ing.parts]
    if ing.kind == "union":
        return set().union(*sets)
    if ing.kind == "inter":
        out = sets[0]
        for s in sets[1:]:
            out = out & s
        return out
    return sets[0] - sets[1]  # diff

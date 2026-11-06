"""Fuel → fluid energy conversion — isolated.

This is the single home of the fuel-conversion math (resolved decision #3 and
the "Energy generation" section of ``CLAUDE.md``). Keep it here so any later
tweak to η / waste stays a local edit.

A fuel-burning machine converts an item's stored ``eu`` into an **output
fluid** — a liquid item that then flows through pipes under the normal
movement-capacity and priority rules. The engine deals only in the energy
throughput; *which* fluid is produced, and its energy density, are content (an
``Item`` with its own ``eu``), never a concept the engine names. In default
content the fluid is steam at 1 EU per unit, so the energy-equivalent rates
below map 1:1 to its volume — but nothing here assumes that.

With max rate ``R``, fluid actually drawn ``D`` (capped at ``R``), and waste
fraction ``w`` (the machine's ``efficiency``, default 0 = lossless):

    fluidOut   = D
    fuelDrain  = D + w·(R − D)        # = w·R + (1−w)·D, well-defined at D = 0
    η          = D / fuelDrain        # 1 when nothing is drained losslessly
    wasted     = w·(R − D)

Given one fuel unit's stored energy ``E``:

    usableOutput = E · η
    burnTime     = E · η / D          # how long one fuel unit lasts

All rates are per second; the tick form scales identically.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["FuelConversion", "convert_fuel", "usable_output", "burn_time"]


@dataclass(frozen=True)
class FuelConversion:
    """The steady-state result of burning fuel for its output fluid at a draw.

    ``max_rate`` ``R``, ``draw`` ``D`` (already capped to ``[0, R]``), ``waste``
    ``w``; ``fluid_out`` output rate, ``fuel_drain`` EU/s of fuel consumed,
    ``efficiency`` ``η``, and ``wasted`` EU/s of heat lost. Rates are in the
    fuel's energy units; content maps them to the output fluid's volume by that
    fluid's energy density.
    """

    max_rate: float
    draw: float
    waste: float
    fluid_out: float
    fuel_drain: float
    efficiency: float
    wasted: float

    def to_dict(self) -> dict[str, float]:
        return {
            "maxRate": self.max_rate,
            "draw": self.draw,
            "waste": self.waste,
            "fluidOut": self.fluid_out,
            "fuelDrain": self.fuel_drain,
            "efficiency": self.efficiency,
            "wasted": self.wasted,
        }


def convert_fuel(
    max_rate: float, draw: float | None = None, waste: float = 0.0
) -> FuelConversion:
    """Compute output fluid, fuel drain, efficiency, and waste.

    ``draw`` is clamped to ``[0, max_rate]``; ``None`` means full draw
    (``D = R``). ``waste`` ``w`` defaults to 0 (lossless, η = 1). A lossy
    converter (e.g. a boiler at ``w = 0.8``, ``R = 256`` in default content)
    burns ``w·R`` fuel even at zero draw and loses ``w`` of any undrawn surplus,
    so η reaches 1 only at full load.
    """

    r = max_rate
    w = waste or 0.0
    d = r if draw is None else draw
    d = max(0.0, min(d, r))

    fuel_drain = d + w * (r - d)  # = w·R + (1−w)·D
    wasted = w * (r - d)
    # η = D / fuelDrain, with the lossless D = 0 case (fuelDrain = 0) defined as 1.
    efficiency = (d / fuel_drain) if fuel_drain > 0 else 1.0

    return FuelConversion(
        max_rate=r,
        draw=d,
        waste=w,
        fluid_out=d,
        fuel_drain=fuel_drain,
        efficiency=efficiency,
        wasted=wasted,
    )


def usable_output(fuel_eu: float, efficiency: float) -> float:
    """Total output one fuel unit of energy ``E`` yields: ``E · η``."""

    return fuel_eu * efficiency


def burn_time(fuel_eu: float, efficiency: float, draw: float) -> float | None:
    """How long one fuel unit lasts: ``E · η / D``. ``None`` at zero draw."""

    if draw <= 0:
        return None
    return (fuel_eu * efficiency) / draw

// Display-unit auto-scaling (WORKFLOW.md P8, resolved decision #5: "store
// canonical units internally; the UI auto-scales values for readability"). The
// engine reports canonical per-second rates; these helpers scale a magnitude to
// a short, readable string with an SI-style suffix. This is the single seam, so
// a later unit tweak stays local.

const SUFFIXES: { threshold: number; divisor: number; suffix: string }[] = [
  { threshold: 1e9, divisor: 1e9, suffix: "G" },
  { threshold: 1e6, divisor: 1e6, suffix: "M" },
  { threshold: 1e3, divisor: 1e3, suffix: "k" },
];

/** Round to at most `sig` significant figures, dropping trailing zeros. */
function toSig(value: number, sig = 3): string {
  if (value === 0) return "0";
  const rounded = Number(value.toPrecision(sig));
  // Avoid scientific notation for the small values we deal with; trim zeros.
  return rounded.toString();
}

/**
 * Scale a non-negative magnitude to `{ value, suffix }`: thousands collapse to
 * k/M/G, sub-unit values keep up to three significant figures. The caller
 * appends the unit (e.g. "/s" for a rate, " EU/s" for energy).
 */
export function scaleMagnitude(magnitude: number): { value: string; suffix: string } {
  const abs = Math.abs(magnitude);
  for (const { threshold, divisor, suffix } of SUFFIXES) {
    if (abs >= threshold) return { value: toSig(magnitude / divisor), suffix };
  }
  return { value: toSig(magnitude), suffix: "" };
}

/** Format a per-second rate, e.g. `1250 → "1.25k/s"`, `0.25 → "0.25/s"`. */
export function formatRate(rate: number): string {
  const { value, suffix } = scaleMagnitude(rate);
  return `${value}${suffix}/s`;
}

import type { Category } from "../bridge";

/** Simulation ticks per second — energy is measured per tick (EU/t). */
export const TICKS_PER_SECOND = 20;

/** Canonical (per-second) energy → the per-tick value the UI edits/shows. */
export const toPerTick = (perSecond: number): number => perSecond / TICKS_PER_SECOND;
/** A per-tick value the user typed → the canonical per-second value stored. */
export const toPerSecond = (perTick: number): number => perTick * TICKS_PER_SECOND;

/**
 * Format a per-second rate with its category unit. Materials read per second
 * (mB/s for liquids, /s for solids); energy reads per tick (EU/t, 20 ticks/s),
 * so the two bases stay convertible.
 */
export function formatRateFor(category: Category | undefined, rate: number): string {
  if (category === "energy") {
    const { value, suffix } = scaleMagnitude(toPerTick(rate));
    return `${value}${suffix} EU/t`;
  }
  const { value, suffix } = scaleMagnitude(rate);
  const unit = category === "liquid" ? " mB" : "";
  return `${value}${suffix}${unit}/s`;
}

/** Format an energy balance in EU/t, keeping an explicit sign for net energy. */
export function formatEnergy(euPerSecond: number): string {
  const perTick = toPerTick(euPerSecond);
  const sign = perTick > 0 ? "+" : perTick < 0 ? "−" : "";
  const { value, suffix } = scaleMagnitude(Math.abs(perTick));
  return `${sign}${value}${suffix} EU/t`;
}

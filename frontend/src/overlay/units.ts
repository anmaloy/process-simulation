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

/** Format an energy balance in EU/s, keeping an explicit sign for net energy. */
export function formatEnergy(eu: number): string {
  const sign = eu > 0 ? "+" : eu < 0 ? "−" : "";
  const { value, suffix } = scaleMagnitude(Math.abs(eu));
  return `${sign}${value}${suffix} EU/s`;
}

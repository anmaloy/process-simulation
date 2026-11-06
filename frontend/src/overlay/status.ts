// Balance-status color map (WORKFLOW.md P8) — the single source for the status
// phosphors, read by the node view, the edges, the legend, and the readout, so
// the decided mapping stays a one-line edit (the decision's "read them from a
// single color-map constant").
//
//   ok                 → green  (--s-ref)
//   shortfall          → red    (--s-bad, added for this app)
//   overflow           → yellow (--s-learn)
//   stalled/unpowered  → grey   (--s-not)
//
// Cyan (--spine) stays reserved for the selected/live element; the category
// colors (colors.ts) stay reserved for edges/ports by item category.

import type { Status } from "../bridge";

export const STATUS_COLOR: Record<Status, string> = {
  ok: "var(--s-ref)",
  shortfall: "var(--s-bad)",
  overflow: "var(--s-learn)",
  stalled: "var(--s-not)",
};

/** Human label for a status, for the legend and the readout. */
export const STATUS_LABEL: Record<Status, string> = {
  ok: "ok",
  shortfall: "shortfall",
  overflow: "overflow",
  stalled: "stalled",
};

/** Status fill token (darker twin) — a faint tinted surface behind a chip. */
export const STATUS_FILL: Record<Status, string> = {
  ok: "var(--f-ref)",
  shortfall: "var(--f-bad)",
  overflow: "var(--f-learn)",
  stalled: "var(--f-not)",
};

/** Order statuses render in a legend (worst-to-neutral is not implied). */
export const STATUS_ORDER: Status[] = ["ok", "shortfall", "overflow", "stalled"];

/** Resolve a (possibly absent) status to its color; absent reads neutral grey. */
export function statusColor(status: Status | undefined): string {
  return STATUS_COLOR[status ?? "stalled"];
}

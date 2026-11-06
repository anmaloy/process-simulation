// Category color map — the single source for edge/port/node-accent colors by
// item category (WORKFLOW.md P6). Read from here, never hardcode a hue per
// element, so the "default, adjustable" assignment stays a one-line edit.
//
//   solid  → silver (--s-teach)
//   liquid → blue   (--s-indep)
//   energy → gold   (--subedge)
//
// A converter's output fluid is a liquid item, so it rides the liquid color by
// virtue of its category — no special case here.
//
// Cyan (--spine) stays reserved for the selected/live element; the status
// phosphors (green/red/yellow/grey) stay reserved for balance state (P8).

import type { Category } from "../bridge";

export const CATEGORY_COLOR: Record<Category, string> = {
  solid: "var(--s-teach)",
  liquid: "var(--s-indep)",
  energy: "var(--subedge)",
};

/** Resolve a category to its token color, falling back to a faint ink. */
export function categoryColor(category: Category): string {
  return CATEGORY_COLOR[category] ?? "var(--ink-faint)";
}

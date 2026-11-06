// Canvas legends (WORKFLOW.md P8) — the category key (what an edge/port color
// means) and the balance-status key (what a node/edge status color means),
// stacked top-right of the stage. Both read from the single color maps
// (colors.ts, status.ts); neither introduces a hue.

import type { Category } from "../bridge";
import { categoryColor } from "../canvas/colors";
import { STATUS_COLOR, STATUS_LABEL, STATUS_ORDER } from "./status";

const CATEGORIES: Category[] = ["solid", "liquid", "energy"];

export function Legends() {
  return (
    <div className="legend-stack">
      <div className="legend-card">
        <span className="eyebrow quiet legend-title">Category</span>
        <div className="legend-items">
          {CATEGORIES.map((c) => (
            <span className="legend-item mono" key={c}>
              <span className="legend-dot" style={{ background: categoryColor(c) }} />
              {c}
            </span>
          ))}
        </div>
      </div>
      <div className="legend-card">
        <span className="eyebrow quiet legend-title">Status</span>
        <div className="legend-items">
          {STATUS_ORDER.map((s) => (
            <span className="legend-item mono" key={s}>
              <span
                className="legend-dot round"
                style={{ background: STATUS_COLOR[s] }}
              />
              {STATUS_LABEL[s]}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

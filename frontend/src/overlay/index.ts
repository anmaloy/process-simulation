// Balance overlay (WORKFLOW.md P8) — the legible layer over the engine's
// output: status colors on the canvas, the category + status legends, and the
// per-scope readout with its scope switcher.

export { Legends } from "./Legends";
export { BalanceReadout, type BalanceReadoutProps } from "./BalanceReadout";
export {
  STATUS_COLOR,
  STATUS_FILL,
  STATUS_LABEL,
  STATUS_ORDER,
  statusColor,
} from "./status";
export { formatEnergy, formatRate, scaleMagnitude } from "./units";

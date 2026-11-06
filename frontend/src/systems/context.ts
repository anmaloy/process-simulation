// Actions a custom system node triggers on the host (WORKFLOW.md P9). A collapsed
// system node lives inside React Flow's render tree, so it reaches the host
// through this context rather than through node data.

import { createContext } from "react";

export interface SystemActions {
  /** Expand (uncollapse) the system with this id. */
  onExpand: (systemId: string) => void;
}

export const SystemActionsContext = createContext<SystemActions>({
  onExpand: () => {},
});

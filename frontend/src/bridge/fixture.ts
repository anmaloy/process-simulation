// Dev fallback fixture — a real World so the frontend runs in a plain browser
// (`npm run dev`) with no Python host (WORKFLOW.md P5). It mirrors the backend's
// `tests/fixtures/furnace_chest.json` acceptance scenario, so the UI shows the
// same data the engine tests exercise.

import type { World } from "./types";

export const DEV_WORLD: World = {
  name: "Furnace → Chest (dev fixture)",
  items: [
    { id: "ore", name: "Iron Ore", category: "solid" },
    { id: "ingot", name: "Iron Ingot", category: "solid" },
  ],
  recipes: [
    {
      id: "smelt",
      name: "Smelt Iron",
      timeSeconds: 2,
      inputs: [{ itemId: "ore", quantity: 1 }],
      outputs: [{ itemId: "ingot", quantity: 1 }],
      energyConsumption: 30,
    },
  ],
  machineDefs: [
    {
      id: "furnace",
      name: "Electric Furnace",
      baseEnergyConsumption: 20,
      allowedRecipeIds: ["smelt"],
      slots: [
        { category: "solid", direction: "in", slots: 1, stackSize: 64 },
        { category: "solid", direction: "out", slots: 1, stackSize: 64 },
      ],
    },
    {
      id: "chest",
      name: "Chest",
      slots: [{ category: "solid", direction: "in", slots: 9, stackSize: 64 }],
    },
  ],
  nodes: [
    {
      id: "furnace1",
      machineDefId: "furnace",
      recipeId: "smelt",
      overclock: 1,
      count: 1,
    },
    { id: "chest1", machineDefId: "chest" },
  ],
  pipes: [
    {
      id: "p1",
      category: "solid",
      movementCapacity: 10,
      a: { nodeId: "furnace1", role: "out", extractPriority: 0 },
      b: { nodeId: "chest1", role: "in", insertPriority: 0 },
    },
  ],
  systems: [],
};

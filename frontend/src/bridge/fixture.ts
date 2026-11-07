// Dev fallback fixture — a real World so the frontend runs in a plain browser
// (`npm run dev`) with no Python host. It exercises the carrier-tiers energy
// model: coal → (boiler) → steam → (turbine) → electricity → (furnace), plus a
// steam buffer and a chest, so the inspectors and overlay have something to show.

import type { World } from "./types";

export const DEV_WORLD: World = {
  name: "Steam Power (dev fixture)",
  items: [
    { id: "ore", name: "Iron Ore", category: "solid" },
    { id: "ingot", name: "Iron Ingot", category: "solid" },
    { id: "coal", name: "Coal", category: "solid", eu: 1600 },
    { id: "water", name: "Water", category: "liquid" },
    { id: "steam", name: "Steam", category: "liquid" },
    { id: "electricity", name: "Electricity", category: "energy" },
  ],
  recipes: [
    {
      id: "smelt_iron",
      name: "Smelt Iron",
      timeSeconds: 2,
      inputs: [{ itemId: "ore", quantity: 1 }],
      outputs: [{ itemId: "ingot", quantity: 1 }],
    },
  ],
  machineDefs: [
    {
      id: "boiler",
      name: "Large Steam Boiler",
      energySource: "fuel",
      sourceItemId: "coal",
      inputItemId: "water",
      outputItemId: "steam",
      efficiency: 0.8,
      maxFluidRate: 256,
      materialRatio: 16,
      slots: [
        { category: "solid", direction: "in", slots: 1, stackSize: 64 },
        { category: "liquid", direction: "in", slots: 1, stackSize: 16000 },
        { category: "liquid", direction: "out", slots: 1, stackSize: 16000 },
      ],
    },
    {
      id: "turbine",
      name: "Steam Turbine",
      energySource: "liquid",
      sourceItemId: "steam",
      outputItemId: "electricity",
      maxFluidRate: 256,
      slots: [
        { category: "liquid", direction: "in", slots: 1, stackSize: 16000 },
        { category: "energy", direction: "out", slots: 1, stackSize: 1 },
      ],
    },
    {
      id: "electric_furnace",
      name: "Electric Furnace",
      energySource: "electric",
      sourceItemId: "electricity",
      baseEnergyConsumption: 30,
      allowedRecipeIds: ["smelt_iron"],
      slots: [
        { category: "solid", direction: "in", slots: 1, stackSize: 64 },
        { category: "energy", direction: "in", slots: 1, stackSize: 1 },
        { category: "solid", direction: "out", slots: 1, stackSize: 64 },
      ],
    },
    {
      id: "steam_tank",
      name: "Steam Tank",
      storage: { category: "liquid", capacity: 64000 },
      slots: [
        { category: "liquid", direction: "in", slots: 1, stackSize: 32000 },
        { category: "liquid", direction: "out", slots: 1, stackSize: 32000 },
      ],
    },
    {
      id: "chest",
      name: "Chest",
      slots: [{ category: "solid", direction: "in", slots: 9, stackSize: 64 }],
    },
  ],
  nodes: [
    // The boiler (coal) and furnace (ore) sit at the system boundary — their
    // feedstock is inserted by hand, so they're flagged manual insertion.
    { id: "boiler_1", machineDefId: "boiler", assumeInputs: true, x: -120, y: 0 },
    { id: "steam_tank_1", machineDefId: "steam_tank", x: 140, y: 0 },
    { id: "turbine_1", machineDefId: "turbine", x: 400, y: 0 },
    { id: "electric_furnace_1", machineDefId: "electric_furnace", recipeId: "smelt_iron", assumeInputs: true, x: 400, y: 200 },
    { id: "chest_1", machineDefId: "chest", x: 680, y: 200 },
  ],
  pipes: [
    {
      id: "pipe_steam_1",
      category: "liquid",
      movementCapacity: 1000000000,
      a: { nodeId: "boiler_1", role: "out", extractPriority: 0 },
      b: { nodeId: "steam_tank_1", role: "in", insertPriority: 0 },
    },
    {
      id: "pipe_steam_2",
      category: "liquid",
      movementCapacity: 1000000000,
      a: { nodeId: "steam_tank_1", role: "out", extractPriority: 0 },
      b: { nodeId: "turbine_1", role: "in", insertPriority: 0 },
    },
    {
      id: "pipe_power_1",
      category: "energy",
      movementCapacity: 5120,
      a: { nodeId: "turbine_1", role: "out", extractPriority: 0 },
      b: { nodeId: "electric_furnace_1", role: "in", insertPriority: 0 },
    },
    {
      id: "pipe_ingot_1",
      category: "solid",
      movementCapacity: 16,
      a: { nodeId: "electric_furnace_1", role: "out", extractPriority: 0 },
      b: { nodeId: "chest_1", role: "in", insertPriority: 0 },
    },
  ],
  systems: [],
};

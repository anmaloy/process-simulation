// Wire types — the JSON shapes that cross the pywebview bridge, mirroring the
// backend model's camelCase serialization and the P4 api.py contract (see
// CLAUDE.md "API surface"). These are the frozen shapes the P5 client is typed
// against; a change here tracks a documented contract bump.
//
// Sparse optional fields are `?` and mean *absent* (never 0) — the same
// "unset ≠ zero" discipline the Python model enforces.

export type Category = "solid" | "liquid" | "energy";
export type Direction = "in" | "out";
export type PipeEndRole = "in" | "out" | "io";

export interface Item {
  id: string;
  name: string;
  category: Category;
  eu?: number;
}

export interface Stack {
  itemId: string;
  quantity: number;
}

export interface Recipe {
  id: string;
  timeSeconds: number;
  name?: string;
  inputs?: Stack[];
  outputs?: Stack[];
  energyConsumption?: number;
  energyProduction?: number;
}

export interface SlotGroup {
  category: Category;
  direction: Direction;
  slots: number;
  stackSize: number;
}

export interface MachineDef {
  id: string;
  name?: string;
  baseEnergyConsumption?: number;
  baseEnergyProduction?: number;
  fuelBurning?: boolean;
  efficiency?: number;
  maxFluidRate?: number;
  allowedRecipeIds?: string[];
  slots?: SlotGroup[];
  overclockMin?: number;
  overclockMax?: number;
}

export interface MachineNode {
  id: string;
  machineDefId: string;
  recipeId?: string;
  overclock?: number;
  count?: number;
  x?: number;
  y?: number;
}

export interface PipeEnd {
  nodeId: string;
  role: PipeEndRole;
  port?: string;
  insertPriority?: number;
  extractPriority?: number;
}

export interface Pipe {
  id: string;
  category: Category;
  a: PipeEnd;
  b: PipeEnd;
  movementCapacity: number;
  storageCapacity?: number;
  overclock?: number;
}

export interface SystemPort {
  nodeId: string;
  port?: string;
  name?: string;
}

export interface System {
  id: string;
  name?: string;
  memberNodeIds?: string[];
  memberSystemIds?: string[];
  exposedPorts?: SystemPort[];
  collapsed?: boolean;
}

export interface World {
  name?: string;
  items?: Item[];
  recipes?: Recipe[];
  machineDefs?: MachineDef[];
  nodes?: MachineNode[];
  pipes?: Pipe[];
  systems?: System[];
}

// --- Engine output (ScopeReport) ----------------------------------------- //

export type Status = "ok" | "shortfall" | "overflow" | "stalled";

export interface InputBalance {
  itemId: string;
  demand: number;
  supplied: number;
  shortfall: number;
  status: Status;
}

export interface OutputBalance {
  itemId: string;
  rate: number;
  demanded: number;
  overflow: number;
  status: Status;
}

export interface NodeBalance {
  nodeId: string;
  inputs: Record<string, InputBalance>;
  outputs: Record<string, OutputBalance>;
  status: Status;
}

export interface Bottleneck {
  nodeId: string;
  itemId: string;
  shortfall: number;
}

/** Resolved steady-state flow on one in-scope pipe (P8 overlay). */
export interface PipeFlow {
  pipeId: string;
  flow: number;
  shortfall: boolean;
}

export interface ScopeReport {
  nodeIds: string[];
  externalInputs: Record<string, number>;
  externalOutputs: Record<string, number>;
  netEnergy?: number;
  balances: Record<string, NodeBalance>;
  bottlenecks: Bottleneck[];
  /** Per-pipe flow + dashed-edge flag, keyed by pipe id (P8). */
  pipeFlows?: Record<string, PipeFlow>;
}

// --- IO / library --------------------------------------------------------- //

export interface LibraryEnvelope {
  schemaVersion: number;
  kind: "library";
  name?: string;
  items: Item[];
  recipes: Recipe[];
  machineDefs: MachineDef[];
}

export interface ImportResult {
  world: World;
  idRemap: Record<string, string>;
  skippedIds: string[];
}

// --- Request shapes ------------------------------------------------------- //

export type EntityKind =
  | "item"
  | "recipe"
  | "machineDef"
  | "node"
  | "pipe"
  | "system";

export type Edit =
  | { op: "upsert"; kind: EntityKind; entity: Record<string, unknown> }
  | { op: "remove"; kind: EntityKind; id: string };

export type Scope = "world" | "node" | "process" | "system";

export interface RecomputeRequest {
  scope: Scope;
  nodeId?: string;
  systemId?: string;
}

export interface ExportLibraryOptions {
  name?: string;
  itemIds?: string[];
  recipeIds?: string[];
  machineDefIds?: string[];
}

export type ConflictPolicy = "rename" | "skip" | "error";

// --- Result envelope ------------------------------------------------------ //

export interface ApiError {
  type: string;
  message: string;
}

export type Envelope<T> =
  | { ok: true; data: T }
  | { ok: false; error: ApiError };

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
/** How a machine is powered while it runs (replaces the fuel-burning flag). */
export type EnergySource = "electric" | "liquid" | "fuel";

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
}

export interface SlotGroup {
  category: Category;
  direction: Direction;
  slots: number;
  stackSize: number;
}

/** A buffer's capacity for one category — makes its def a storage node. */
export interface StorageSpec {
  category: Category;
  capacity: number;
}

export interface MachineDef {
  id: string;
  name?: string;
  /** How the machine is powered; absent = passive. */
  energySource?: EnergySource;
  /** The carrier item consumed (a fuel item, or a liquid like steam). */
  sourceItemId?: string;
  /** EU/s drawn while running (a plain consumer). */
  baseEnergyConsumption?: number;
  /** A converter's material input (e.g. water) that becomes the output. */
  inputItemId?: string;
  /** A converter's output carrier (steam, or electricity). */
  outputItemId?: string;
  efficiency?: number;
  maxFluidRate?: number;
  /** Set to make this def a storage buffer. */
  storage?: StorageSpec;
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
  x?: number;
  y?: number;
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
  /** Outside feedstock the running scope consumes (no internal producer). */
  externalInputs: Record<string, number>;
  /** Actual yield leaving the scope (effective, after duty throttling). */
  externalOutputs: Record<string, number>;
  netEnergy?: number;
  balances: Record<string, NodeBalance>;
  bottlenecks: Bottleneck[];
  /** Per-pipe flow + dashed-edge flag, keyed by pipe id (P8). */
  pipeFlows?: Record<string, PipeFlow>;
  /** Per-node running fraction in [0,1]; <1 = throttled by a starved input. */
  nodeDuty?: Record<string, number>;
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

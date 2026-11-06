// The raw bridge surface — one method per api.py method, each returning the
// uniform result envelope exactly as the Python side does. `window.pywebview.api`
// is duck-typed to this interface in the webview; `DevApi` implements it in JS
// for a plain browser. The typed `ApiClient` (client.ts) wraps either one and
// unwraps the envelope.

import { DEV_WORLD } from "./fixture";
import type {
  ConflictPolicy,
  Edit,
  Envelope,
  ExportLibraryOptions,
  ImportResult,
  LibraryEnvelope,
  RecomputeRequest,
  ScopeReport,
  World,
} from "./types";

export interface RawApi {
  get_world(): Promise<Envelope<World>>;
  set_world(world: World): Promise<Envelope<World>>;
  apply_edit(edit: Edit): Promise<Envelope<World>>;
  load_project(path: string): Promise<Envelope<World>>;
  save_project(path: string): Promise<Envelope<{ path: string }>>;
  recompute(request?: RecomputeRequest): Promise<Envelope<ScopeReport>>;
  export_library(
    options?: ExportLibraryOptions,
  ): Promise<Envelope<LibraryEnvelope>>;
  save_library(
    path: string,
    options?: ExportLibraryOptions,
  ): Promise<Envelope<{ path: string }>>;
  load_library(path: string): Promise<Envelope<LibraryEnvelope>>;
  import_library(
    library: LibraryEnvelope,
    on_conflict?: ConflictPolicy,
  ): Promise<Envelope<ImportResult>>;
  import_library_file(
    path: string,
    on_conflict?: ConflictPolicy,
  ): Promise<Envelope<ImportResult>>;
}

const SCHEMA_VERSION = 1;

function ok<T>(data: T): Envelope<T> {
  return { ok: true, data };
}

function err<T>(type: string, message: string): Envelope<T> {
  return { ok: false, error: { type, message } };
}

const KIND_FIELD = {
  item: "items",
  recipe: "recipes",
  machineDef: "machineDefs",
  node: "nodes",
  pipe: "pipes",
  system: "systems",
} as const;

/**
 * In-browser stand-in for the Python host. It keeps a live World in memory so
 * World-state calls (get/set/edit/load) round-trip for real; engine- and
 * file-backed calls return a clearly-marked dev result, since the rate engine
 * and the filesystem live in the Python host. The UI flags the "dev fixture"
 * source so these stand-ins are never mistaken for authoritative analysis.
 */
export class DevApi implements RawApi {
  private world: World;

  constructor(world: World = DEV_WORLD) {
    this.world = structuredClone(world);
  }

  async get_world(): Promise<Envelope<World>> {
    return ok(structuredClone(this.world));
  }

  async set_world(world: World): Promise<Envelope<World>> {
    this.world = structuredClone(world);
    return ok(structuredClone(this.world));
  }

  async apply_edit(edit: Edit): Promise<Envelope<World>> {
    const field = KIND_FIELD[edit.kind];
    if (!field) return err("ValueError", `unknown entity kind: ${edit.kind}`);
    const list = ((this.world[field] as { id: string }[] | undefined) ??
      []) as { id: string }[];
    if (edit.op === "upsert") {
      const entity = edit.entity as { id: string };
      const i = list.findIndex((e) => e.id === entity.id);
      if (i >= 0) list[i] = entity;
      else list.push(entity);
    } else if (edit.op === "remove") {
      const next = list.filter((e) => e.id !== edit.id);
      list.length = 0;
      list.push(...next);
    } else {
      return err("ValueError", `unknown edit op`);
    }
    (this.world[field] as unknown) = list;
    return ok(structuredClone(this.world));
  }

  // No filesystem in the browser: `load_project` returns the in-memory World so
  // the load-World round-trip still works against the dev fixture.
  async load_project(): Promise<Envelope<World>> {
    return ok(structuredClone(this.world));
  }

  async save_project(path: string): Promise<Envelope<{ path: string }>> {
    return ok({ path });
  }

  async recompute(
    request: RecomputeRequest = { scope: "world" },
  ): Promise<Envelope<ScopeReport>> {
    // The engine runs in the Python host; in dev we only resolve which nodes
    // the scope covers and return an empty-balance report.
    const all = (this.world.nodes ?? []).map((n) => n.id);
    let nodeIds = all;
    if (request.scope === "node" && request.nodeId) {
      nodeIds = all.filter((id) => id === request.nodeId);
    } else if (request.scope === "system" && request.systemId) {
      const sys = (this.world.systems ?? []).find(
        (s) => s.id === request.systemId,
      );
      nodeIds = sys?.memberNodeIds ?? [];
    }
    return ok({
      nodeIds,
      externalInputs: {},
      externalOutputs: {},
      balances: {},
      bottlenecks: [],
      pipeFlows: {},
    });
  }

  async export_library(
    options: ExportLibraryOptions = {},
  ): Promise<Envelope<LibraryEnvelope>> {
    const pick = <T extends { id: string }>(
      all: T[] | undefined,
      ids?: string[],
    ): T[] => {
      const src = all ?? [];
      if (!ids) return structuredClone(src);
      const wanted = new Set(ids);
      return structuredClone(src.filter((e) => wanted.has(e.id)));
    };
    return ok({
      schemaVersion: SCHEMA_VERSION,
      kind: "library",
      name: options.name ?? this.world.name,
      items: pick(this.world.items, options.itemIds),
      recipes: pick(this.world.recipes, options.recipeIds),
      machineDefs: pick(this.world.machineDefs, options.machineDefIds),
    });
  }

  async save_library(
    path: string,
  ): Promise<Envelope<{ path: string }>> {
    return ok({ path });
  }

  async load_library(): Promise<Envelope<LibraryEnvelope>> {
    return err(
      "ProcessSimIOError",
      "loading a library file needs the Python host (unavailable in the dev browser)",
    );
  }

  async import_library(): Promise<Envelope<ImportResult>> {
    return err(
      "ProcessSimIOError",
      "library import runs in the Python host (unavailable in the dev browser)",
    );
  }

  async import_library_file(): Promise<Envelope<ImportResult>> {
    return err(
      "ProcessSimIOError",
      "library import runs in the Python host (unavailable in the dev browser)",
    );
  }
}

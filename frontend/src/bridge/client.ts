// Typed API client over the pywebview bridge (WORKFLOW.md P5).
//
// `ApiClient` is what the rest of the frontend uses. It resolves the raw
// surface once — `window.pywebview.api` in the webview, a `DevApi` fallback in a
// plain browser — then exposes typed methods that unwrap the result envelope:
// success returns the payload, an expected failure throws `ApiCallError`
// carrying the Python error's type and message. The method shapes mirror
// api.py exactly (the frozen P4 contract).

import { DevApi, type RawApi } from "./rawApi";
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

export type ApiSource = "pywebview" | "dev";

/** A failure the Python side reported as `{ok: false, error: {...}}`. */
export class ApiCallError extends Error {
  readonly type: string;
  constructor(type: string, message: string) {
    super(message);
    this.name = "ApiCallError";
    this.type = type;
  }
}

declare global {
  interface Window {
    pywebview?: { api?: RawApi };
  }
}

/**
 * Resolve the raw bridge. pywebview injects `window.pywebview.api`
 * asynchronously and fires `pywebviewready`; in a plain browser that never
 * happens, so after a short wait we fall back to the dev fixture.
 */
export async function resolveRawApi(
  timeoutMs = 1500,
): Promise<{ api: RawApi; source: ApiSource }> {
  if (window.pywebview?.api) {
    return { api: window.pywebview.api, source: "pywebview" };
  }
  const ready = await waitForPywebview(timeoutMs);
  if (ready && window.pywebview?.api) {
    return { api: window.pywebview.api, source: "pywebview" };
  }
  return { api: new DevApi(), source: "dev" };
}

function waitForPywebview(timeoutMs: number): Promise<boolean> {
  return new Promise((resolve) => {
    let done = false;
    const finish = (value: boolean) => {
      if (done) return;
      done = true;
      window.removeEventListener("pywebviewready", onReady);
      clearInterval(poll);
      clearTimeout(timer);
      resolve(value);
    };
    const onReady = () => finish(true);
    window.addEventListener("pywebviewready", onReady);
    // Poll too: the event may have fired before this listener attached.
    const poll = setInterval(() => {
      if (window.pywebview?.api) finish(true);
    }, 50);
    const timer = setTimeout(() => finish(false), timeoutMs);
  });
}

function unwrap<T>(env: Envelope<T>): T {
  if (env.ok) return env.data;
  throw new ApiCallError(env.error.type, env.error.message);
}

export class ApiClient {
  private constructor(
    private readonly raw: RawApi,
    readonly source: ApiSource,
  ) {}

  /** Resolve the bridge and build a client. Call once at app start. */
  static async connect(): Promise<ApiClient> {
    const { api, source } = await resolveRawApi();
    return new ApiClient(api, source);
  }

  /** Build a client over an explicit raw surface (used by tests). */
  static over(raw: RawApi, source: ApiSource = "dev"): ApiClient {
    return new ApiClient(raw, source);
  }

  async getWorld(): Promise<World> {
    return unwrap(await this.raw.get_world());
  }

  async setWorld(world: World): Promise<World> {
    return unwrap(await this.raw.set_world(world));
  }

  async applyEdit(edit: Edit): Promise<World> {
    return unwrap(await this.raw.apply_edit(edit));
  }

  async loadProject(path: string): Promise<World> {
    return unwrap(await this.raw.load_project(path));
  }

  async saveProject(path: string): Promise<{ path: string }> {
    return unwrap(await this.raw.save_project(path));
  }

  async recompute(request: RecomputeRequest = { scope: "world" }): Promise<ScopeReport> {
    return unwrap(await this.raw.recompute(request));
  }

  async exportLibrary(
    options: ExportLibraryOptions = {},
  ): Promise<LibraryEnvelope> {
    return unwrap(await this.raw.export_library(options));
  }

  async saveLibrary(
    path: string,
    options: ExportLibraryOptions = {},
  ): Promise<{ path: string }> {
    return unwrap(await this.raw.save_library(path, options));
  }

  async loadLibrary(path: string): Promise<LibraryEnvelope> {
    return unwrap(await this.raw.load_library(path));
  }

  async importLibrary(
    library: LibraryEnvelope,
    onConflict: ConflictPolicy = "rename",
  ): Promise<ImportResult> {
    return unwrap(await this.raw.import_library(library, onConflict));
  }

  async importLibraryFile(
    path: string,
    onConflict: ConflictPolicy = "rename",
  ): Promise<ImportResult> {
    return unwrap(await this.raw.import_library_file(path, onConflict));
  }
}

import {
  VidKrakenConnectionError,
  VidKrakenError,
  VidKrakenJobFailedError,
  VidKrakenWaitTimeoutError,
  defaultCodeForStatus,
  type JobKind,
} from "./errors.js";
import { parseRateLimitHeaders, parseRetryAfter } from "./ratelimit.js";
import { saveFile, type SaveFileOptions, type SavedFile } from "./save.js";
import type {
  AccountResponse,
  ApiErrorBody,
  DownloadJob,
  DownloadJobCompleted,
  DownloadJobPending,
  DownloadParams,
  DownloadSubmission,
  InfoJob,
  InfoJobPending,
  InfoJobSuccess,
  InfoParams,
  ListChannelParams,
  ListChannelResponse,
  SubmitDownloadResponse,
  SubmitInfoResponse,
} from "./types.js";
import { VERSION } from "./version.js";

export const DEFAULT_BASE_URL = "https://vidkraken.com/api/v2";

export interface VidKrakenOptions {
  /** API key. Defaults to the `VIDKRAKEN_API_KEY` environment variable. */
  apiKey?: string;
  /** Defaults to `VIDKRAKEN_BASE_URL` or `https://vidkraken.com/api/v2`. */
  baseUrl?: string;
  /** Custom fetch implementation (tests, proxies, other runtimes). Defaults to the global `fetch`. */
  fetch?: typeof fetch;
  /**
   * Retries for safely retryable failures (default 2, 0 disables). GETs retry
   * on network errors, timeouts, 429, 500, 502, 503 and 504. POSTs retry only
   * on 429 and 503, where the API guarantees nothing was queued: the API has
   * no idempotency key, so a POST is never retried after a 5xx, timeout or
   * network error (it could start, and bill, a second download).
   * `Retry-After` is honored; one longer than 60 s is not waited on.
   */
  maxRetries?: number;
  /** Per-request timeout in milliseconds (default 60000). */
  timeoutMs?: number;
}

export interface RequestOptions {
  signal?: AbortSignal;
}

export interface WaitOptions<P> {
  /** Delay between status polls (default 3000 ms for downloads, 2000 ms for info). Polling is free. */
  pollIntervalMs?: number;
  /** Give up (with {@link VidKrakenWaitTimeoutError}) after this long. Default: wait indefinitely. */
  timeoutMs?: number;
  /** Called with every non-final status. */
  onProgress?: (status: P) => void;
  signal?: AbortSignal;
}

export type WaitForDownloadOptions = WaitOptions<DownloadJobPending>;
export type WaitForInfoOptions = WaitOptions<InfoJobPending>;

type Method = "GET" | "POST";

const RETRYABLE_GET_STATUSES = new Set([429, 500, 502, 503, 504]);
const RETRYABLE_POST_STATUSES = new Set([429, 503]);
// A Retry-After longer than this is surfaced to the caller instead of slept on.
const MAX_RETRY_AFTER_SEC = 60;
// Consecutive transient poll failures tolerated by waitFor* (on top of the
// per-request retries) before giving up.
const MAX_POLL_FAILURES = 5;

function readEnv(name: string): string | undefined {
  const proc = (globalThis as { process?: { env?: Record<string, string | undefined> } }).process;
  const value = proc?.env?.[name];
  return value === "" ? undefined : value;
}

function backoffMs(attempt: number): number {
  const base = Math.min(500 * 2 ** attempt, 8000);
  return base / 2 + Math.random() * (base / 2);
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(abortReason(signal));
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      reject(abortReason(signal!));
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

function abortReason(signal: AbortSignal): unknown {
  if (signal.reason !== undefined) return signal.reason;
  const err = new Error("The operation was aborted");
  err.name = "AbortError";
  return err;
}

function isTransient(err: unknown): boolean {
  return (
    err instanceof VidKrakenError &&
    (err.status === 0 || err.status === 429 || err.status >= 500)
  );
}

/**
 * Client for the Vid Kraken API.
 *
 * ```ts
 * const vk = new VidKraken(); // reads VIDKRAKEN_API_KEY
 * const job = await vk.downloadAndWait({ url: "https://youtu.be/dQw4w9WgXcQ", format: "720" });
 * await vk.saveFile(job, "./downloads/");
 * ```
 */
export class VidKraken {
  readonly baseUrl: string;
  readonly maxRetries: number;
  readonly timeoutMs: number;
  readonly #apiKey: string;
  readonly #fetch: typeof fetch;

  constructor(options: VidKrakenOptions = {}) {
    const apiKey = options.apiKey ?? readEnv("VIDKRAKEN_API_KEY");
    if (!apiKey) {
      throw new Error(
        "Missing Vid Kraken API key: pass { apiKey } to new VidKraken() or set the VIDKRAKEN_API_KEY environment variable. Get a key at https://vidkraken.com/login",
      );
    }
    const fetchImpl = options.fetch ?? globalThis.fetch;
    if (typeof fetchImpl !== "function") {
      throw new Error("No global fetch found (Node.js >= 18 required); pass { fetch } to new VidKraken()");
    }
    this.#apiKey = apiKey;
    this.#fetch = fetchImpl;
    this.baseUrl = (options.baseUrl ?? readEnv("VIDKRAKEN_BASE_URL") ?? DEFAULT_BASE_URL).replace(/\/+$/, "");
    this.maxRetries = Math.max(0, Math.floor(options.maxRetries ?? 2));
    this.timeoutMs = options.timeoutMs ?? 60_000;
  }

  // ------------------------------------------------------------ downloads

  /**
   * Submit a download (`POST /download`). Bandwidth is billed when the job
   * completes; never retried after a 5xx or network error.
   *
   * The result has a non-enumerable `rateLimit` property with the parsed
   * `RateLimit` headers: `rateLimit.remaining` is how many more downloads you
   * can queue right now.
   */
  async download(params: DownloadParams, options?: RequestOptions): Promise<DownloadSubmission> {
    const { data, response } = await this.#request<SubmitDownloadResponse & Record<string, unknown>>(
      "POST",
      "/download",
      params,
      options,
    );
    // Undocumented fields for other integrations; not part of the public shape.
    delete data.pollToken;
    delete data.pollUrl;
    const rateLimit = parseRateLimitHeaders(response.headers);
    Object.defineProperty(data, "rateLimit", { value: rateLimit, enumerable: false });
    return data as DownloadSubmission;
  }

  /** Current status of a download job (`GET /download/{jobId}`). Free. */
  async getDownload(jobId: string, options?: RequestOptions): Promise<DownloadJob> {
    const { data } = await this.#request<DownloadJob>("GET", `/download/${encodeURIComponent(jobId)}`, undefined, options);
    return data;
  }

  /**
   * Poll until the download is COMPLETED and resolve with that status.
   * Throws {@link VidKrakenJobFailedError} on FAILED and
   * {@link VidKrakenWaitTimeoutError} after `timeoutMs`.
   */
  waitForDownload(jobId: string, options: WaitForDownloadOptions = {}): Promise<DownloadJobCompleted> {
    return this.#waitFor<DownloadJob, DownloadJobPending, DownloadJobCompleted>("download", jobId, options, 3000, (id, o) =>
      this.getDownload(id, o),
      (job) => {
        if (job.status === "COMPLETED") return { done: job };
        if (job.status === "FAILED") throw new VidKrakenJobFailedError("download", job);
        return { pending: job };
      },
    );
  }

  /** {@link download} then {@link waitForDownload}. */
  async downloadAndWait(params: DownloadParams, options: WaitForDownloadOptions = {}): Promise<DownloadJobCompleted> {
    const submission = await this.download(params, options.signal ? { signal: options.signal } : undefined);
    return this.waitForDownload(submission.jobId, options);
  }

  /**
   * Stream a finished download to disk (Node.js only). `source` is a
   * `downloadUrl` or a COMPLETED status; see {@link saveFile} for `path`.
   * The API key is never sent to the file host.
   */
  saveFile(
    source: string | DownloadJobCompleted,
    path?: string,
    options: Omit<SaveFileOptions, "fetch"> = {},
  ): Promise<SavedFile> {
    return saveFile(source, path, { ...options, fetch: this.#fetch });
  }

  // ----------------------------------------------------------------- info

  /** Submit a video-info job (`POST /info`). Counts 1 info request on success. */
  async info(params: InfoParams, options?: RequestOptions): Promise<SubmitInfoResponse> {
    const { data } = await this.#request<SubmitInfoResponse>("POST", "/info", params, options);
    return data;
  }

  /** Current status of a video-info job (`GET /info/{jobId}`). Free. */
  async getInfo(jobId: string, options?: RequestOptions): Promise<InfoJob> {
    const { data } = await this.#request<InfoJob>("GET", `/info/${encodeURIComponent(jobId)}`, undefined, options);
    return data;
  }

  /** Poll until the info job succeeds; resolves with `{ jobId, status: "success", result }`. */
  waitForInfo(jobId: string, options: WaitForInfoOptions = {}): Promise<InfoJobSuccess> {
    return this.#waitFor<InfoJob, InfoJobPending, InfoJobSuccess>("info", jobId, options, 2000, (id, o) =>
      this.getInfo(id, o),
      (job) => {
        if (job.status === "success") return { done: job };
        if (job.status === "failed") throw new VidKrakenJobFailedError("info", job);
        return { pending: job };
      },
    );
  }

  /** {@link info} then {@link waitForInfo}. */
  async infoAndWait(params: InfoParams, options: WaitForInfoOptions = {}): Promise<InfoJobSuccess> {
    const submitted = await this.info(params, options.signal ? { signal: options.signal } : undefined);
    return this.waitForInfo(submitted.jobId, options);
  }

  // ------------------------------------------------------ channel/account

  /** List recent videos of a channel (`POST /list-channel`, paid add-on). */
  async listChannel(params: ListChannelParams, options?: RequestOptions): Promise<ListChannelResponse> {
    const { data } = await this.#request<ListChannelResponse>("POST", "/list-channel", params, options);
    return data;
  }

  /** Account, plan and live balances (`GET /me`). Free. */
  async me(options?: RequestOptions): Promise<AccountResponse> {
    const { data } = await this.#request<AccountResponse>("GET", "/me", undefined, options);
    return data;
  }

  // ------------------------------------------------------------- internals

  async #waitFor<J, P, D>(
    kind: JobKind,
    jobId: string,
    options: WaitOptions<P>,
    defaultInterval: number,
    get: (jobId: string, options?: RequestOptions) => Promise<J>,
    classify: (job: J) => { done: D } | { pending: P },
  ): Promise<D> {
    const interval = options.pollIntervalMs ?? defaultInterval;
    const deadline = options.timeoutMs === undefined ? Infinity : Date.now() + options.timeoutMs;
    const signal = options.signal;
    let failures = 0;
    for (;;) {
      let job: J | undefined;
      try {
        job = await get(jobId, signal ? { signal } : undefined);
        failures = 0;
      } catch (err) {
        if (signal?.aborted || !isTransient(err) || ++failures >= MAX_POLL_FAILURES) throw err;
      }
      if (job !== undefined) {
        const state = classify(job);
        if ("done" in state) return state.done;
        options.onProgress?.(state.pending);
      }
      const remaining = deadline - Date.now();
      if (remaining <= 0) throw new VidKrakenWaitTimeoutError(kind, jobId, options.timeoutMs!);
      await sleep(Math.min(interval, remaining), signal);
    }
  }

  async #request<T>(
    method: Method,
    path: string,
    body: unknown,
    options: RequestOptions = {},
  ): Promise<{ data: T; response: Response }> {
    const url = `${this.baseUrl}${path}`;
    const headers: Record<string, string> = {
      Authorization: `Bearer ${this.#apiKey}`,
      Accept: "application/json",
      "User-Agent": `vidkraken-js/${VERSION}`,
    };
    let payload: string | undefined;
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(body);
    }
    const retryable = method === "GET" ? RETRYABLE_GET_STATUSES : RETRYABLE_POST_STATUSES;

    for (let attempt = 0; ; attempt++) {
      const canRetry = attempt < this.maxRetries;
      const controller = new AbortController();
      let timedOut = false;
      const timer = setTimeout(() => {
        timedOut = true;
        controller.abort();
      }, this.timeoutMs);
      const onAbort = () => controller.abort();
      options.signal?.addEventListener("abort", onAbort, { once: true });
      if (options.signal?.aborted) controller.abort();

      let response: Response;
      let text: string;
      try {
        response = await this.#fetch(url, { method, headers, body: payload, signal: controller.signal });
        text = await response.text();
      } catch (err) {
        if (options.signal?.aborted) throw abortReason(options.signal);
        const error = new VidKrakenConnectionError({
          code: timedOut ? "TIMEOUT" : "CONNECTION_ERROR",
          message:
            (timedOut
              ? `${method} ${path} timed out after ${this.timeoutMs} ms`
              : `${method} ${path} failed: ${(err as Error)?.message ?? String(err)}`) +
            (method === "POST" ? " (not retried: the request may have been processed)" : ""),
          cause: err,
        });
        if (method === "GET" && canRetry) {
          await sleep(backoffMs(attempt), options.signal);
          continue;
        }
        throw error;
      } finally {
        clearTimeout(timer);
        options.signal?.removeEventListener("abort", onAbort);
      }

      const requestId = response.headers.get("x-request-id") ?? response.headers.get("x-vercel-id") ?? undefined;

      if (response.ok) {
        try {
          return { data: JSON.parse(text) as T, response };
        } catch {
          throw new VidKrakenError({
            status: response.status,
            code: "INVALID_RESPONSE",
            message: `${method} ${path} returned a non-JSON response`,
            details: text.slice(0, 500),
            requestId,
          });
        }
      }

      const error = buildHttpError(response, text, requestId);
      const tooLongToWait = error.retryAfter !== undefined && error.retryAfter > MAX_RETRY_AFTER_SEC;
      if (canRetry && retryable.has(response.status) && !tooLongToWait) {
        const delay = error.retryAfter !== undefined ? error.retryAfter * 1000 : backoffMs(attempt);
        await sleep(delay, options.signal);
        continue;
      }
      throw error;
    }
  }
}

function buildHttpError(response: Response, text: string, requestId: string | undefined): VidKrakenError {
  let body: Partial<ApiErrorBody> | undefined;
  try {
    const parsed: unknown = JSON.parse(text);
    if (parsed && typeof parsed === "object") body = parsed as Partial<ApiErrorBody>;
  } catch {
    // Not JSON (e.g. a proxy error page).
  }
  const code = typeof body?.code === "string" && body.code ? body.code : defaultCodeForStatus(response.status);
  const message =
    typeof body?.error === "string" && body.error
      ? body.error
      : `HTTP ${response.status}${response.statusText ? ` ${response.statusText}` : ""}`;
  const details =
    typeof body?.details === "string" ? body.details : body === undefined && text ? text.slice(0, 500) : undefined;
  return new VidKrakenError({
    status: response.status,
    code,
    message,
    details,
    retryAfter: parseRetryAfter(response.headers.get("retry-after")),
    requestId,
    rateLimit: parseRateLimitHeaders(response.headers),
  });
}

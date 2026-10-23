import {
  DOWNLOAD_ERROR_CODES,
  DOWNLOAD_ERROR_DESCRIPTIONS,
  type ApiErrorCode,
  type DownloadErrorCode,
  type DownloadJobFailed,
  type InfoJobFailed,
  type KnownDownloadErrorCode,
  type RateLimitInfo,
} from "./types.js";

export interface VidKrakenErrorInit {
  status: number;
  code: ApiErrorCode;
  message: string;
  details?: string;
  retryAfter?: number;
  requestId?: string;
  rateLimit?: RateLimitInfo;
  cause?: unknown;
}

/**
 * An HTTP error from the API (`status` > 0), or a transport failure
 * (`status` 0, see {@link VidKrakenConnectionError}).
 *
 * Branch on `code` (`INVALID_REQUEST`, `UNAUTHORIZED`, `LIMIT_REACHED`,
 * `NOT_FOUND`, `VIDEO_NOT_FOUND`, `TOO_MANY_PENDING`, `RATE_LIMITED`,
 * `INTERNAL_ERROR`, `UNAVAILABLE`, ...); treat unknown codes by `status`.
 */
export class VidKrakenError extends Error {
  /** HTTP status, or 0 when no response was received. */
  readonly status: number;
  readonly code: ApiErrorCode;
  /** Optional extra context from the API (e.g. the extractor's message on a 500). */
  readonly details?: string;
  /** Seconds to wait before retrying, from the `Retry-After` header. */
  readonly retryAfter?: number;
  /** Request id from the response headers, when the server sent one. */
  readonly requestId?: string;
  /** Parsed `RateLimit` headers, when present (e.g. on a 429 from `POST /download`). */
  readonly rateLimit?: RateLimitInfo;

  constructor(init: VidKrakenErrorInit) {
    super(init.message, init.cause === undefined ? undefined : { cause: init.cause });
    this.name = "VidKrakenError";
    this.status = init.status;
    this.code = init.code;
    if (init.details !== undefined) this.details = init.details;
    if (init.retryAfter !== undefined) this.retryAfter = init.retryAfter;
    if (init.requestId !== undefined) this.requestId = init.requestId;
    if (init.rateLimit !== undefined) this.rateLimit = init.rateLimit;
  }
}

/** No usable HTTP response: network failure (`CONNECTION_ERROR`) or request timeout (`TIMEOUT`). */
export class VidKrakenConnectionError extends VidKrakenError {
  constructor(init: Omit<VidKrakenErrorInit, "status">) {
    super({ ...init, status: 0 });
    this.name = "VidKrakenConnectionError";
  }
}

export type JobKind = "download" | "info";

const KNOWN_JOB_CODES = new Set<string>(DOWNLOAD_ERROR_CODES);

/** Human description of a job `errorCode`, or `undefined` for unknown codes. */
export function describeJobErrorCode(code: string | null | undefined): string | undefined {
  return code && KNOWN_JOB_CODES.has(code)
    ? DOWNLOAD_ERROR_DESCRIPTIONS[code as KnownDownloadErrorCode]
    : undefined;
}

/**
 * A job was accepted but ended in `FAILED` (download) or `failed` (info).
 * Failures are never charged.
 */
export class VidKrakenJobFailedError extends Error {
  readonly kind: JobKind;
  readonly jobId: string;
  /** Machine-readable reason, e.g. `AGE_RESTRICTED`. May be absent. */
  readonly errorCode?: DownloadErrorCode;
  /**
   * `true` when retrying (as a new job, after a short backoff) may succeed:
   * the code is `NETWORK_ERROR`, or it is missing/unknown, which the API
   * documents as a transient infrastructure failure. Every other known code
   * describes the video itself and will fail again.
   */
  readonly retryable: boolean;
  /** The final job status as returned by the API. */
  readonly job: DownloadJobFailed | InfoJobFailed;

  constructor(kind: JobKind, job: DownloadJobFailed | InfoJobFailed) {
    const errorCode = job.errorCode ?? undefined;
    const apiMessage = "error" in job ? job.error : undefined;
    const description = describeJobErrorCode(errorCode);
    const message =
      [errorCode, apiMessage ?? description].filter(Boolean).join(": ") || `The ${kind} job failed`;
    super(message);
    this.name = "VidKrakenJobFailedError";
    this.kind = kind;
    this.jobId = job.jobId;
    if (errorCode !== undefined) this.errorCode = errorCode;
    this.retryable = errorCode === "NETWORK_ERROR" || !errorCode || !KNOWN_JOB_CODES.has(errorCode);
    this.job = job;
  }
}

/** `waitForDownload` / `waitForInfo` gave up after `timeoutMs`. The job itself keeps running. */
export class VidKrakenWaitTimeoutError extends Error {
  readonly kind: JobKind;
  readonly jobId: string;
  readonly timeoutMs: number;
  constructor(kind: JobKind, jobId: string, timeoutMs: number) {
    super(`Timed out after ${timeoutMs} ms waiting for ${kind} job ${jobId}; the job may still finish`);
    this.name = "VidKrakenWaitTimeoutError";
    this.kind = kind;
    this.jobId = jobId;
    this.timeoutMs = timeoutMs;
  }
}

const DEFAULT_CODES: Record<number, ApiErrorCode> = {
  400: "INVALID_REQUEST",
  401: "UNAUTHORIZED",
  403: "LIMIT_REACHED",
  404: "NOT_FOUND",
  429: "RATE_LIMITED",
  503: "UNAVAILABLE",
};

/** The code the API uses for a status when the body carries none. */
export function defaultCodeForStatus(status: number): ApiErrorCode {
  return DEFAULT_CODES[status] ?? (status >= 500 ? "INTERNAL_ERROR" : `HTTP_${status}`);
}

// Types for the Vid Kraken v2 REST API. Field names match
// https://vidkraken.com/openapi.yaml exactly.

/** Allows known string literals to autocomplete while accepting values added to the API later. */
// eslint-disable-next-line @typescript-eslint/ban-types
export type OpenString<T extends string> = T | (string & {});

/** Every media format the API accepts, in documentation order. */
export const MEDIA_FORMATS = ["1080", "720", "480", "360", "audio", "audio-aac", "audio-mp3"] as const;

/**
 * Target media format. Numeric values are vertical resolutions delivered as MP4.
 * `audio` is the best native audio stream (usually M4A or WebM, nothing re-encoded),
 * `audio-aac` prefers AAC/M4A, `audio-mp3` is always a 320 kbps MP3.
 */
export type MediaFormat = (typeof MEDIA_FORMATS)[number];

export const DOWNLOAD_JOB_STATUSES = ["IN_QUEUE", "IN_PROGRESS", "COMPLETED", "FAILED"] as const;
export type DownloadJobStatus = (typeof DOWNLOAD_JOB_STATUSES)[number];

export const INFO_JOB_STATUSES = ["pending", "success", "failed"] as const;
export type InfoJobStatus = (typeof INFO_JOB_STATUSES)[number];

/** Why an accepted download (or info) job failed. See {@link DOWNLOAD_ERROR_DESCRIPTIONS}. */
export const DOWNLOAD_ERROR_CODES = [
  "AGE_RESTRICTED",
  "MEMBERS_ONLY",
  "PAYMENT_REQUIRED",
  "PRIVATE_VIDEO",
  "VIDEO_PREMIERE",
  "GEO_RESTRICTED",
  "VIDEO_PROCESSING",
  "VIDEO_UNAVAILABLE",
  "DRM_PROTECTED",
  "VIDEO_NOT_FOUND",
  "FORMAT_UNAVAILABLE",
  "LIVE_STREAM_IN_PROGRESS",
  "COPYRIGHTED_MUSIC",
  "NO_DOWNLOAD_OPT_OUT",
  "FILE_TOO_LARGE",
  "NETWORK_ERROR",
] as const;
export type KnownDownloadErrorCode = (typeof DOWNLOAD_ERROR_CODES)[number];
/** Known codes plus any value the API adds later (unknown values are transient: retry after a backoff). */
export type DownloadErrorCode = OpenString<KnownDownloadErrorCode>;

export const DOWNLOAD_ERROR_DESCRIPTIONS: Record<KnownDownloadErrorCode, string> = {
  AGE_RESTRICTED: "Sign-in required by YouTube for this video",
  MEMBERS_ONLY: "Restricted to channel members",
  PAYMENT_REQUIRED: "Purchase or rental required",
  PRIVATE_VIDEO: "The video is private",
  VIDEO_PREMIERE: "A scheduled premiere that has not aired yet; retry after the premiere starts",
  GEO_RESTRICTED: "Not available in the server region",
  VIDEO_PROCESSING: "YouTube is still processing the upload",
  VIDEO_UNAVAILABLE: "Unavailable for an unspecified reason",
  DRM_PROTECTED: "DRM protected",
  VIDEO_NOT_FOUND: "No such video, or it has been deleted",
  FORMAT_UNAVAILABLE: "The requested format/quality is not offered",
  LIVE_STREAM_IN_PROGRESS: "Still live; retry once it has ended",
  COPYRIGHTED_MUSIC: "Copyrighted music, not downloadable here",
  NO_DOWNLOAD_OPT_OUT: "The creator opted out via #nodownload",
  FILE_TOO_LARGE:
    "The file in this format is larger than we can deliver; try a lower-quality format or a shorter clip",
  NETWORK_ERROR: "Transient network failure on our side; retry after a short backoff",
};

/** `code` on an HTTP error response. New codes may be added: branch on the HTTP status for unknown ones. */
export const API_ERROR_CODES = [
  "INVALID_REQUEST",
  "UNAUTHORIZED",
  "FORBIDDEN",
  "LIMIT_REACHED",
  "NOT_FOUND",
  "VIDEO_NOT_FOUND",
  "TOO_MANY_PENDING",
  "RATE_LIMITED",
  "INTERNAL_ERROR",
  "UNAVAILABLE",
] as const;
export type KnownApiErrorCode = (typeof API_ERROR_CODES)[number];
/**
 * Error codes produced by the SDK itself rather than the API:
 * `CONNECTION_ERROR` (network failure), `TIMEOUT` (request timed out),
 * `INVALID_RESPONSE` (the API answered with something that is not JSON).
 */
export type ClientErrorCode = "CONNECTION_ERROR" | "TIMEOUT" | "INVALID_RESPONSE";
export type ApiErrorCode = OpenString<KnownApiErrorCode | ClientErrorCode>;

/** The JSON body of every HTTP error response. */
export interface ApiErrorBody {
  error: string;
  code: OpenString<KnownApiErrorCode>;
  details?: string;
}

// ---------------------------------------------------------------- downloads

export interface DownloadParams {
  /** YouTube video URL (`youtube.com/watch?v=ID`, `youtu.be/ID`, `youtube.com/live/ID`). */
  url: string;
  /** Target format. Defaults to `"audio"` on the server. */
  format?: MediaFormat;
  /** Trim start in seconds (inclusive). */
  startTime?: number;
  /** Trim end in seconds (exclusive). */
  endTime?: number;
  /** Preferred audio track language as a BCP-47 tag (`en`, `en-US`, `th`). */
  language?: string;
  /** Public URL that receives the final job status as a signed POST. */
  webhookUrl?: string;
}

export interface SubmitDownloadResponse {
  jobId: string;
  title: string;
  /** Full video duration in seconds. */
  duration: number;
  status: "IN_QUEUE";
  /** Human-readable next-step hint. */
  message: string;
}

/**
 * The pending-downloads cap from the IETF `RateLimit-Policy` / `RateLimit`
 * headers on `POST /download` (draft-ietf-httpapi-ratelimit-headers).
 */
export interface RateLimitInfo {
  /** Policy name, e.g. `"pending"`. */
  policy: string;
  /** `q`: how many downloads may be queued or in progress at once. */
  limit?: number;
  /** `r`: how many more downloads can be queued right now. */
  remaining?: number;
  /** `t`: seconds until the quota resets, when the server sends it. */
  resetSeconds?: number;
  /** `qu`: quota unit, e.g. `"concurrent-requests"`. */
  quotaUnit?: string;
}

/**
 * What {@link VidKraken.download} resolves with: the JSON response, plus a
 * non-enumerable `rateLimit` property (so `JSON.stringify` and spreading are
 * unaffected) carrying the parsed rate-limit headers.
 */
export type DownloadSubmission = SubmitDownloadResponse & {
  readonly rateLimit?: RateLimitInfo;
};

export type DownloadProgressStage =
  | "queued"
  | "starting"
  | "extracting"
  | "downloading"
  | "processing"
  | "uploading";

export interface DownloadProgress {
  stage: OpenString<DownloadProgressStage>;
  /** Overall progress, 0-99 while running (never decreases). */
  percent: number;
  bytesDownloaded: number;
  totalBytes: number | null;
  totalBytesIsEstimate: boolean;
  etaSeconds: number | null;
  /** Only while `stage` is `queued`: place in the queue, 1 = next. */
  queuePosition?: number | null;
  updatedAt: string;
}

interface DownloadJobBase {
  jobId: string;
  title: string;
  duration: number;
  /** The original YouTube URL submitted. */
  url: string;
  /** The format requested in the original POST. */
  format: MediaFormat;
  language?: string | null;
  createdAt?: string;
  startedAt?: string | null;
  completedAt?: string | null;
}

export interface DownloadJobPending extends DownloadJobBase {
  status: "IN_QUEUE" | "IN_PROGRESS";
  progress?: DownloadProgress;
}

export interface DownloadJobCompleted extends DownloadJobBase {
  status: "COMPLETED";
  /** Format actually delivered; may differ from `format`. */
  actualFormat: MediaFormat;
  /** CDN URL of the produced file. */
  downloadUrl: string;
  /** Size of the produced file in bytes. */
  fileSize: number;
}

export interface DownloadJobFailed extends DownloadJobBase {
  status: "FAILED";
  errorCode?: DownloadErrorCode | null;
}

export type DownloadJob = DownloadJobPending | DownloadJobCompleted | DownloadJobFailed;

// --------------------------------------------------------------------- info

export interface InfoParams {
  /** YouTube video URL. */
  url: string;
  /** Also list available audio tracks (slower: needs a full extraction). */
  includeAudioTracks?: boolean;
}

export interface SubmitInfoResponse {
  jobId: string;
  status: "pending";
}

export interface AudioTrack {
  type: "original" | "dubbed";
  /** ISO language code, or `null` if unknown. */
  language: string | null;
}

export interface VideoInfo {
  title: string;
  /** Duration in seconds. */
  duration: number;
  url: string;
  /** Present only when submitted with `includeAudioTracks: true`. */
  audio?: AudioTrack[];
  /** ISO 3166-1 alpha-2 country codes. Absent when the video plays everywhere. */
  regionRestriction?: { allowed?: string[]; blocked?: string[] };
}

export interface InfoJobPending {
  jobId: string;
  status: "pending";
}
export interface InfoJobSuccess {
  jobId: string;
  status: "success";
  result: VideoInfo;
}
export interface InfoJobFailed {
  jobId: string;
  status: "failed";
  /** Human-readable failure reason. */
  error: string;
  /** Present for recognised conditions; absent for unrecognised (retryable) failures. */
  errorCode?: DownloadErrorCode;
}
export type InfoJob = InfoJobPending | InfoJobSuccess | InfoJobFailed;

// ------------------------------------------------------------------ channel

export interface ListChannelParams {
  /** YouTube channel URL, e.g. `https://www.youtube.com/@channelHandle`. */
  channelUrl: string;
  /** 1-1000, default 5. */
  maxVideos?: number;
}

export interface ChannelEntry {
  id?: string;
  title?: string;
  url?: string;
  duration?: number;
  view_count?: number;
  uploader?: string;
  [key: string]: unknown;
}

/** Raw `yt-dlp --flat-playlist -J` output. */
export interface ListChannelResponse {
  id?: string;
  title?: string;
  channel?: string;
  channel_id?: string;
  channel_url?: string;
  entries?: ChannelEntry[];
  [key: string]: unknown;
}

// ------------------------------------------------------------------ account

export interface FeatureBalance {
  granted: number;
  used: number;
  remaining: number;
  /** When `true`, usage is not capped and the numeric fields can be ignored. */
  unlimited: boolean;
  /** Unix ms when the balance resets, or `null` for one-off grants. */
  resetsAt: number | null;
}

export interface AccountResponse {
  name: string;
  email: string;
  plan: {
    /** `free`, `starter`, `growth`, `scale` or `custom`. */
    id: string;
    renewsAt: number | null;
    canceledAt: number | null;
  };
  /** Keyed by feature id: always `download_count`, `bandwidth_mb`, `info`; add-ons when enabled. */
  balances: Record<string, FeatureBalance>;
}

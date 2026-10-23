import { parseArgs } from "node:util";
import { VidKraken } from "./client.js";
import {
  VidKrakenError,
  VidKrakenJobFailedError,
  VidKrakenWaitTimeoutError,
  describeJobErrorCode,
} from "./errors.js";
import { suggestedFilename } from "./filename.js";
import {
  MEDIA_FORMATS,
  type AccountResponse,
  type DownloadJob,
  type DownloadJobPending,
  type DownloadParams,
  type MediaFormat,
  type VideoInfo,
} from "./types.js";
import { VERSION } from "./version.js";

export interface CliStream {
  write(chunk: string): unknown;
  isTTY?: boolean;
}

export interface CliIO {
  stdout: CliStream;
  stderr: CliStream;
  env: Record<string, string | undefined>;
  /** Injected fetch (tests). */
  fetch?: typeof fetch;
  /** Poll interval override in ms (tests). */
  pollIntervalMs?: number;
}

export const EXIT_OK = 0;
export const EXIT_ERROR = 1;
export const EXIT_USAGE = 2;

class UsageError extends Error {}

const HELP = `vidkraken ${VERSION} - download YouTube videos and audio with the Vid Kraken API

Usage:
  vidkraken download <url> [options]   Download a video or its audio and save it
  vidkraken status <jobId>             Show a download job's status
  vidkraken info <url> [--audio-tracks] Show title, duration and audio tracks
  vidkraken me                         Show your plan and remaining balances

Download options:
  -f, --format <fmt>     1080 | 720 | 480 | 360 | audio | audio-aac | audio-mp3 (default: audio)
      --start <time>     Trim start, in seconds or [h:]m:s
      --end <time>       Trim end, in seconds or [h:]m:s
      --language <tag>   Preferred audio language (BCP-47, e.g. en, en-US)
  -o, --output <path>    File path or directory (default: ./<title>.<ext>)
      --no-wait          Submit only; print the job id and exit
      --webhook <url>    POST the final job status to this URL
      --timeout <sec>    Stop waiting after this many seconds (the job keeps running)

Global options:
      --json             Print one JSON document to stdout (errors too)
  -q, --quiet            No progress output on stderr
      --api-key <key>    API key (default: $VIDKRAKEN_API_KEY)
  -h, --help             Show this help
  -v, --version          Show the version

Exit codes: 0 ok, 1 API or job error, 2 usage error.
Get an API key at https://vidkraken.com/login - docs: https://vidkraken.com/docs
Use only with content you have the right to download.
`;

const OPTIONS = {
  help: { type: "boolean", short: "h" },
  version: { type: "boolean", short: "v" },
  json: { type: "boolean" },
  quiet: { type: "boolean", short: "q" },
  "api-key": { type: "string" },
  "base-url": { type: "string" },
  format: { type: "string", short: "f" },
  start: { type: "string" },
  end: { type: "string" },
  language: { type: "string" },
  output: { type: "string", short: "o" },
  "no-wait": { type: "boolean" },
  webhook: { type: "string" },
  timeout: { type: "string" },
  "audio-tracks": { type: "boolean" },
} as const;

type OptionName = keyof typeof OPTIONS;
const GLOBAL_OPTIONS: OptionName[] = ["help", "version", "json", "quiet", "api-key", "base-url"];
const COMMANDS: Record<string, { args: string[]; options: OptionName[] }> = {
  download: {
    args: ["url"],
    options: ["format", "start", "end", "language", "output", "no-wait", "webhook", "timeout"],
  },
  status: { args: ["jobId"], options: [] },
  info: { args: ["url"], options: ["audio-tracks", "timeout"] },
  me: { args: [], options: [] },
};

type Values = Partial<Record<OptionName, string | boolean>>;

export async function runCli(argv: string[], io: CliIO): Promise<number> {
  let json = argv.includes("--json");
  const color = !!io.stdout.isTTY && !!io.stderr.isTTY && !io.env.NO_COLOR;
  const paint = (code: string, s: string) => (color ? `\x1b[${code}m${s}\x1b[0m` : s);

  const fail = (exit: number, error: Record<string, unknown>, lines: string[]) => {
    if (json) io.stdout.write(JSON.stringify({ error }) + "\n");
    for (const line of lines) io.stderr.write(line + "\n");
    return exit;
  };

  let values: Values;
  let positionals: string[];
  try {
    const parsed = parseArgs({ args: argv, options: OPTIONS, allowPositionals: true, strict: true });
    values = parsed.values as Values;
    positionals = parsed.positionals;
  } catch (err) {
    return fail(EXIT_USAGE, { code: "USAGE", message: (err as Error).message }, [
      `${paint("31", "error:")} ${(err as Error).message}`,
      "Run 'vidkraken --help' for usage.",
    ]);
  }
  json = !!values.json;

  if (values.version) {
    io.stdout.write(json ? JSON.stringify({ version: VERSION }) + "\n" : `${VERSION}\n`);
    return EXIT_OK;
  }
  if (values.help || positionals.length === 0) {
    if (!values.help) {
      return fail(EXIT_USAGE, { code: "USAGE", message: "Missing command" }, [HELP.trimEnd()]);
    }
    io.stdout.write(HELP);
    return EXIT_OK;
  }

  try {
    const [command, ...args] = positionals as [string, ...string[]];
    const spec = COMMANDS[command];
    if (!spec) throw new UsageError(`Unknown command '${command}'`);
    for (const name of Object.keys(values) as OptionName[]) {
      if (!GLOBAL_OPTIONS.includes(name) && !spec.options.includes(name)) {
        throw new UsageError(`Option --${name} is not valid for '${command}'`);
      }
    }
    if (args.length !== spec.args.length) {
      const usage = spec.args.map((a) => `<${a}>`).join(" ");
      throw new UsageError(`Usage: vidkraken ${command}${usage ? " " + usage : ""}`);
    }

    // Validate options before the client so usage errors win over a missing key.
    const downloadParams = command === "download" ? buildDownloadParams(args[0]!, values) : undefined;
    const timeoutMs = values.timeout === undefined ? undefined : parsePositive(values.timeout as string, "--timeout") * 1000;
    if (values["no-wait"] && values.output !== undefined) {
      throw new UsageError("--output cannot be combined with --no-wait");
    }

    let client: VidKraken;
    try {
      client = new VidKraken({
        apiKey: (values["api-key"] as string | undefined) ?? io.env.VIDKRAKEN_API_KEY,
        baseUrl: (values["base-url"] as string | undefined) ?? io.env.VIDKRAKEN_BASE_URL,
        ...(io.fetch ? { fetch: io.fetch } : {}),
      });
    } catch (err) {
      throw new UsageError((err as Error).message);
    }

    const ctx: Ctx = {
      io,
      client,
      json,
      quiet: !!values.quiet,
      timeoutMs,
      ...(io.pollIntervalMs !== undefined ? { pollIntervalMs: io.pollIntervalMs } : {}),
    };
    switch (command) {
      case "download":
        return await cmdDownload(ctx, downloadParams!, values);
      case "status":
        return await cmdStatus(ctx, args[0]!);
      case "info":
        return await cmdInfo(ctx, args[0]!, !!values["audio-tracks"]);
      default:
        return await cmdMe(ctx);
    }
  } catch (err) {
    clearProgressLine(io);
    if (err instanceof UsageError) {
      return fail(EXIT_USAGE, { code: "USAGE", message: err.message }, [
        `${paint("31", "error:")} ${err.message}`,
        "Run 'vidkraken --help' for usage.",
      ]);
    }
    if (err instanceof VidKrakenJobFailedError) {
      const code = err.errorCode ?? "JOB_FAILED";
      return fail(
        EXIT_ERROR,
        {
          code,
          message: err.message,
          jobId: err.jobId,
          retryable: err.retryable,
        },
        [`${paint("31", "error:")} ${err.errorCode ? err.message : `JOB_FAILED: ${err.message}`} (job ${err.jobId})`],
      );
    }
    if (err instanceof VidKrakenError) {
      const lines = [`${paint("31", "error:")} ${err.code}: ${err.message}`];
      if (err.details) lines.push(`details: ${err.details}`);
      if (err.retryAfter !== undefined) lines.push(`retry after: ${err.retryAfter}s`);
      return fail(
        EXIT_ERROR,
        {
          code: err.code,
          message: err.message,
          status: err.status,
          ...(err.details !== undefined && { details: err.details }),
          ...(err.retryAfter !== undefined && { retryAfter: err.retryAfter }),
          ...(err.requestId !== undefined && { requestId: err.requestId }),
        },
        lines,
      );
    }
    if (err instanceof VidKrakenWaitTimeoutError) {
      return fail(EXIT_ERROR, { code: "WAIT_TIMEOUT", message: err.message, jobId: err.jobId }, [
        `${paint("31", "error:")} WAIT_TIMEOUT: ${err.message}`,
      ]);
    }
    const message = (err as Error)?.message ?? String(err);
    return fail(EXIT_ERROR, { code: "ERROR", message }, [`${paint("31", "error:")} ${message}`]);
  }
}

interface Ctx {
  io: CliIO;
  client: VidKraken;
  json: boolean;
  quiet: boolean;
  timeoutMs: number | undefined;
  pollIntervalMs?: number;
}

// ------------------------------------------------------------------ parsing

/** Seconds from `90`, `1:30` or `1:02:03`. */
export function parseTime(value: string, flag: string): number {
  if (/^\d+$/.test(value)) return Number(value);
  const parts = value.split(":");
  if (parts.length >= 2 && parts.length <= 3 && parts.every((p) => /^\d+$/.test(p))) {
    return parts.reduce((acc, p) => acc * 60 + Number(p), 0);
  }
  throw new UsageError(`${flag} must be seconds or [h:]m:s, got '${value}'`);
}

function parsePositive(value: string, flag: string): number {
  const n = Number(value);
  if (!/^\d+(\.\d+)?$/.test(value) || !(n > 0)) throw new UsageError(`${flag} must be a positive number`);
  return n;
}

export function parseFormat(value: string): MediaFormat {
  const normalized = value.toLowerCase().replace(/^(\d+)p$/, "$1");
  if ((MEDIA_FORMATS as readonly string[]).includes(normalized)) return normalized as MediaFormat;
  throw new UsageError(`Invalid --format '${value}'. Use one of: ${MEDIA_FORMATS.join(", ")}`);
}

function buildDownloadParams(url: string, values: Values): DownloadParams {
  const params: DownloadParams = { url };
  if (values.format !== undefined) params.format = parseFormat(values.format as string);
  if (values.start !== undefined) params.startTime = parseTime(values.start as string, "--start");
  if (values.end !== undefined) params.endTime = parseTime(values.end as string, "--end");
  if (params.startTime !== undefined && params.endTime !== undefined && params.endTime <= params.startTime) {
    throw new UsageError("--end must be greater than --start");
  }
  if (values.language !== undefined) params.language = values.language as string;
  if (values.webhook !== undefined) params.webhookUrl = values.webhook as string;
  return params;
}

// --------------------------------------------------------------- formatting

export function formatBytes(bytes: number): string {
  if (bytes < 1000) return `${bytes} B`;
  const units = ["kB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = -1;
  while (value >= 1000 && unit < units.length - 1) {
    value /= 1000;
    unit++;
  }
  return `${value.toFixed(1)} ${units[unit]}`;
}

export function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}

const isoDate = (ms: number | null | undefined) => (ms ? new Date(ms).toISOString().slice(0, 10) : undefined);

export function formatProgress(job: DownloadJobPending): string {
  const p = job.progress;
  if (!p) return job.status === "IN_QUEUE" ? "queued" : "in progress";
  if (p.stage === "queued") return p.queuePosition ? `queued (position ${p.queuePosition})` : "queued";
  let line = `${p.stage} ${p.percent}%`;
  const extra: string[] = [];
  if (p.totalBytes) {
    extra.push(`${formatBytes(p.bytesDownloaded)} of ${p.totalBytesIsEstimate ? "~" : ""}${formatBytes(p.totalBytes)}`);
  }
  if (p.etaSeconds !== null && p.etaSeconds !== undefined) extra.push(`~${formatDuration(p.etaSeconds)} left`);
  if (extra.length) line += ` (${extra.join(", ")})`;
  return line;
}

function table(rows: Array<[string, string | number | undefined | null]>): string {
  const present = rows.filter((r): r is [string, string | number] => r[1] !== undefined && r[1] !== null && r[1] !== "");
  const width = Math.max(...present.map(([k]) => k.length)) + 1;
  return present.map(([k, v]) => `${(k + ":").padEnd(width + 1)}${v}`).join("\n") + "\n";
}

// Progress lines: rewritten in place on a TTY, one line per change otherwise.
const progressState = new WeakMap<CliIO, { last?: string; open: boolean }>();

function progress(ctx: Ctx, line: string) {
  if (ctx.quiet) return;
  const state = progressState.get(ctx.io) ?? { open: false };
  progressState.set(ctx.io, state);
  if (line === state.last) return;
  state.last = line;
  if (ctx.io.stderr.isTTY) {
    ctx.io.stderr.write(`\r\x1b[2K${line}`);
    state.open = true;
  } else {
    ctx.io.stderr.write(line + "\n");
  }
}

function clearProgressLine(io: CliIO) {
  const state = progressState.get(io);
  if (state?.open) {
    io.stderr.write("\r\x1b[2K");
    state.open = false;
  }
  if (state) state.last = undefined;
}

function note(ctx: Ctx, line: string) {
  if (ctx.quiet) return;
  clearProgressLine(ctx.io);
  ctx.io.stderr.write(line + "\n");
}

// ----------------------------------------------------------------- commands

async function cmdDownload(ctx: Ctx, params: DownloadParams, values: Values): Promise<number> {
  const { io, client } = ctx;
  const submission = await client.download(params);

  if (values["no-wait"]) {
    if (ctx.json) {
      const out = submission.rateLimit ? { ...submission, rateLimit: submission.rateLimit } : { ...submission };
      io.stdout.write(JSON.stringify(out) + "\n");
    } else {
      io.stdout.write(`${submission.jobId}\n`);
    }
    return EXIT_OK;
  }

  note(ctx, `Submitted ${submission.jobId}: ${submission.title} (${formatDuration(submission.duration)})`);
  const completed = await client.waitForDownload(submission.jobId, {
    ...(ctx.pollIntervalMs !== undefined && { pollIntervalMs: ctx.pollIntervalMs }),
    ...(ctx.timeoutMs !== undefined && { timeoutMs: ctx.timeoutMs }),
    onProgress: (job) => progress(ctx, formatProgress(job)),
  });

  const output = values.output as string | undefined;
  note(ctx, `Completed (${formatBytes(completed.fileSize)}${completed.actualFormat !== completed.format ? `, delivered as ${completed.actualFormat}` : ""}); saving`);
  const saved = await client.saveFile(completed, output ?? suggestedFilename(completed), {
    onProgress: ({ bytes, total }) => {
      if (io.stderr.isTTY) progress(ctx, `saving ${formatBytes(bytes)}${total ? ` of ${formatBytes(total)}` : ""}`);
    },
  });
  clearProgressLine(io);

  if (ctx.json) {
    io.stdout.write(JSON.stringify({ ...completed, path: saved.path, bytes: saved.bytes }) + "\n");
  } else {
    io.stdout.write(`${saved.path}\n`);
  }
  return EXIT_OK;
}

async function cmdStatus(ctx: Ctx, jobId: string): Promise<number> {
  const job: DownloadJob = await ctx.client.getDownload(jobId);
  // A FAILED job exits 1 (like `download`), so scripts can branch on it.
  const exitCode = job.status === "FAILED" ? EXIT_ERROR : EXIT_OK;
  if (ctx.json) {
    ctx.io.stdout.write(JSON.stringify(job) + "\n");
    return exitCode;
  }
  const rows: Array<[string, string | number | undefined | null]> = [
    ["jobId", job.jobId],
    ["status", job.status],
    ["title", job.title],
    ["duration", typeof job.duration === "number" ? formatDuration(job.duration) : undefined],
    ["url", job.url],
    ["format", job.format],
    ["language", job.language],
  ];
  if (job.status === "COMPLETED") {
    rows.push(["actualFormat", job.actualFormat], ["fileSize", formatBytes(job.fileSize)], ["downloadUrl", job.downloadUrl]);
  } else if (job.status !== "FAILED") {
    rows.push(["progress", formatProgress(job)]);
  } else {
    const description = describeJobErrorCode(job.errorCode);
    rows.push(["errorCode", job.errorCode ? `${job.errorCode}${description ? ` (${description})` : ""}` : "unknown"]);
  }
  ctx.io.stdout.write(table(rows));
  return exitCode;
}

async function cmdInfo(ctx: Ctx, url: string, includeAudioTracks: boolean): Promise<number> {
  const submitted = await ctx.client.info(includeAudioTracks ? { url, includeAudioTracks } : { url });
  const done = await ctx.client.waitForInfo(submitted.jobId, {
    ...(ctx.pollIntervalMs !== undefined && { pollIntervalMs: ctx.pollIntervalMs }),
    ...(ctx.timeoutMs !== undefined && { timeoutMs: ctx.timeoutMs }),
    onProgress: () => progress(ctx, "fetching info"),
  });
  clearProgressLine(ctx.io);
  const info: VideoInfo = done.result;
  if (ctx.json) {
    ctx.io.stdout.write(JSON.stringify(info) + "\n");
    return EXIT_OK;
  }
  const region = info.regionRestriction;
  ctx.io.stdout.write(
    table([
      ["title", info.title],
      ["duration", `${formatDuration(info.duration)} (${info.duration}s)`],
      ["url", info.url],
      ["audio", info.audio?.map((t) => `${t.type} ${t.language ?? "unknown"}`).join(", ")],
      ["allowed in", region?.allowed?.join(", ")],
      ["blocked in", region?.blocked?.join(", ")],
    ]),
  );
  return EXIT_OK;
}

async function cmdMe(ctx: Ctx): Promise<number> {
  const me: AccountResponse = await ctx.client.me();
  if (ctx.json) {
    ctx.io.stdout.write(JSON.stringify(me) + "\n");
    return EXIT_OK;
  }
  const renews = isoDate(me.plan.renewsAt);
  const canceled = isoDate(me.plan.canceledAt);
  let out = table([
    ["name", me.name],
    ["email", me.email],
    ["plan", `${me.plan.id}${renews ? ` (renews ${renews})` : ""}${canceled ? ` (cancels at period end)` : ""}`],
  ]);
  const keys = Object.keys(me.balances).sort();
  if (keys.length) {
    out += "balances:\n";
    const width = Math.max(...keys.map((k) => k.length));
    for (const key of keys) {
      const b = me.balances[key]!;
      const resets = isoDate(b.resetsAt);
      const amount = b.unlimited ? "unlimited" : `${b.remaining} of ${b.granted} remaining`;
      out += `  ${key.padEnd(width)}  ${amount}${resets && !b.unlimited ? ` (resets ${resets})` : ""}\n`;
    }
  }
  ctx.io.stdout.write(out);
  return EXIT_OK;
}

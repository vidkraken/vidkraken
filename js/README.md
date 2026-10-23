# vidkraken

[![Download success rate over the last 30 days](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fvidkraken.com%2Fapi%2Fstatus&query=%24.overall.reliability&suffix=%25&label=download%20success%2C%2030%20days&color=brightgreen&cacheSeconds=3600)](https://vidkraken.com/reliability)

**Reliability you can check:** Vid Kraken publishes its download success rate live. The badge above is the share of downloads that completed over the last 30 days ([daily breakdown](https://vidkraken.com/reliability)). Bot detection, proxies, cookies and retries are handled on our servers, so your code never sees YouTube's blocks.

The official JavaScript/TypeScript SDK and CLI for [Vid Kraken](https://vidkraken.com), a REST API for downloading YouTube videos and audio (MP4 from 360p to 1080p and 4K on request, M4A, MP3) without running yt-dlp yourself.

- Zero runtime dependencies, typed, ESM + CommonJS
- Node.js 18+ (or any runtime with `fetch`; saving files needs Node)
- Safe retries: never re-submits a download that might already have been queued

> Use only with content you have the right to download.

## Install

```sh
npm install vidkraken
```

Get an API key at **https://vidkraken.com/login**, then expose it as `VIDKRAKEN_API_KEY` (or pass `apiKey` to the client).

```sh
export VIDKRAKEN_API_KEY=your_api_key
```

## SDK quickstart

### Download and wait

```ts
import { VidKraken } from "vidkraken";

const vk = new VidKraken(); // reads VIDKRAKEN_API_KEY

const job = await vk.downloadAndWait(
  { url: "https://youtu.be/dQw4w9WgXcQ", format: "720" },
  { onProgress: (s) => console.error(s.status, s.progress?.percent ?? 0, "%") },
);

console.log(job.downloadUrl, job.fileSize, job.actualFormat);
```

`require("vidkraken")` works the same in CommonJS.

Formats: `"1080"`, `"720"`, `"480"`, `"360"` (MP4), `"audio"` (default: best native audio, usually M4A or WebM, not re-encoded), `"audio-aac"` (M4A), `"audio-mp3"` (320 kbps MP3). Trim with `startTime` / `endTime` (seconds), pick a dubbed track with `language` (`"en"`, `"en-US"`), or pass a `webhookUrl` to be notified instead of polling.

### Step by step

```ts
const { jobId } = await vk.download({ url, format: "audio-mp3" });
const status = await vk.getDownload(jobId); // IN_QUEUE | IN_PROGRESS | COMPLETED | FAILED
const done = await vk.waitForDownload(jobId, { pollIntervalMs: 3000, timeoutMs: 10 * 60_000 });
```

### Save the file (Node.js)

```ts
const saved = await vk.saveFile(job, "./downloads/"); // a directory: uses "<title>.<ext>"
await vk.saveFile(job.downloadUrl, "./song.mp3");      // or an explicit file path
console.log(saved.path, saved.bytes);
```

The file is streamed to `<path>.part` and renamed when complete. The extension is `mp4` for video, `mp3` for `audio-mp3`, `m4a` for `audio-aac`, and whatever container YouTube served for `audio`. Your API key is never sent to the file host. `saveFile` loads `node:fs` lazily, so importing the SDK is safe in browsers, workers, Deno and Bun.

### Video info

```ts
const { result } = await vk.infoAndWait({ url, includeAudioTracks: true });
console.log(result.title, result.duration, result.audio); // [{ type: "original", language: "en" }, ...]
```

Also: `vk.info()`, `vk.getInfo(jobId)`, `vk.waitForInfo(jobId)`, `vk.listChannel({ channelUrl, maxVideos })` (paid add-on) and `vk.me()` (plan and live balances; free).

### Errors

```ts
import { VidKrakenError, VidKrakenJobFailedError } from "vidkraken";

try {
  await vk.downloadAndWait({ url });
} catch (err) {
  if (err instanceof VidKrakenJobFailedError) {
    // The job was accepted but failed (never charged).
    console.error(err.errorCode, err.retryable); // e.g. "AGE_RESTRICTED", false
  } else if (err instanceof VidKrakenError) {
    // The request was rejected.
    console.error(err.status, err.code, err.message, err.details, err.retryAfter);
  }
}
```

| `err.code` | Status | Meaning |
|---|---|---|
| `INVALID_REQUEST` | 400 | Fix the request |
| `UNAUTHORIZED` | 401 | Missing or invalid API key |
| `LIMIT_REACHED` | 403 | Plan allowance used up |
| `NOT_FOUND` / `VIDEO_NOT_FOUND` | 404 | Unknown/expired job, or no such video |
| `TOO_MANY_PENDING` / `RATE_LIMITED` | 429 | Wait `retryAfter` seconds |
| `INTERNAL_ERROR` | 500 | Server error |
| `UNAVAILABLE` | 503 | Temporarily unavailable |
| `CONNECTION_ERROR` / `TIMEOUT` | 0 | No response (`VidKrakenConnectionError`) |

New codes may appear; fall back to `err.status` for unknown ones. A job's `errorCode` is one of `AGE_RESTRICTED`, `MEMBERS_ONLY`, `PAYMENT_REQUIRED`, `PRIVATE_VIDEO`, `VIDEO_PREMIERE`, `GEO_RESTRICTED`, `VIDEO_PROCESSING`, `VIDEO_UNAVAILABLE`, `DRM_PROTECTED`, `VIDEO_NOT_FOUND`, `FORMAT_UNAVAILABLE`, `LIVE_STREAM_IN_PROGRESS`, `COPYRIGHTED_MUSIC`, `NO_DOWNLOAD_OPT_OUT`, `FILE_TOO_LARGE` or `NETWORK_ERROR`. `retryable` is `true` for `NETWORK_ERROR` and for missing or unknown codes (transient failures on the server side); every other code describes the video and will fail again. `waitFor*` throws `VidKrakenWaitTimeoutError` when its `timeoutMs` runs out (the job keeps running).

### Retries

The client retries automatically, `maxRetries` times (default 2, `0` disables), with exponential backoff and honoring `Retry-After` (up to 60 s):

- **GET** requests: on network errors, timeouts, 429, 500, 502, 503 and 504.
- **POST** requests (`download`, `info`, `listChannel`): **only** on 429 and 503, where the API guarantees nothing was queued. The API has no idempotency key, so a POST that times out or gets a 5xx is never retried: it may already have started (and will bill) a download. Check your jobs before resubmitting.

```ts
new VidKraken({ apiKey, maxRetries: 4, timeoutMs: 30_000, baseUrl, fetch });
```

### Rate limits

Each account can have 100 downloads queued or in progress at once. `download()` returns the JSON response with an extra **non-enumerable** `rateLimit` property parsed from the IETF `RateLimit-Policy` / `RateLimit` headers, so it never shows up in `JSON.stringify` or spreads:

```ts
const submission = await vk.download({ url });
submission.rateLimit; // { policy: "pending", limit: 100, remaining: 37, quotaUnit: "concurrent-requests" }
if (submission.rateLimit?.remaining === 0) {
  // wait for some jobs to finish before submitting more
}
```

A 429 `VidKrakenError` carries the same `rateLimit` plus `retryAfter`.

## CLI

```sh
npx vidkraken download https://youtu.be/dQw4w9WgXcQ -f 720
```

```
vidkraken download <url> [-f FORMAT] [--start S] [--end S] [--language TAG]
                         [-o PATH|DIR] [--no-wait] [--webhook URL] [--timeout SEC]
vidkraken status <jobId>
vidkraken info <url> [--audio-tracks]
vidkraken me

Global: --json, -q/--quiet, --api-key KEY, -h/--help, -v/--version
```

- `download` submits, shows progress on stderr, saves the file (default `./<title>.<ext>`) and prints its path on stdout. `--no-wait` prints only the job id. `--start` / `--end` take seconds or `[h:]m:s`.
- `--json` prints exactly one JSON document on stdout: the result, or `{ "error": { "code", "message", ... } }` on failure. Human-readable messages and progress always go to stderr (`-q` silences progress).
- Exit codes: `0` success, `1` API or job error (`code: message` on stderr), `2` usage error (bad flags, missing API key).
- No prompts, and no color unless both stdout and stderr are terminals and `NO_COLOR` is unset, so it is safe to drive from scripts and AI agents.
- The API key comes from `--api-key` or `VIDKRAKEN_API_KEY`; `VIDKRAKEN_BASE_URL` overrides the API endpoint.

## Links

- Documentation: https://vidkraken.com/docs
- For LLMs and agents: https://vidkraken.com/llms.txt
- OpenAPI spec: https://vidkraken.com/openapi.yaml
- Support: hello@vidkraken.com

## License

MIT © Vid Kraken

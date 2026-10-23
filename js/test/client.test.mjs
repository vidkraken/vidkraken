import assert from "node:assert/strict";
import { mkdtemp, readFile, readdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, describe, test } from "node:test";
import {
  VERSION,
  VidKraken,
  VidKrakenConnectionError,
  VidKrakenError,
  VidKrakenJobFailedError,
  VidKrakenWaitTimeoutError,
  extensionFor,
  parseRateLimitHeaders,
  parseRetryAfter,
  suggestedFilename,
} from "../dist/index.js";
import {
  COMPLETED,
  FAILED,
  PENDING,
  QUEUED,
  RATE_HEADERS,
  SUBMITTED,
  hangingResponse,
  mockFetch,
} from "./helpers.mjs";

const client = (fetch, opts = {}) => new VidKraken({ apiKey: "test-key", fetch, ...opts });

describe("requests", () => {
  test("sends auth, user agent and JSON body to the base URL", async () => {
    const fetch = mockFetch([{ body: SUBMITTED, headers: RATE_HEADERS }]);
    const res = await client(fetch).download({ url: "https://youtu.be/dQw4w9WgXcQ", format: "720" });
    const [call] = fetch.calls;
    assert.equal(call.url, "https://vidkraken.com/api/v2/download");
    assert.equal(call.method, "POST");
    assert.equal(call.headers.get("authorization"), "Bearer test-key");
    assert.equal(call.headers.get("user-agent"), `vidkraken-js/${VERSION}`);
    assert.equal(call.headers.get("content-type"), "application/json");
    assert.deepEqual(call.body, { url: "https://youtu.be/dQw4w9WgXcQ", format: "720" });
    assert.equal(res.jobId, "job123");
    assert.equal("pollToken" in res, false, "undocumented pollToken is stripped");
    assert.equal("pollUrl" in res, false);
  });

  test("custom baseUrl, encoded job ids, GET without a body", async () => {
    const fetch = mockFetch([{ body: PENDING }]);
    await client(fetch, { baseUrl: "http://localhost:3000/api/v2/" }).getDownload("a/b");
    assert.equal(fetch.calls[0].url, "http://localhost:3000/api/v2/download/a%2Fb");
    assert.equal(fetch.calls[0].method, "GET");
    assert.equal(fetch.calls[0].headers.get("content-type"), null);
  });

  test("reads the key from VIDKRAKEN_API_KEY and throws a clear error without one", () => {
    const saved = process.env.VIDKRAKEN_API_KEY;
    try {
      delete process.env.VIDKRAKEN_API_KEY;
      assert.throws(() => new VidKraken(), /VIDKRAKEN_API_KEY/);
      process.env.VIDKRAKEN_API_KEY = "from-env";
      assert.doesNotThrow(() => new VidKraken());
    } finally {
      if (saved === undefined) delete process.env.VIDKRAKEN_API_KEY;
      else process.env.VIDKRAKEN_API_KEY = saved;
    }
  });

  test("me, info, getInfo, listChannel hit the documented paths", async () => {
    const fetch = mockFetch([
      { body: { name: "Jane", email: "j@example.com", plan: { id: "starter", renewsAt: null, canceledAt: null }, balances: {} } },
      { body: { jobId: "i1", status: "pending" } },
      { body: { jobId: "i1", status: "pending" } },
      { body: { id: "UC1", entries: [] } },
    ]);
    const vk = client(fetch);
    await vk.me();
    await vk.info({ url: "https://youtu.be/x", includeAudioTracks: true });
    await vk.getInfo("i1");
    await vk.listChannel({ channelUrl: "https://www.youtube.com/@x", maxVideos: 3 });
    assert.deepEqual(
      fetch.calls.map((c) => `${c.method} ${c.url.replace("https://vidkraken.com/api/v2", "")}`),
      ["GET /me", "POST /info", "GET /info/i1", "POST /list-channel"],
    );
    assert.deepEqual(fetch.calls[1].body, { url: "https://youtu.be/x", includeAudioTracks: true });
    assert.deepEqual(fetch.calls[3].body, { channelUrl: "https://www.youtube.com/@x", maxVideos: 3 });
  });
});

describe("errors", () => {
  test("parses the error body into VidKrakenError", async () => {
    const fetch = mockFetch([
      { status: 400, body: { error: "Missing required field: url", code: "INVALID_REQUEST", details: "x" } },
    ]);
    const err = await client(fetch).download({ url: "" }).catch((e) => e);
    assert.ok(err instanceof VidKrakenError);
    assert.equal(err.status, 400);
    assert.equal(err.code, "INVALID_REQUEST");
    assert.equal(err.message, "Missing required field: url");
    assert.equal(err.details, "x");
  });

  test("keeps unknown codes and falls back to the status for bodies without one", async () => {
    const fetch = mockFetch([
      { status: 418, body: { error: "teapot", code: "SOMETHING_NEW" } },
      { status: 404, body: "<html>not found</html>", headers: { "content-type": "text/html" } },
      { status: 403, body: { error: "Credit limit reached", code: "LIMIT_REACHED" }, headers: { "x-request-id": "req_1" } },
    ]);
    const vk = client(fetch, { maxRetries: 0 });
    const a = await vk.me().catch((e) => e);
    assert.equal(a.code, "SOMETHING_NEW");
    assert.equal(a.status, 418);
    const b = await vk.getDownload("x").catch((e) => e);
    assert.equal(b.code, "NOT_FOUND");
    assert.equal(b.message, "HTTP 404");
    const c = await vk.me().catch((e) => e);
    assert.equal(c.code, "LIMIT_REACHED");
    assert.equal(c.requestId, "req_1");
  });

  test("a non-JSON success body is an INVALID_RESPONSE error", async () => {
    const fetch = mockFetch([{ status: 200, body: "oops" }]);
    const err = await client(fetch).me().catch((e) => e);
    assert.equal(err.code, "INVALID_RESPONSE");
  });
});

describe("retry policy", () => {
  test("GET is retried on 500 and on network errors", async () => {
    const fetch = mockFetch([
      { status: 500, body: { error: "boom", code: "INTERNAL_ERROR" } },
      new TypeError("fetch failed"),
      { body: PENDING },
    ]);
    const job = await client(fetch).getDownload("job123");
    assert.equal(job.status, "IN_PROGRESS");
    assert.equal(fetch.calls.length, 3);
  });

  test("GET retries are bounded by maxRetries", async () => {
    const fetch = mockFetch([
      { status: 502, body: { error: "bad gateway", code: "INTERNAL_ERROR" } },
      { status: 502, body: { error: "bad gateway", code: "INTERNAL_ERROR" } },
      { status: 502, body: { error: "bad gateway", code: "INTERNAL_ERROR" } },
    ]);
    const err = await client(fetch).me().catch((e) => e);
    assert.equal(err.status, 502);
    assert.equal(fetch.calls.length, 3); // 1 + maxRetries (2)
  });

  test("POST /download is NOT retried on 500", async () => {
    const fetch = mockFetch([
      { status: 500, body: { error: "Failed to enqueue download job", code: "INTERNAL_ERROR" } },
      { body: SUBMITTED },
    ]);
    const err = await client(fetch).download({ url: "https://youtu.be/x" }).catch((e) => e);
    assert.equal(err.code, "INTERNAL_ERROR");
    assert.equal(fetch.calls.length, 1);
  });

  test("POST /download and POST /info are NOT retried on network errors or timeouts", async () => {
    const fetch = mockFetch([new TypeError("fetch failed"), { body: SUBMITTED }]);
    const err = await client(fetch).download({ url: "https://youtu.be/x" }).catch((e) => e);
    assert.ok(err instanceof VidKrakenConnectionError);
    assert.equal(err.code, "CONNECTION_ERROR");
    assert.match(err.message, /not retried/);
    assert.equal(fetch.calls.length, 1);

    const fetch2 = mockFetch([hangingResponse, { body: { jobId: "i", status: "pending" } }]);
    const err2 = await client(fetch2, { timeoutMs: 20 }).info({ url: "https://youtu.be/x" }).catch((e) => e);
    assert.equal(err2.code, "TIMEOUT");
    assert.equal(fetch2.calls.length, 1);
  });

  test("POST /download is retried on 429, honoring Retry-After", async () => {
    const fetch = mockFetch([
      {
        status: 429,
        body: { error: "Too many pending downloads (100).", code: "TOO_MANY_PENDING" },
        headers: { "Retry-After": "1", "RateLimit-Policy": RATE_HEADERS["RateLimit-Policy"], RateLimit: '"pending";r=0' },
      },
      { body: SUBMITTED, headers: RATE_HEADERS },
    ]);
    const res = await client(fetch).download({ url: "https://youtu.be/x" });
    assert.equal(res.jobId, "job123");
    assert.equal(fetch.calls.length, 2);
    const waited = fetch.calls[1].time - fetch.calls[0].time;
    assert.ok(waited >= 950, `waited ${waited} ms, expected >= 1000`);
  });

  test("POST /download is retried on 503 UNAVAILABLE", async () => {
    const fetch = mockFetch([
      { status: 503, body: { error: "The download could not be queued.", code: "UNAVAILABLE" } },
      { body: SUBMITTED },
    ]);
    const res = await client(fetch).download({ url: "https://youtu.be/x" });
    assert.equal(res.jobId, "job123");
    assert.equal(fetch.calls.length, 2);
  });

  test("maxRetries: 0 disables retries", async () => {
    const fetch = mockFetch([{ status: 503, body: { error: "x", code: "UNAVAILABLE" } }, { body: PENDING }]);
    const err = await client(fetch, { maxRetries: 0 }).getDownload("job123").catch((e) => e);
    assert.equal(err.code, "UNAVAILABLE");
    assert.equal(fetch.calls.length, 1);
  });

  test("a Retry-After over 60 s is surfaced instead of slept on", async () => {
    const fetch = mockFetch([
      { status: 429, body: { error: "slow down", code: "RATE_LIMITED" }, headers: { "Retry-After": "120" } },
    ]);
    const err = await client(fetch).download({ url: "https://youtu.be/x" }).catch((e) => e);
    assert.equal(err.code, "RATE_LIMITED");
    assert.equal(err.retryAfter, 120);
    assert.equal(fetch.calls.length, 1);
  });

  test("GET timeouts are retried", async () => {
    const fetch = mockFetch([hangingResponse, { body: PENDING }]);
    const job = await client(fetch, { timeoutMs: 20 }).getDownload("job123");
    assert.equal(job.status, "IN_PROGRESS");
    assert.equal(fetch.calls.length, 2);
  });
});

describe("rate limits", () => {
  test("download() exposes rateLimit as a non-enumerable property", async () => {
    const fetch = mockFetch([{ body: SUBMITTED, headers: RATE_HEADERS }]);
    const res = await client(fetch).download({ url: "https://youtu.be/x" });
    assert.deepEqual(res.rateLimit, { policy: "pending", limit: 100, remaining: 37, quotaUnit: "concurrent-requests" });
    assert.equal(Object.keys(res).includes("rateLimit"), false);
    assert.equal(JSON.parse(JSON.stringify(res)).rateLimit, undefined);
    assert.deepEqual(Object.keys(res).sort(), ["duration", "jobId", "message", "status", "title"]);
  });

  test("rateLimit is undefined without headers", async () => {
    const fetch = mockFetch([{ body: SUBMITTED }]);
    const res = await client(fetch).download({ url: "https://youtu.be/x" });
    assert.equal(res.rateLimit, undefined);
  });

  test("a 429 error carries retryAfter and rateLimit", async () => {
    const fetch = mockFetch([
      {
        status: 429,
        body: { error: "Too many pending downloads (100).", code: "TOO_MANY_PENDING" },
        headers: { "Retry-After": "30", ...RATE_HEADERS, RateLimit: '"pending";r=0' },
      },
    ]);
    const err = await client(fetch, { maxRetries: 0 }).download({ url: "https://youtu.be/x" }).catch((e) => e);
    assert.equal(err.code, "TOO_MANY_PENDING");
    assert.equal(err.retryAfter, 30);
    assert.equal(err.rateLimit.remaining, 0);
    assert.equal(err.rateLimit.limit, 100);
  });

  test("header parsers", () => {
    const h = new Headers({ "RateLimit-Policy": '"burst";q=10;w=1, "pending";q=100', RateLimit: '"pending";r=5;t=30' });
    assert.deepEqual(parseRateLimitHeaders(h), { policy: "pending", limit: 100, remaining: 5, resetSeconds: 30 });
    assert.equal(parseRateLimitHeaders(new Headers()), undefined);
    assert.equal(parseRetryAfter("30"), 30);
    assert.equal(parseRetryAfter(null), undefined);
    assert.equal(parseRetryAfter(new Date(Date.now() + 10_000).toUTCString()) >= 9, true);
  });
});

describe("waiting", () => {
  test("waitForDownload polls until COMPLETED and reports progress", async () => {
    const fetch = mockFetch([{ body: QUEUED }, { body: PENDING }, { body: COMPLETED }]);
    const seen = [];
    const done = await client(fetch).waitForDownload("job123", {
      pollIntervalMs: 1,
      onProgress: (s) => seen.push(s.status),
    });
    assert.equal(done.status, "COMPLETED");
    assert.equal(done.downloadUrl, COMPLETED.downloadUrl);
    assert.deepEqual(seen, ["IN_QUEUE", "IN_PROGRESS"]);
  });

  test("waitForDownload throws VidKrakenJobFailedError on FAILED", async () => {
    const fetch = mockFetch([{ body: PENDING }, { body: FAILED }]);
    const err = await client(fetch).waitForDownload("job123", { pollIntervalMs: 1 }).catch((e) => e);
    assert.ok(err instanceof VidKrakenJobFailedError);
    assert.equal(err.jobId, "job123");
    assert.equal(err.errorCode, "AGE_RESTRICTED");
    assert.equal(err.retryable, false);
    assert.match(err.message, /^AGE_RESTRICTED: Sign-in required/);
    assert.equal(err.job.status, "FAILED");
  });

  test("NETWORK_ERROR and unknown codes are retryable", async () => {
    const fetch = mockFetch([
      { body: { ...FAILED, errorCode: "NETWORK_ERROR" } },
      { body: { ...FAILED, errorCode: "SOMETHING_NEW" } },
      { body: { ...FAILED, errorCode: null } },
    ]);
    const vk = client(fetch);
    for (let i = 0; i < 3; i++) {
      const err = await vk.waitForDownload("job123").catch((e) => e);
      assert.equal(err.retryable, true);
    }
  });

  test("downloadAndWait submits once then polls", async () => {
    const fetch = mockFetch([{ body: SUBMITTED }, { body: PENDING }, { body: COMPLETED }]);
    const done = await client(fetch).downloadAndWait({ url: "https://youtu.be/x", format: "1080" }, { pollIntervalMs: 1 });
    assert.equal(done.status, "COMPLETED");
    assert.deepEqual(fetch.calls.map((c) => c.method), ["POST", "GET", "GET"]);
  });

  test("waiting survives a transient poll failure but not a 404", async () => {
    const fetch = mockFetch([
      { body: PENDING },
      { status: 500, body: { error: "x", code: "INTERNAL_ERROR" } },
      { body: COMPLETED },
    ]);
    const done = await client(fetch, { maxRetries: 0 }).waitForDownload("job123", { pollIntervalMs: 1 });
    assert.equal(done.status, "COMPLETED");

    const fetch2 = mockFetch([{ status: 404, body: { error: "Download job not found", code: "NOT_FOUND" } }]);
    const err = await client(fetch2).waitForDownload("nope", { pollIntervalMs: 1 }).catch((e) => e);
    assert.equal(err.code, "NOT_FOUND");
  });

  test("waitForDownload times out", async () => {
    const fetch = mockFetch(Array.from({ length: 50 }, () => ({ body: PENDING })));
    const err = await client(fetch).waitForDownload("job123", { pollIntervalMs: 5, timeoutMs: 30 }).catch((e) => e);
    assert.ok(err instanceof VidKrakenWaitTimeoutError);
    assert.equal(err.jobId, "job123");
  });

  test("waitForDownload honors an abort signal", async () => {
    const fetch = mockFetch(Array.from({ length: 50 }, () => ({ body: PENDING })));
    const controller = new AbortController();
    setTimeout(() => controller.abort(), 20);
    const err = await client(fetch).waitForDownload("job123", { pollIntervalMs: 5, signal: controller.signal }).catch((e) => e);
    assert.equal(err.name, "AbortError");
  });

  test("infoAndWait resolves with the result; failures carry errorCode", async () => {
    const result = { title: "T", duration: 10, url: "https://youtu.be/x", audio: [{ type: "original", language: "en" }] };
    const fetch = mockFetch([
      { body: { jobId: "i1", status: "pending" } },
      { body: { jobId: "i1", status: "pending" } },
      { body: { jobId: "i1", status: "success", result } },
      { body: { jobId: "i2", status: "pending" } },
      { body: { jobId: "i2", status: "failed", error: "Video unavailable", errorCode: "PRIVATE_VIDEO" } },
    ]);
    const vk = client(fetch);
    const done = await vk.infoAndWait({ url: "https://youtu.be/x" }, { pollIntervalMs: 1 });
    assert.deepEqual(done.result, result);
    const err = await vk.infoAndWait({ url: "https://youtu.be/y" }, { pollIntervalMs: 1 }).catch((e) => e);
    assert.ok(err instanceof VidKrakenJobFailedError);
    assert.equal(err.kind, "info");
    assert.equal(err.errorCode, "PRIVATE_VIDEO");
    assert.equal(err.message, "PRIVATE_VIDEO: Video unavailable");
    assert.equal(err.retryable, false);
  });
});

describe("files", () => {
  const dirs = [];
  after(() => Promise.all(dirs.map((d) => rm(d, { recursive: true, force: true }))));

  test("extensions and file names", () => {
    const job = (actualFormat, downloadUrl = "https://cdn/x.webm") => ({ ...COMPLETED, format: actualFormat, actualFormat, downloadUrl });
    assert.equal(extensionFor(job("1080")), "mp4");
    assert.equal(extensionFor(job("360")), "mp4");
    assert.equal(extensionFor(job("audio-mp3")), "mp3");
    assert.equal(extensionFor(job("audio-aac")), "m4a");
    assert.equal(extensionFor(job("audio")), "webm");
    assert.equal(extensionFor(job("audio", "https://cdn/x.m4a?sig=1")), "m4a");
    assert.equal(suggestedFilename({ ...COMPLETED, title: 'AC/DC: "Thunder" <live>?' }), "AC DC Thunder live.mp4");
    assert.equal(suggestedFilename({ ...COMPLETED, title: "..." }), "job123.mp4");
  });

  test("saveFile streams to a directory with the suggested name, without the API key", async () => {
    const dir = await mkdtemp(join(tmpdir(), "vk-test-"));
    dirs.push(dir);
    const fetch = mockFetch([
      () => new Response("hello world", { status: 200, headers: { "content-length": "11" } }),
    ]);
    const progress = [];
    const saved = await client(fetch).saveFile(COMPLETED, dir, { onProgress: (p) => progress.push(p) });
    assert.equal(saved.path, join(dir, "Rick Astley - Never Gonna Give You Up.mp4"));
    assert.equal(saved.bytes, 11);
    assert.equal(await readFile(saved.path, "utf8"), "hello world");
    assert.equal(fetch.calls[0].url, COMPLETED.downloadUrl);
    assert.equal(fetch.calls[0].headers.get("authorization"), null);
    assert.deepEqual(progress.at(-1), { bytes: 11, total: 11 });
    assert.deepEqual(await readdir(dir), ["Rick Astley - Never Gonna Give You Up.mp4"]);
  });

  test("saveFile fails cleanly on HTTP errors and truncated bodies", async () => {
    const dir = await mkdtemp(join(tmpdir(), "vk-test-"));
    dirs.push(dir);
    const fetch = mockFetch([
      () => new Response("gone", { status: 404 }),
      () => new Response("short", { status: 200, headers: { "content-length": "100" } }),
    ]);
    const vk = client(fetch);
    const a = await vk.saveFile("https://cdn/x.mp4", join(dir, "a.mp4")).catch((e) => e);
    assert.equal(a.status, 404);
    const b = await vk.saveFile("https://cdn/x.mp4", join(dir, "b.mp4")).catch((e) => e);
    assert.match(b.message, /Incomplete download/);
    assert.deepEqual(await readdir(dir), [], "no partial files left behind");
  });
});


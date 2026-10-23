import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, describe, test } from "node:test";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { runCli } from "../dist/cli-run.js";
import { COMPLETED, FAILED, PENDING, RATE_HEADERS, SUBMITTED, captureIO, mockFetch } from "./helpers.mjs";

const pkg = JSON.parse(await readFile(new URL("../package.json", import.meta.url), "utf8"));
const CLI = fileURLToPath(new URL("../dist/cli.js", import.meta.url));
const run = async (argv, fetch = mockFetch([]), opts) => {
  const { io, out } = captureIO(fetch, opts);
  const code = await runCli(argv, io);
  return { code, ...out, fetch };
};

const dirs = [];
after(() => Promise.all(dirs.map((d) => rm(d, { recursive: true, force: true }))));
const tempDir = async () => {
  const d = await mkdtemp(join(tmpdir(), "vk-cli-"));
  dirs.push(d);
  return d;
};

describe("cli: help, version, usage errors", () => {
  test("--help and --version", async () => {
    const help = await run(["--help"]);
    assert.equal(help.code, 0);
    assert.match(help.stdout, /^vidkraken /);
    assert.match(help.stdout, /vidkraken download <url>/);
    assert.equal(help.stderr, "");

    const version = await run(["--version"]);
    assert.equal(version.code, 0);
    assert.equal(version.stdout, `${pkg.version}\n`);

    const versionJson = await run(["--version", "--json"]);
    assert.deepEqual(JSON.parse(versionJson.stdout), { version: pkg.version });
  });

  test("usage errors exit 2 without calling the API", async () => {
    const cases = [
      [],
      ["frobnicate"],
      ["download"],
      ["download", "https://youtu.be/x", "--bogus"],
      ["download", "https://youtu.be/x", "-f", "4k"],
      ["download", "https://youtu.be/x", "--start", "abc"],
      ["download", "https://youtu.be/x", "--start", "90", "--end", "30"],
      ["download", "https://youtu.be/x", "--no-wait", "-o", "x.mp4"],
      ["me", "--format", "720"],
      ["status"],
      ["info", "https://youtu.be/x", "--timeout", "-1"],
    ];
    for (const argv of cases) {
      const r = await run(argv);
      assert.equal(r.code, 2, `exit code for ${JSON.stringify(argv)}`);
      assert.equal(r.fetch.calls.length, 0);
      assert.notEqual(r.stderr, "");
    }
  });

  test("a missing API key is a usage error", async () => {
    const r = await run(["me"], mockFetch([]), { env: {} });
    assert.equal(r.code, 2);
    assert.match(r.stderr, /VIDKRAKEN_API_KEY/);
  });

  test("--json usage errors print a JSON error on stdout", async () => {
    const r = await run(["download", "https://youtu.be/x", "-f", "4k", "--json"]);
    assert.equal(r.code, 2);
    assert.equal(JSON.parse(r.stdout).error.code, "USAGE");
  });
});

describe("cli: download", () => {
  test("--no-wait prints the job id; --json prints the submission with rateLimit", async () => {
    const plain = await run(["download", "https://youtu.be/x", "--no-wait"], mockFetch([{ body: SUBMITTED, headers: RATE_HEADERS }]));
    assert.equal(plain.code, 0);
    assert.equal(plain.stdout, "job123\n");

    const json = await run(
      ["download", "https://youtu.be/x", "--no-wait", "--json"],
      mockFetch([{ body: SUBMITTED, headers: RATE_HEADERS }]),
    );
    assert.equal(json.code, 0);
    const out = JSON.parse(json.stdout);
    assert.equal(out.jobId, "job123");
    assert.equal(out.pollToken, undefined);
    assert.deepEqual(out.rateLimit, { policy: "pending", limit: 100, remaining: 37, quotaUnit: "concurrent-requests" });
  });

  test("maps flags to the request body", async () => {
    const r = await run(
      ["download", "https://youtu.be/x", "-f", "1080p", "--start", "1:30", "--end", "120", "--language", "en-US", "--webhook", "https://example.com/h", "--no-wait"],
      mockFetch([{ body: SUBMITTED }]),
    );
    assert.equal(r.code, 0);
    assert.deepEqual(r.fetch.calls[0].body, {
      url: "https://youtu.be/x",
      format: "1080",
      startTime: 90,
      endTime: 120,
      language: "en-US",
      webhookUrl: "https://example.com/h",
    });
  });

  test("waits, shows progress on stderr, saves the file and prints its path", async () => {
    const dir = await tempDir();
    const fetch = mockFetch([
      { body: SUBMITTED, headers: RATE_HEADERS },
      { body: PENDING },
      { body: COMPLETED },
      () => new Response("hello world", { status: 200, headers: { "content-length": "11" } }),
    ]);
    const r = await run(["download", "https://youtu.be/x", "-f", "1080", "-o", dir + "/"], fetch);
    assert.equal(r.code, 0, r.stderr);
    const expected = join(dir, "Rick Astley - Never Gonna Give You Up.mp4");
    assert.equal(r.stdout, `${expected}\n`);
    assert.equal(await readFile(expected, "utf8"), "hello world");
    assert.match(r.stderr, /Submitted job123/);
    assert.match(r.stderr, /downloading 41% \(43\.5 MB of 102\.3 MB, ~0:12 left\)/);
    assert.doesNotMatch(r.stderr, /\x1b\[/, "no ANSI escapes when not a TTY");
  });

  test("--json prints only the completed status plus path to stdout", async () => {
    const dir = await tempDir();
    const fetch = mockFetch([
      { body: SUBMITTED },
      { body: COMPLETED },
      () => new Response("hello world", { status: 200 }),
    ]);
    const r = await run(["download", "https://youtu.be/x", "-o", join(dir, "out.mp4"), "--json", "-q"], fetch);
    assert.equal(r.code, 0, r.stderr);
    const out = JSON.parse(r.stdout);
    assert.equal(out.status, "COMPLETED");
    assert.equal(out.path, join(dir, "out.mp4"));
    assert.equal(out.bytes, 11);
    assert.equal(r.stdout.trim().split("\n").length, 1);
    assert.equal(r.stderr, "", "--quiet silences progress");
  });

  test("a FAILED job exits 1 with the errorCode on stderr (and JSON on stdout with --json)", async () => {
    const r = await run(["download", "https://youtu.be/x"], mockFetch([{ body: SUBMITTED }, { body: FAILED }]));
    assert.equal(r.code, 1);
    assert.match(r.stderr, /error: AGE_RESTRICTED: Sign-in required/);
    assert.equal(r.stdout, "");

    const j = await run(["download", "https://youtu.be/x", "--json"], mockFetch([{ body: SUBMITTED }, { body: FAILED }]));
    assert.equal(j.code, 1);
    assert.deepEqual(JSON.parse(j.stdout).error, {
      code: "AGE_RESTRICTED",
      message: "AGE_RESTRICTED: Sign-in required by YouTube for this video",
      jobId: "job123",
      retryable: false,
    });
  });

  test("API errors exit 1 and print code + message", async () => {
    const r = await run(
      ["download", "https://youtu.be/x", "--api-key", "bad"],
      mockFetch([{ status: 401, body: { error: "Invalid API key", code: "UNAUTHORIZED" } }]),
    );
    assert.equal(r.code, 1);
    assert.equal(r.stderr, "error: UNAUTHORIZED: Invalid API key\n");
    assert.equal(r.fetch.calls[0].headers.get("authorization"), "Bearer bad", "--api-key overrides env");
  });
});

describe("cli: status, info, me", () => {
  test("status prints key/value lines or JSON", async () => {
    const r = await run(["status", "job123"], mockFetch([{ body: PENDING }]));
    assert.equal(r.code, 0);
    assert.match(r.stdout, /^jobId: +job123$/m);
    assert.match(r.stdout, /^status: +IN_PROGRESS$/m);
    assert.match(r.stdout, /^progress: +downloading 41%/m);

    const j = await run(["status", "job123", "--json"], mockFetch([{ body: COMPLETED }]));
    assert.deepEqual(JSON.parse(j.stdout), COMPLETED);

    const f = await run(["status", "job123"], mockFetch([{ body: FAILED }]));
    assert.equal(f.code, 1, "a FAILED job exits 1, like download");
    assert.match(f.stdout, /^errorCode: +AGE_RESTRICTED \(Sign-in required/m);
  });

  test("status of an unknown job exits 1 with NOT_FOUND", async () => {
    const r = await run(["status", "nope"], mockFetch([{ status: 404, body: { error: "Download job not found", code: "NOT_FOUND" } }]));
    assert.equal(r.code, 1);
    assert.match(r.stderr, /NOT_FOUND: Download job not found/);
  });

  test("info --audio-tracks --json", async () => {
    const result = { title: "T", duration: 213, url: "https://youtu.be/x", audio: [{ type: "original", language: "en" }] };
    const fetch = mockFetch([
      { body: { jobId: "i1", status: "pending" } },
      { body: { jobId: "i1", status: "pending" } },
      { body: { jobId: "i1", status: "success", result } },
    ]);
    const r = await run(["info", "https://youtu.be/x", "--audio-tracks", "--json"], fetch);
    assert.equal(r.code, 0);
    assert.deepEqual(JSON.parse(r.stdout), result);
    assert.deepEqual(fetch.calls[0].body, { url: "https://youtu.be/x", includeAudioTracks: true });

    const h = await run(["info", "https://youtu.be/x"], mockFetch([{ body: { jobId: "i1", status: "pending" } }, { body: { jobId: "i1", status: "success", result } }]));
    assert.match(h.stdout, /^duration: +3:33 \(213s\)$/m);
    assert.match(h.stdout, /^audio: +original en$/m);
  });

  test("me prints plan and balances", async () => {
    const me = {
      name: "Jane Developer",
      email: "jane@example.com",
      plan: { id: "starter", renewsAt: 1758326400000, canceledAt: null },
      balances: {
        info: { granted: 5000, used: 310, remaining: 4690, unlimited: false, resetsAt: 1758326400000 },
        bandwidth_mb: { granted: 400000, used: 52480, remaining: 347520, unlimited: false, resetsAt: 1758326400000 },
      },
    };
    const r = await run(["me"], mockFetch([{ body: me }]));
    assert.equal(r.code, 0);
    assert.equal(
      r.stdout,
      [
        "name:  Jane Developer",
        "email: jane@example.com",
        "plan:  starter (renews 2025-09-20)",
        "balances:",
        "  bandwidth_mb  347520 of 400000 remaining (resets 2025-09-20)",
        "  info          4690 of 5000 remaining (resets 2025-09-20)",
        "",
      ].join("\n"),
    );
    const j = await run(["me", "--json"], mockFetch([{ body: me }]));
    assert.deepEqual(JSON.parse(j.stdout), me);
  });
});

describe("cli: the built bin", () => {
  const exec = promisify(execFile);
  test("node dist/cli.js --help exits 0", async () => {
    const { stdout } = await exec(process.execPath, [CLI, "--help"]);
    assert.match(stdout, /Usage:/);
  });
  test("node dist/cli.js with no command exits 2", async () => {
    const err = await exec(process.execPath, [CLI], { env: { PATH: process.env.PATH } }).catch((e) => e);
    assert.equal(err.code, 2);
  });
  test("node dist/cli.js me without a key exits 2", async () => {
    const err = await exec(process.execPath, [CLI, "me"], { env: { PATH: process.env.PATH } }).catch((e) => e);
    assert.equal(err.code, 2);
    assert.match(err.stderr, /VIDKRAKEN_API_KEY/);
  });
});

// Shared test helpers: a scripted mock fetch. Tests never hit the real API.

export function jsonResponse(status, body, headers = {}) {
  return new Response(body === undefined ? null : typeof body === "string" ? body : JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

/**
 * A fetch that answers from a script, in order. Each entry is
 * `{ status?, body?, headers? }`, an Error (thrown as a network failure), or a
 * function `(url, init) => Response | Promise<Response>`.
 */
export function mockFetch(script) {
  const queue = [...script];
  const calls = [];
  const fn = async (url, init = {}) => {
    calls.push({
      url: String(url),
      method: init.method ?? "GET",
      headers: new Headers(init.headers),
      body: typeof init.body === "string" ? JSON.parse(init.body) : undefined,
      time: Date.now(),
    });
    const next = queue.shift();
    if (next === undefined) throw new Error(`mockFetch: unexpected request ${init.method ?? "GET"} ${url}`);
    if (next instanceof Error) throw next;
    if (typeof next === "function") return next(url, init);
    return jsonResponse(next.status ?? 200, next.body, next.headers);
  };
  fn.calls = calls;
  fn.remaining = () => queue.length;
  return fn;
}

/** A fetch that only settles when its signal aborts (to exercise timeouts). */
export function hangingResponse(_url, init) {
  return new Promise((_resolve, reject) => {
    init.signal.addEventListener("abort", () => {
      const err = new Error("aborted");
      err.name = "AbortError";
      reject(err);
    });
  });
}

export const RATE_HEADERS = {
  "RateLimit-Policy": '"pending";q=100;qu="concurrent-requests"',
  RateLimit: '"pending";r=37',
};

export const SUBMITTED = {
  jobId: "job123",
  title: "Rick Astley - Never Gonna Give You Up",
  duration: 213,
  status: "IN_QUEUE",
  message: "Download job enqueued. Poll GET /api/v2/download/{jobId} for status.",
  pollToken: "secret-token",
  pollUrl: "https://vidkraken.com/api/v2/poll/secret-token",
};

const base = {
  jobId: "job123",
  title: "Rick Astley - Never Gonna Give You Up",
  duration: 213,
  url: "https://youtu.be/dQw4w9WgXcQ",
  format: "1080",
  language: null,
};

export const PENDING = {
  ...base,
  status: "IN_PROGRESS",
  progress: {
    stage: "downloading",
    percent: 41,
    bytesDownloaded: 43500000,
    totalBytes: 102300000,
    totalBytesIsEstimate: false,
    etaSeconds: 12,
    updatedAt: "2026-09-23T20:10:19Z",
  },
};

export const QUEUED = { ...base, status: "IN_QUEUE" };

export const COMPLETED = {
  ...base,
  status: "COMPLETED",
  actualFormat: "1080",
  downloadUrl: "https://proxy.vidkraken.com/media/job123.mp4",
  fileSize: 11,
};

export const FAILED = { ...base, status: "FAILED", errorCode: "AGE_RESTRICTED" };

/** Collects CLI output. */
export function captureIO(fetch, { env = { VIDKRAKEN_API_KEY: "test-key" }, tty = false } = {}) {
  const out = { stdout: "", stderr: "" };
  return {
    out,
    io: {
      stdout: { write: (s) => (out.stdout += s), isTTY: tty },
      stderr: { write: (s) => (out.stderr += s), isTTY: tty },
      env,
      fetch,
      pollIntervalMs: 1,
    },
  };
}

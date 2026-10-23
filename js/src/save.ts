import { VidKrakenConnectionError, VidKrakenError, defaultCodeForStatus } from "./errors.js";
import { suggestedFilename } from "./filename.js";
import type { DownloadJobCompleted } from "./types.js";

export interface SaveFileProgress {
  /** Bytes written so far. */
  bytes: number;
  /** Total bytes from `Content-Length`, when known. */
  total?: number;
}

export interface SaveFileOptions {
  /** Called as chunks are written. */
  onProgress?: (progress: SaveFileProgress) => void;
  signal?: AbortSignal;
  /** Custom fetch (defaults to the global one). */
  fetch?: typeof fetch;
}

export interface SavedFile {
  /** Absolute path of the written file. */
  path: string;
  /** Bytes written. */
  bytes: number;
}

/**
 * Stream a finished download to disk (Node.js only; `node:fs` is loaded lazily
 * so importing the SDK stays runtime-neutral).
 *
 * `source` is a `downloadUrl` or a COMPLETED job status. `path` may be a file
 * path, an existing directory, or a path ending in `/`; for a directory (or
 * when omitted: the current directory) the file name comes from
 * {@link suggestedFilename}. The file is written to `<path>.part` and renamed
 * when complete, so a partial file never has the final name. An existing
 * file at the final path is overwritten.
 */
export async function saveFile(
  source: string | DownloadJobCompleted,
  path?: string,
  options: SaveFileOptions = {},
): Promise<SavedFile> {
  const [fs, fsp, nodePath, { pipeline }] = await Promise.all([
    import("node:fs"),
    import("node:fs/promises"),
    import("node:path"),
    import("node:stream/promises"),
  ]);

  const url = typeof source === "string" ? source : source.downloadUrl;
  if (!url) throw new TypeError("saveFile: no downloadUrl (is the job COMPLETED?)");

  const defaultName = () => {
    if (typeof source !== "string") return suggestedFilename(source);
    try {
      const base = nodePath.posix.basename(new URL(source).pathname);
      if (base) return decodeURIComponent(base);
    } catch {
      // fall through
    }
    return "download";
  };

  let target = path ?? defaultName();
  const endsWithSep = path !== undefined && /[\\/]$/.test(path);
  const isDir = endsWithSep || (await fsp.stat(target).then((s) => s.isDirectory(), () => false));
  if (path !== undefined && isDir) target = nodePath.join(path, defaultName());
  target = nodePath.resolve(target);
  await fsp.mkdir(nodePath.dirname(target), { recursive: true });

  const fetchImpl = options.fetch ?? globalThis.fetch;
  let res: Response;
  try {
    res = await fetchImpl(url, { signal: options.signal });
  } catch (err) {
    if (options.signal?.aborted) throw err;
    throw new VidKrakenConnectionError({
      code: "CONNECTION_ERROR",
      message: `Could not fetch ${url}: ${(err as Error)?.message ?? String(err)}`,
      cause: err,
    });
  }
  if (!res.ok || !res.body) {
    throw new VidKrakenError({
      status: res.status,
      code: defaultCodeForStatus(res.status),
      message: `Fetching the file failed with HTTP ${res.status}`,
    });
  }

  const lengthHeader = res.headers.get("content-length");
  const total = lengthHeader !== null && /^\d+$/.test(lengthHeader) ? Number(lengthHeader) : undefined;
  const body = res.body as unknown as AsyncIterable<Uint8Array>;
  const onProgress = options.onProgress;
  let bytes = 0;

  const partPath = `${target}.part`;
  try {
    await pipeline(
      async function* () {
        for await (const chunk of body) {
          bytes += chunk.byteLength;
          onProgress?.(total === undefined ? { bytes } : { bytes, total });
          yield chunk;
        }
      },
      fs.createWriteStream(partPath),
      ...(options.signal ? [{ signal: options.signal }] : []),
    );
    if (total !== undefined && bytes !== total) {
      throw new VidKrakenConnectionError({
        code: "CONNECTION_ERROR",
        message: `Incomplete download: received ${bytes} of ${total} bytes`,
      });
    }
    await fsp.rename(partPath, target);
  } catch (err) {
    await fsp.rm(partPath, { force: true }).catch(() => {});
    throw err;
  }
  return { path: target, bytes };
}

import type { DownloadJobCompleted } from "./types.js";

const AUDIO_EXTENSIONS = new Set(["m4a", "webm", "opus", "ogg", "mp3", "aac", "mp4", "mka", "flac", "wav"]);

function extensionFromUrl(url: string): string | undefined {
  try {
    const path = new URL(url).pathname;
    const match = /\.([A-Za-z0-9]{2,5})$/.exec(path);
    return match?.[1]?.toLowerCase();
  } catch {
    return undefined;
  }
}

/**
 * The file extension (without a dot) for a completed download: `mp4` for
 * video, `mp3` for `audio-mp3`, `m4a` for `audio-aac`, and for `audio` the
 * extension of the `downloadUrl` (the container YouTube served).
 */
export function extensionFor(job: Pick<DownloadJobCompleted, "actualFormat" | "format" | "downloadUrl">): string {
  const format = job.actualFormat ?? job.format;
  if (/^\d+$/.test(format)) return "mp4";
  if (format === "audio-mp3") return "mp3";
  if (format === "audio-aac") return "m4a";
  const fromUrl = extensionFromUrl(job.downloadUrl);
  if (fromUrl && AUDIO_EXTENSIONS.has(fromUrl)) return fromUrl;
  return fromUrl ?? "m4a";
}

/** Make a video title safe to use as a file name on every common OS. */
export function sanitizeFilename(name: string): string {
  const cleaned = name
    // eslint-disable-next-line no-control-regex
    .replace(/[\u0000-\u001f\u007f<>:"/\\|?*]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/^[.\s]+|[.\s]+$/g, "");
  // Keep well under the usual 255-byte limit, leaving room for the extension.
  let out = cleaned;
  while (new TextEncoder().encode(out).length > 200) out = out.slice(0, -1);
  return out.trim();
}

/** A sensible file name for a completed download: `<title>.<ext>`, falling back to the job id. */
export function suggestedFilename(
  job: Pick<DownloadJobCompleted, "actualFormat" | "format" | "downloadUrl" | "title" | "jobId">,
): string {
  const base = sanitizeFilename(job.title ?? "") || job.jobId;
  return `${base}.${extensionFor(job)}`;
}

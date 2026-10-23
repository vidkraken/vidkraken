---
name: vidkraken
description: "Download a YouTube video as MP4 or its audio as MP3/AAC, cut a clip by start and end time, or look up a video's title, duration and audio languages, through the Vid Kraken API or MCP server. Use when a task needs the actual media file from a YouTube URL."
---

# Vid Kraken: download YouTube video and audio

Vid Kraken is a managed YouTube download API. Send a YouTube URL, get back a direct link to the finished MP4 or audio file. Bot detection, proxies and retries are handled server-side; there is nothing to install.

## When to use

- The user needs the media file of a YouTube video: MP4 video, or audio as MP3, AAC (M4A) or the original stream.
- The user wants only part of a video: a clip between a start and end time.
- The user wants a video's title, duration, region restrictions or dubbed audio languages before downloading.

Not for transcripts, comments or search, and only for content the user has the right to download.

## Option A: MCP (in an MCP host)

Connect to the remote MCP server at `https://vidkraken.com/api/mcp/mcp` (Streamable HTTP; the host runs the OAuth sign-in). Tools:

1. `get_video_info`: title, duration, chapters and audio languages.
2. `create_clip`: URL, start and end in seconds, and a format (`audio`, `audio-mp3`, `360`, `480`, `720` or `1080`). Returns a `jobId`.
3. `get_clip_status`: poll with the `jobId` until it is completed; the download link expires about 12 hours later.
4. `get_account_usage`: plan and remaining balance. Free.

## Option B: the `vidkraken` CLI (shell, one command)

With `VIDKRAKEN_API_KEY` set (see below for getting a key), one command submits, waits and saves the file:

```bash
npx vidkraken download "https://youtu.be/dQw4w9WgXcQ" -f 1080 -o ./ --json
```

`pip install vidkraken` installs the same CLI for Python. Add `--start`/`--end` (seconds) for a clip; `vidkraken info <url> --json` returns metadata. Exit code 0 means success, 1 an API or job error (the JSON has `error.code`), 2 a usage error. In code, the same clients are `npm install vidkraken` and `pip install vidkraken`.

## Option C: REST API (any language)

Needs an API key: the user gets one at https://vidkraken.com/login (free to start, no credit card). Read it from an environment variable such as `VIDKRAKEN_API_KEY`; never print it.

1. Submit the download:

   ```bash
   curl -X POST https://vidkraken.com/api/v2/download \
     -H "Authorization: Bearer $VIDKRAKEN_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{"url":"https://youtu.be/dQw4w9WgXcQ","format":"1080"}'
   ```

   The response carries a `jobId`. Optional fields: `startTime` and `endTime` (seconds) for a clip, `language` (BCP-47) for a dubbed audio track, `webhookUrl` to be notified instead of polling.

2. Poll every few seconds until `status` is `COMPLETED` or `FAILED`:

   ```bash
   curl https://vidkraken.com/api/v2/download/JOB_ID \
     -H "Authorization: Bearer $VIDKRAKEN_API_KEY"
   ```

3. On `COMPLETED`, fetch the file from `downloadUrl`. `actualFormat` is the format delivered and `fileSize` its size in bytes.

Formats (`format`): `1080`, `720`, `480` or `360` for MP4 video; `audio` for the original audio stream (fastest, not re-encoded), `audio-aac` for AAC in M4A, `audio-mp3` for a 320 kbps MP3. The default is `audio`.

Metadata only: `POST https://vidkraken.com/api/v2/info` with `{"url": "..."}` (add `"includeAudioTracks": true` for languages), then poll `GET /api/v2/info/JOB_ID`.

## Errors

- A `FAILED` job has an `errorCode`. Only `NETWORK_ERROR` is worth retrying right away. `VIDEO_PROCESSING`, `VIDEO_PREMIERE` and `LIVE_STREAM_IN_PROGRESS` are worth retrying later, once the video has finished processing, aired or ended. The rest (`AGE_RESTRICTED`, `GEO_RESTRICTED`, `PRIVATE_VIDEO`, `VIDEO_NOT_FOUND`, `MEMBERS_ONLY`, ...) describe the video: tell the user instead of retrying.
- A rejected request returns an HTTP error whose JSON has a `code`: `INVALID_REQUEST` (400, fix the request), `UNAUTHORIZED` (401, bad API key), `LIMIT_REACHED` (403, the plan's allowance is used up), `VIDEO_NOT_FOUND` (404), `TOO_MANY_PENDING` (429, 100 downloads already queued or running: wait `Retry-After` seconds), `INTERNAL_ERROR` (500) or `UNAVAILABLE` (503); retry only the last two, after a short backoff.
- The `RateLimit` header on `POST /download` (`"pending";r=37`) says how many more downloads can be queued now.

## Cost

Bandwidth only, at the delivered file size with a 20 MB minimum per download. Failed downloads, polling and metadata lookups are free. Pricing: https://vidkraken.com/pricing.md. Questions: hello@vidkraken.com.

## Reference

- API docs (Markdown): https://vidkraken.com/docs.md
- OpenAPI: https://vidkraken.com/openapi.json
- Overview: https://vidkraken.com/llms.txt

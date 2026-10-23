---
name: vidkraken-audio
description: "Convert a YouTube video to MP3 or AAC (M4A), or get its original audio stream, through the Vid Kraken API, CLI or MCP server. Use when a task needs only the audio of a YouTube video: transcription, podcasts, music analysis or dubbing."
---

# Vid Kraken: YouTube to MP3 / AAC

Get just the audio of a YouTube video as a file. Vid Kraken downloads it server-side (no yt-dlp, proxies or cookies) and returns a direct download link.

## Pick a format

- `audio-mp3`: a real MP3 at 320 kbps CBR. Use when the user or a tool needs MP3 specifically.
- `audio-aac`: YouTube's AAC stream in an M4A container (~128 kbps), not re-encoded. The most widely compatible choice.
- `audio`: the best original stream (M4A or WebM, ~130-160 kbps), not re-encoded. Fastest and highest quality; fine for transcription.

Add `language` (BCP-47, e.g. `en`, `es`) to pick a dubbed audio track when the video has several.

## One command (CLI)

With `VIDKRAKEN_API_KEY` set (free key at https://vidkraken.com/login):

```bash
npx vidkraken download "https://youtu.be/dQw4w9WgXcQ" -f audio-mp3 -o ./ --json
```

`pip install vidkraken` installs the same CLI. Exit code 0 means the file was saved; 1 means an API or job error (the JSON has `error.code`).

## REST API

```bash
curl -X POST https://vidkraken.com/api/v2/download \
  -H "Authorization: Bearer $VIDKRAKEN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://youtu.be/dQw4w9WgXcQ","format":"audio-mp3"}'
```

Poll `GET https://vidkraken.com/api/v2/download/JOB_ID` until `status` is `COMPLETED`, then fetch `downloadUrl`. `actualFormat` reports what was delivered (`audio-aac` falls back to `audio` when a video has no AAC stream).

## MCP

In an MCP host connected to https://vidkraken.com/api/mcp/mcp, call `create_clip` with the video's full time range and format `audio` or `audio-mp3`, then `get_clip_status`.

## Cost

Bandwidth only (20 MB minimum per download); failed downloads are free. Only for content the user has the right to download. Full guide: https://vidkraken.com/youtube-to-mp3-api

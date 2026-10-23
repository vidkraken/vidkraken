---
name: vidkraken-clips
description: "Cut a clip from a YouTube video by start and end time and get it as MP4 (360p-1080p) or audio, without downloading the whole video, through the Vid Kraken API, CLI or MCP server. Use when the user wants only part of a YouTube video."
---

# Vid Kraken: cut a YouTube clip

Get only a section of a YouTube video, as MP4 or audio, by start and end time. Vid Kraken trims server-side and returns a direct download link to the clip.

## Times

- `startTime` and `endTime` are whole seconds: start inclusive, end exclusive, and `startTime < endTime <= duration`.
- Convert timestamps first: 12:30 is 750, 1:02:05 is 3725.
- Unsure of the length? Check `duration` with `vidkraken info <url> --json` (or `POST /api/v2/info`) before cutting.

## One command (CLI)

With `VIDKRAKEN_API_KEY` set (free key at https://vidkraken.com/login):

```bash
npx vidkraken download "https://youtu.be/dQw4w9WgXcQ" -f 1080 --start 30 --end 90 -o ./ --json
```

Formats: `1080`, `720`, `480`, `360` for MP4, or `audio`, `audio-aac`, `audio-mp3` for an audio clip. `pip install vidkraken` installs the same CLI.

## REST API

```bash
curl -X POST https://vidkraken.com/api/v2/download \
  -H "Authorization: Bearer $VIDKRAKEN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://youtu.be/dQw4w9WgXcQ","format":"1080","startTime":30,"endTime":90}'
```

Poll `GET https://vidkraken.com/api/v2/download/JOB_ID` until `status` is `COMPLETED`, then fetch `downloadUrl`. An out-of-range time is a 400 with `code: INVALID_REQUEST`: fix the times instead of retrying.

## MCP

In an MCP host connected to https://vidkraken.com/api/mcp/mcp: `get_video_info` (duration and chapters, handy for picking times), then `create_clip` with the URL, start, end and format, then `get_clip_status` until it returns the download link (valid about 12 hours).

## Cost

Bandwidth for the clip only (20 MB minimum per download); failed downloads are free. Only for content the user has the right to use.

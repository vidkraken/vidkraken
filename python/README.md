# Vid Kraken Python SDK and CLI

The official Python client for [Vid Kraken](https://vidkraken.com), a REST API
that downloads YouTube videos and audio on its servers and returns a CDN link
to the finished file. You pay for bandwidth only, and failed downloads are never
charged.

One package gives you:

- `VidKraken` and `AsyncVidKraken`: typed sync and async clients built on [httpx](https://www.python-httpx.org/)
- a `vidkraken` command-line tool for scripts, shells and AI agents

> Use only with content you have the right to download.

## Install

```bash
pip install vidkraken
```

Requires Python 3.9 or newer. The only runtime dependency is `httpx`.

## Get an API key

Sign in at **https://vidkraken.com/login** and copy your key from the dashboard.
Then either export it:

```bash
export VIDKRAKEN_API_KEY="vk_..."
```

or pass it to the client with `VidKraken(api_key="vk_...")`.

## Quickstart (sync)

```python
from vidkraken import VidKraken

with VidKraken() as vk:  # reads VIDKRAKEN_API_KEY
    done = vk.download_and_wait(
        "https://youtu.be/dQw4w9WgXcQ",
        format="720",                       # "1080", "720", "480", "360", "audio", "audio-aac", "audio-mp3"
        on_progress=lambda s: print(s.status, s.progress and s.progress.percent),
    )
    print(done.download_url, done.actual_format, done.file_size)
    path = vk.save_file(done, "downloads/")  # named after the video title
    print("saved to", path)
```

Step by step, if you'd rather poll yourself or use a webhook:

```python
job = vk.download("https://youtu.be/dQw4w9WgXcQ", format="audio-mp3", start_time=30, end_time=90)
print(job.job_id, job.title, job.rate_limit.remaining)

status = vk.get_download(job.job_id)     # IN_QUEUE / IN_PROGRESS / COMPLETED / FAILED
status = vk.wait_for_download(job.job_id, poll_interval=3.0, timeout=600)
```

Pass `webhook_url="https://example.com/hooks/vidkraken"` to `download()` and Vid
Kraken POSTs the final status to that URL. The body is the same as `get_download()`
returns, so `DownloadStatus.from_dict(payload)` parses it.

## Quickstart (async)

`AsyncVidKraken` has the same methods. Each one is a coroutine.

```python
import asyncio
from vidkraken import AsyncVidKraken

async def main() -> None:
    async with AsyncVidKraken() as vk:
        urls = ["https://youtu.be/dQw4w9WgXcQ", "https://youtu.be/9bZkp7q19f0"]
        results = await asyncio.gather(*(vk.download_and_wait(u, format="audio") for u in urls))
        for done in results:
            await vk.save_file(done, "downloads/")

asyncio.run(main())
```

## Video info

```python
info = vk.info_and_wait("https://youtu.be/dQw4w9WgXcQ", include_audio_tracks=True)
print(info.title, info.duration)
for track in info.audio or []:
    print(track.language, track.type)   # e.g. "en original", "es dubbed"
```

`info()`, `get_info()` and `wait_for_info()` are the separate steps behind `info_and_wait()`.

## Account and channel listing

```python
me = vk.me()
print(me.plan.id, me.balances["bandwidth_mb"].remaining)

listing = vk.list_channel("https://www.youtube.com/@RickAstleyYT", max_videos=25)  # paid add-on
for entry in listing.entries:
    print(entry["id"], entry["title"])
```

## Saving files

`save_file(source, path=None)` streams a finished download to disk in chunks.

- `source` is a completed `DownloadStatus` or a raw `downloadUrl` string.
- `path` is a file path, a directory, or `None` for the current directory. When
  you give a directory, the file is named `<title>.<ext>`: `.mp4` for video,
  `.mp3` for `audio-mp3`, `.m4a` for `audio-aac`, and the CDN's own extension
  for `audio`.
- The file is written to `<name>.part` and renamed once it is complete.
- Your API key is never sent to the file host.

## Responses

Responses are frozen dataclasses with snake_case attributes, for example
`status.download_url` and `status.progress.eta_seconds`. The original JSON is kept
on `.raw`, and `obj["camelCaseKey"]` / `obj.get(...)` read from it, so fields
added to the API later are still reachable.

## Errors

Every error inherits from `vidkraken.VidKrakenError`, which has `.status`, `.code`,
`.message`, `.details` and `.retry_after`. Branch on `.code`, not on the message.

| Exception | When | `code` |
|---|---|---|
| `BadRequestError` | 400 | `INVALID_REQUEST` |
| `AuthenticationError` | 401 | `UNAUTHORIZED` |
| `PermissionDeniedError` | 403 | `LIMIT_REACHED` |
| `NotFoundError` | 404 | `NOT_FOUND`, `VIDEO_NOT_FOUND` |
| `RateLimitError` | 429 | `TOO_MANY_PENDING`, `RATE_LIMITED` |
| `ServerError` / `ServiceUnavailableError` | 5xx / 503 | `INTERNAL_ERROR` / `UNAVAILABLE` |
| `APIError` | any other HTTP error (base class of the above) | as sent |
| `APIConnectionError` / `APITimeoutError` | no HTTP response | `CONNECTION_ERROR` / `TIMEOUT` |
| `JobFailedError` | the job ended in `FAILED` / `failed` | `.error_code`, e.g. `AGE_RESTRICTED` |
| `WaitTimeoutError` | `wait_for_*(timeout=...)` ran out of time | `WAIT_TIMEOUT` |
| `MissingAPIKeyError` | no key given and `VIDKRAKEN_API_KEY` is unset | `MISSING_API_KEY` |

If the API sends a code this SDK doesn't know, the exception class is picked from
the HTTP status.

```python
from vidkraken import JobFailedError, RateLimitError, VidKrakenError

try:
    done = vk.download_and_wait(url, format="1080")
except JobFailedError as e:
    print(e.job_id, e.error_code, e.message)
    if e.retryable:          # only NETWORK_ERROR is worth resubmitting
        ...
except RateLimitError as e:
    print("retry in", e.retry_after, "seconds")
except VidKrakenError as e:
    print(e.status, e.code, e.message)
```

## Retries

The API has **no idempotency key**. Retrying a `POST /download` after a timeout
or a 5xx could therefore start, and bill, a second download. The client only
retries when that can't happen:

| Request | Retried on |
|---|---|
| Any `GET` (status polls, `/me`, file download) | network errors, timeouts, 429, 500, 502, 503, 504 |
| `POST /download`, `/info`, `/list-channel` | 429 and 503 `UNAVAILABLE` only, because in both cases nothing was queued |

It never retries a POST on 500, 502, 504, a timeout or a network error. In those
cases you get the error and can check before resubmitting.

- Retries use exponential backoff with jitter (0.5s, 1s, 2s and so on, capped at 8s).
- A `Retry-After` header is honored if it is 60s or less. If it is longer, the
  error is raised so you can decide what to do.
- `max_retries` defaults to 2. Set it to 0 to turn retries off:

```python
vk = VidKraken(max_retries=5, timeout=30.0)
vk = VidKraken(max_retries=0)
```

## Rate limits

Each account can have at most 100 downloads queued or in progress at once.
Every `download()` result carries the headroom parsed from the IETF `RateLimit`
headers:

```python
job = vk.download(url)
job.rate_limit.limit      # 100: how many may be pending at once
job.rate_limit.remaining  # how many more you can queue right now
```

Past the cap, `download()` raises `RateLimitError` with code `TOO_MANY_PENDING`
and `.retry_after` set. The error also has `.rate_limit`.

## Bring your own httpx client

```python
import httpx
vk = VidKraken(http_client=httpx.Client(proxy="http://proxy:8080"))  # proxy= needs httpx>=0.26
```

The client must be an `httpx.Client` for `VidKraken` or an `httpx.AsyncClient`
for `AsyncVidKraken`. A client you pass in is not closed by `close()`.

## CLI

```bash
vidkraken download https://youtu.be/dQw4w9WgXcQ -f 1080            # waits, saves, prints the path
vidkraken download https://youtu.be/dQw4w9WgXcQ -f audio-mp3 -o music/
vidkraken download https://youtu.be/dQw4w9WgXcQ --start 30 --end 90 -o clip.mp4 -f 720
vidkraken download https://youtu.be/dQw4w9WgXcQ --no-wait          # prints the job id
vidkraken download https://youtu.be/dQw4w9WgXcQ --webhook https://example.com/hook --no-wait
vidkraken status <job_id>
vidkraken info https://youtu.be/dQw4w9WgXcQ --audio-tracks
vidkraken me
vidkraken --version
python -m vidkraken --help
```

| Option | Applies to | Effect |
|---|---|---|
| `-f/--format` | `download` | one of `1080`, `720`, `480`, `360`, `audio` (default), `audio-aac`, `audio-mp3` |
| `--start S` / `--end S` | `download` | trim the clip, in seconds |
| `--language LANG` | `download` | preferred audio track |
| `-o/--output PATH` | `download` | output file or directory |
| `--no-wait` | `download` | print the job id and exit |
| `--webhook URL` | `download` | URL to POST the final status to |
| `--timeout S`, `--poll-interval S` | `download` | how long to wait and how often to poll |
| `-q/--quiet` | `download` | no progress output |
| `--json` | every command | print one JSON document on stdout |
| `--api-key KEY` | every command | overrides `VIDKRAKEN_API_KEY` |

The CLI is designed to be scripted and driven by agents:

- **stdout** carries only the result: a path, a job id, a plain report, or one
  JSON document with `--json`. On failure with `--json`, stdout gets
  `{"error", "code", ...}`.
- **stderr** carries progress and errors such as
  `vidkraken: error: TOO_MANY_PENDING: ...`.
- It never prompts. Color appears only when stderr is a TTY and `NO_COLOR` is unset.
- **Exit codes:**
  - `0`: success
  - `1`: API or job error. `status` also exits 1 for a `FAILED` job.
  - `2`: usage error, including a missing API key
  - `130`: interrupted

## Links

- Documentation: https://vidkraken.com/docs
- LLM-friendly reference: https://vidkraken.com/llms.txt
- OpenAPI spec: https://vidkraken.com/openapi.yaml
- Support: hello@vidkraken.com

## License

MIT © Vid Kraken

"""``vidkraken`` command-line interface.

Output contract (stable, agent-friendly):

- stdout carries only the result: a file path / job id / plain-text report, or
  a single JSON document with ``--json``.
- stderr carries progress and errors. No prompts. Color only when stderr is a
  TTY and ``NO_COLOR`` is unset.
- Exit codes: 0 success, 1 API or job error, 2 usage error (bad arguments,
  missing API key), 130 interrupted.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Callable, Dict, List, Optional, Sequence, TextIO, Tuple

from ._client import VidKraken
from ._exceptions import JobFailedError, MissingAPIKeyError, VidKrakenError
from ._models import DEFAULT_FORMAT, FORMATS, DownloadStatus, RateLimit
from ._version import __version__

Row = Tuple[str, Any]

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_INTERRUPTED = 130

_EPILOG = """\
environment:
  VIDKRAKEN_API_KEY    API key (get one at https://vidkraken.com/login)
  VIDKRAKEN_BASE_URL   API root (default https://vidkraken.com/api/v2)
  NO_COLOR             disable colored stderr output

exit codes:
  0 success, 1 API or job error, 2 usage error, 130 interrupted

docs: https://vidkraken.com/docs  -  llms.txt: https://vidkraken.com/llms.txt
Use only with content you have the right to download.
"""


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #


def _non_negative_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected whole seconds, got {value!r}") from None
    if number < 0:
        raise argparse.ArgumentTypeError(f"must be >= 0, got {number}")
    return number


def _positive_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a number, got {value!r}") from None
    if number <= 0:
        raise argparse.ArgumentTypeError(f"must be > 0, got {value}")
    return number


def _add_common(parser: argparse.ArgumentParser, suppress: bool) -> None:
    default = argparse.SUPPRESS if suppress else None
    group = parser.add_argument_group("connection")
    group.add_argument("--api-key", metavar="KEY", default=default, help="API key (overrides VIDKRAKEN_API_KEY)")
    group.add_argument("--base-url", metavar="URL", default=default, help=argparse.SUPPRESS)


def _add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="print one JSON document on stdout")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vidkraken",
        description="Vid Kraken CLI: download YouTube videos and audio via the Vid Kraken API.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"vidkraken {__version__}")
    _add_common(parser, suppress=False)
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    dl = sub.add_parser(
        "download",
        help="download a video or its audio and save it to disk",
        description=(
            "Submit a download, wait for it (progress on stderr), save the file and print its path. "
            "With --no-wait, print the job id instead."
        ),
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    dl.add_argument("url", help="YouTube URL (watch, youtu.be or live link)")
    dl.add_argument(
        "-f",
        "--format",
        choices=FORMATS,
        default=DEFAULT_FORMAT,
        metavar="FORMAT",
        help=f"one of {', '.join(FORMATS)} (default: {DEFAULT_FORMAT})",
    )
    dl.add_argument("--start", type=_non_negative_int, metavar="S", help="trim start, in seconds")
    dl.add_argument("--end", type=_non_negative_int, metavar="S", help="trim end, in seconds")
    dl.add_argument("--language", metavar="LANG", help="preferred audio language, BCP-47 (e.g. en, en-US)")
    dl.add_argument(
        "-o",
        "--output",
        metavar="PATH",
        help="output file, or directory to save into (default: current directory, named after the title)",
    )
    dl.add_argument("--no-wait", action="store_true", help="submit only and print the job id")
    dl.add_argument("--webhook", metavar="URL", help="URL to POST the final job status to")
    dl.add_argument(
        "--poll-interval", type=_positive_float, default=3.0, metavar="S", help="seconds between polls (default: 3)"
    )
    dl.add_argument("--timeout", type=_positive_float, metavar="S", help="give up waiting after S seconds")
    dl.add_argument("-q", "--quiet", action="store_true", help="no progress output on stderr")
    _add_json(dl)
    _add_common(dl, suppress=True)

    st = sub.add_parser("status", help="show the status of a download job")
    st.add_argument("job_id", help="job id returned by `download --no-wait`")
    _add_json(st)
    _add_common(st, suppress=True)

    info = sub.add_parser("info", help="show a video's metadata (title, duration, audio tracks)")
    info.add_argument("url", help="YouTube URL")
    info.add_argument("--audio-tracks", action="store_true", help="also list audio tracks (slower)")
    info.add_argument("--timeout", type=_positive_float, metavar="S", help="give up waiting after S seconds")
    _add_json(info)
    _add_common(info, suppress=True)

    me = sub.add_parser("me", help="show your account, plan and remaining balances")
    _add_json(me)
    _add_common(me, suppress=True)

    return parser


# --------------------------------------------------------------------------- #
# Output helpers
# --------------------------------------------------------------------------- #


def _use_color(stream: TextIO) -> bool:
    if "NO_COLOR" in os.environ or os.environ.get("TERM") == "dumb":
        return False
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


def _json_error(data: Dict[str, Any]) -> Dict[str, Any]:
    """``--json`` error document: ``{"error": {"code", "message", ...}}``, the
    same shape as the JavaScript CLI. Empty fields are left out."""
    fields = {k: v for k, v in data.items() if k != "error" and v is not None}
    return {"error": {"code": fields.pop("code", None), "message": data.get("error"), **fields}}


def _print_json(data: Any, out: TextIO) -> None:
    out.write(json.dumps(data, ensure_ascii=False, sort_keys=False) + "\n")
    out.flush()


def _fmt_bytes(size: Optional[float]) -> str:
    if size is None:
        return "?"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1000 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1000
    return f"{value:.1f} GB"  # pragma: no cover


def _fmt_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "?"
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _rate_limit_dict(rate_limit: Optional[RateLimit]) -> Optional[Dict[str, Any]]:
    if rate_limit is None:
        return None
    return {
        "policy": rate_limit.policy,
        "limit": rate_limit.limit,
        "remaining": rate_limit.remaining,
    }


class _Progress:
    """Renders job progress on stderr: one rewritten line on a TTY, otherwise
    one line per change."""

    def __init__(self, stream: TextIO, enabled: bool) -> None:
        self.stream = stream
        self.enabled = enabled
        self.tty = bool(getattr(stream, "isatty", None) and stream.isatty())
        self._last = ""
        self._width = 0

    def _emit(self, line: str) -> None:
        if not self.enabled or line == self._last:
            return
        self._last = line
        if self.tty:
            pad = max(0, self._width - len(line))
            self.stream.write("\r" + line + " " * pad)
            self._width = len(line)
        else:
            self.stream.write(line + "\n")
        self.stream.flush()

    def message(self, line: str) -> None:
        if not self.enabled:
            return
        self.finish()
        self.stream.write(line + "\n")
        self.stream.flush()

    def status(self, status: DownloadStatus) -> None:
        if status.is_pending:
            p = status.progress
            if p is None:
                line = "queued" if status.status == "IN_QUEUE" else "in progress"
            else:
                line = f"{p.stage or status.status.lower()} {p.percent or 0}%"
                if p.queue_position:
                    line += f" (queue position {p.queue_position})"
                if self.tty and p.total_bytes:
                    line += f" {_fmt_bytes(p.bytes_downloaded)}/{_fmt_bytes(p.total_bytes)}"
                if self.tty and p.eta_seconds is not None:
                    line += f" eta {_fmt_duration(p.eta_seconds)}"
            self._emit(line)
        elif status.is_completed:
            self._emit(f"completed 100% ({_fmt_bytes(status.file_size)})")
            self.finish()

    def bytes(self, written: int, total: Optional[int]) -> None:
        if self.tty:
            pct = f" {written * 100 // total}%" if total else ""
            self._emit(f"saving{pct} {_fmt_bytes(written)}/{_fmt_bytes(total)}")

    def finish(self) -> None:
        if self.tty and self._width:
            self.stream.write("\n")
            self.stream.flush()
        self._width = 0
        self._last = ""


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def _make_client(args: argparse.Namespace) -> VidKraken:
    return VidKraken(api_key=getattr(args, "api_key", None), base_url=getattr(args, "base_url", None))


def _cmd_download(args: argparse.Namespace, client: VidKraken, out: TextIO, err: TextIO) -> int:
    progress = _Progress(err, enabled=not args.quiet)
    job = client.download(
        args.url,
        args.format,
        start_time=args.start,
        end_time=args.end,
        language=args.language,
        webhook_url=args.webhook,
    )
    if args.no_wait:
        if args.json:
            _print_json({**job.raw, "rateLimit": _rate_limit_dict(job.rate_limit)}, out)
        else:
            out.write(job.job_id + "\n")
        return EXIT_OK

    progress.message(f"job {job.job_id}: {job.title or args.url}")
    try:
        status = client.wait_for_download(
            job.job_id, poll_interval=args.poll_interval, timeout=args.timeout, on_progress=progress.status
        )
    finally:
        progress.finish()
    try:
        path = client.save_file(status, args.output, on_bytes=progress.bytes)
    finally:
        progress.finish()
    if args.json:
        _print_json({**status.raw, "path": str(path)}, out)
    else:
        out.write(str(path) + "\n")
    return EXIT_OK


def _cmd_status(args: argparse.Namespace, client: VidKraken, out: TextIO, err: TextIO) -> int:
    status = client.get_download(args.job_id)
    if args.json:
        _print_json(status.raw, out)
    else:
        rows: List[Row] = [
            ("jobId", status.job_id),
            ("status", status.status),
            ("title", status.title),
            ("duration", _fmt_duration(status.duration) if status.duration is not None else None),
            ("url", status.url),
            ("format", status.format),
            ("actualFormat", status.actual_format),
            ("language", status.language),
        ]
        if status.progress is not None:
            p = status.progress
            rows.append(("progress", f"{p.stage} {p.percent}%"))
            if p.eta_seconds is not None:
                rows.append(("eta", _fmt_duration(p.eta_seconds)))
            if p.queue_position:
                rows.append(("queuePosition", p.queue_position))
        rows += [
            ("fileSize", _fmt_bytes(status.file_size) if status.file_size is not None else None),
            ("downloadUrl", status.download_url),
            ("errorCode", status.error_code),
        ]
        _write_rows(rows, out)
    if status.is_failed:
        _report_error(JobFailedError(status.job_id, status.error_code, status.error_message, job=status), err)
        return EXIT_ERROR
    return EXIT_OK


def _cmd_info(args: argparse.Namespace, client: VidKraken, out: TextIO, err: TextIO) -> int:
    info = client.info_and_wait(args.url, include_audio_tracks=args.audio_tracks, timeout=args.timeout)
    if args.json:
        _print_json(info.raw, out)
        return EXIT_OK
    rows: List[Row] = [
        ("title", info.title),
        ("duration", _fmt_duration(info.duration) if info.duration is not None else None),
        ("url", info.url),
    ]
    if info.region_restriction is not None:
        if info.region_restriction.allowed:
            rows.append(("allowedIn", ",".join(info.region_restriction.allowed)))
        if info.region_restriction.blocked:
            rows.append(("blockedIn", ",".join(info.region_restriction.blocked)))
    if info.audio is not None:
        tracks = [f"{t.language or 'unknown'} ({t.type})" for t in info.audio]
        rows.append(("audioTracks", ", ".join(tracks) if tracks else "none"))
    _write_rows(rows, out)
    return EXIT_OK


def _cmd_me(args: argparse.Namespace, client: VidKraken, out: TextIO, err: TextIO) -> int:
    account = client.me()
    if args.json:
        _print_json(account.raw, out)
        return EXIT_OK
    _write_rows([("name", account.name), ("email", account.email), ("plan", account.plan.id)], out)
    for feature in sorted(account.balances):
        bal = account.balances[feature]
        if bal.unlimited:
            text = "unlimited"
        else:
            text = f"{_num(bal.remaining)} of {_num(bal.granted)} remaining ({_num(bal.used)} used)"
        out.write(f"  {feature}: {text}\n")
    return EXIT_OK


def _num(value: Optional[float]) -> str:
    if value is None:
        return "?"
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _write_rows(rows: Sequence[Row], out: TextIO) -> None:
    present = [(k, v) for k, v in rows if v is not None and v != ""]
    width = max((len(k) for k, _ in present), default=0)
    for key, value in present:
        out.write(f"{key + ':':<{width + 1}} {value}\n")


def _report_error(exc: VidKrakenError, err: TextIO) -> None:
    prefix = "vidkraken: error:"
    if _use_color(err):
        prefix = f"\033[31m{prefix}\033[0m"
    code = exc.error_code if isinstance(exc, JobFailedError) else exc.code
    line = f"{prefix} {code}: {exc.message}" if code else f"{prefix} {exc.message}"
    if isinstance(exc, JobFailedError):
        line += f" (job {exc.job_id}{', retryable' if exc.retryable else ''})"
    err.write(line + "\n")
    if exc.details:
        err.write(f"  details: {exc.details}\n")
    if exc.retry_after is not None:
        err.write(f"  retry after: {exc.retry_after:g}s\n")
    err.flush()


_COMMANDS: Dict[str, Callable[[argparse.Namespace, VidKraken, TextIO, TextIO], int]] = {
    "download": _cmd_download,
    "status": _cmd_status,
    "info": _cmd_info,
    "me": _cmd_me,
}


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point for the ``vidkraken`` console script. Returns the exit code."""
    out, err = sys.stdout, sys.stderr
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help(err)
        return EXIT_USAGE
    if args.command == "download" and args.start is not None and args.end is not None and args.end <= args.start:
        parser.error("--end must be greater than --start")

    want_json = bool(getattr(args, "json", False))
    try:
        client = _make_client(args)
    except MissingAPIKeyError as exc:
        _report_error(exc, err)
        if want_json:
            _print_json(_json_error(exc.to_dict()), out)
        return EXIT_USAGE

    try:
        with client:
            return _COMMANDS[args.command](args, client, out, err)
    except VidKrakenError as exc:
        _report_error(exc, err)
        if want_json:
            _print_json(_json_error(exc.to_dict()), out)
        return EXIT_ERROR
    except OSError as exc:  # e.g. cannot write the output file
        err.write(f"vidkraken: error: {exc}\n")
        if want_json:
            _print_json(_json_error({"error": str(exc), "code": "IO_ERROR"}), out)
        return EXIT_ERROR
    except KeyboardInterrupt:
        err.write("\nvidkraken: interrupted (any submitted job keeps running server-side)\n")
        return EXIT_INTERRUPTED


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

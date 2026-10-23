from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, List

import httpx
import pytest

import vidkraken
from vidkraken import cli

from conftest import (
    ACCOUNT,
    API_KEY,
    CDN_URL,
    COMPLETED,
    FAILED,
    INFO_ID,
    INFO_SUCCESS,
    JOB_ID,
    PENDING,
    RATE_HEADERS,
    SUBMITTED,
    FakeAPI,
    api_error,
    json_response,
)


@pytest.fixture
def wired(api: FakeAPI, monkeypatch: pytest.MonkeyPatch) -> FakeAPI:
    """Route the CLI's client through the fake API."""
    made: List[Any] = []

    def make_client(args: Any) -> vidkraken.VidKraken:
        client = api.client(api_key=getattr(args, "api_key", None) or API_KEY)
        made.append(args)
        return client

    monkeypatch.setattr(cli, "_make_client", make_client)
    return api


def run_cli(capsys: pytest.CaptureFixture[str], *argv: str) -> "tuple[int, str, str]":
    try:
        code = cli.main(list(argv))
    except SystemExit as exc:  # argparse usage errors / --help / --version
        code = int(exc.code or 0)
    out, err = capsys.readouterr()
    return code, out, err


# --------------------------------------------------------------------------- #
# Parsing and usage errors
# --------------------------------------------------------------------------- #


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run_cli(capsys, "--version")
    assert code == 0
    assert out.strip() == f"vidkraken {vidkraken.__version__}"


def test_help(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run_cli(capsys, "--help")
    assert code == 0
    for command in ("download", "status", "info", "me"):
        assert command in out


def test_no_command_is_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run_cli(capsys)
    assert code == 2
    assert out == ""
    assert "usage:" in err


@pytest.mark.parametrize(
    "argv",
    [
        ["download", "https://youtu.be/x", "-f", "4k"],
        ["download", "https://youtu.be/x", "--start", "-5"],
        ["download", "https://youtu.be/x", "--start", "10", "--end", "5"],
        ["download"],
        ["status"],
        ["bogus"],
    ],
)
def test_usage_errors_exit_2(capsys: pytest.CaptureFixture[str], wired: FakeAPI, argv: List[str]) -> None:
    code, out, _ = run_cli(capsys, *argv)
    assert code == 2
    assert out == ""
    assert wired.requests == []


def test_missing_api_key_is_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run_cli(capsys, "me", "--json")
    assert code == 2
    assert "MISSING_API_KEY" in err
    assert json.loads(out)["error"]["code"] == "MISSING_API_KEY"


@pytest.mark.parametrize("argv", [["--api-key", "vk_flag", "me"], ["me", "--api-key", "vk_flag"]])
def test_api_key_flag_overrides_env(monkeypatch: pytest.MonkeyPatch, argv: List[str]) -> None:
    monkeypatch.setenv("VIDKRAKEN_API_KEY", "vk_env")
    args = cli.build_parser().parse_args(argv)
    with cli._make_client(args) as client:
        assert client.api_key == "vk_flag"
    with cli._make_client(cli.build_parser().parse_args(["me"])) as client:
        assert client.api_key == "vk_env"


def test_parses_download_options() -> None:
    args = cli.build_parser().parse_args(
        ["download", "https://youtu.be/x", "-f", "1080", "--start", "30", "--end", "90", "--language", "en", "-o", "out/", "--no-wait", "--webhook", "https://h.example/x", "--json"]
    )
    assert (args.format, args.start, args.end, args.language, args.output, args.no_wait, args.webhook, args.json) == (
        "1080", 30, 90, "en", "out/", True, "https://h.example/x", True
    )


# --------------------------------------------------------------------------- #
# download
# --------------------------------------------------------------------------- #


def test_download_no_wait_prints_job_id(capsys: pytest.CaptureFixture[str], wired: FakeAPI) -> None:
    wired.add("POST", "/download", json_response(200, SUBMITTED, RATE_HEADERS))
    code, out, err = run_cli(capsys, "download", "https://youtu.be/x", "--no-wait", "-f", "720")
    assert code == 0
    assert out == f"{JOB_ID}\n"
    assert json.loads(wired.requests[0].content)["format"] == "720"


def test_download_no_wait_json(capsys: pytest.CaptureFixture[str], wired: FakeAPI) -> None:
    wired.add("POST", "/download", json_response(200, SUBMITTED, RATE_HEADERS))
    code, out, _ = run_cli(capsys, "download", "https://youtu.be/x", "--no-wait", "--json", "--webhook", "https://h.example/x")
    assert code == 0
    data = json.loads(out)
    assert data["jobId"] == JOB_ID
    assert data["rateLimit"] == {"policy": "pending", "limit": 100, "remaining": 37}
    assert json.loads(wired.requests[0].content)["webhookUrl"] == "https://h.example/x"


def test_download_waits_saves_and_prints_path(capsys: pytest.CaptureFixture[str], wired: FakeAPI, tmp_path: Path) -> None:
    wired.add("POST", "/download", json_response(200, SUBMITTED))
    wired.add("GET", f"/download/{JOB_ID}", json_response(200, PENDING), json_response(200, COMPLETED))
    wired.add("GET", CDN_URL, httpx.Response(200, content=b"hello world"))
    code, out, err = run_cli(capsys, "download", "https://youtu.be/x", "-f", "1080", "-o", str(tmp_path))
    assert code == 0
    expected = tmp_path / "Rick Astley - Never Gonna Give You Up.mp4"
    assert out == f"{expected}\n"
    assert expected.read_bytes() == b"hello world"
    assert "downloading 41%" in err  # progress goes to stderr
    assert "completed" in err


def test_download_json_output_is_only_json(capsys: pytest.CaptureFixture[str], wired: FakeAPI, tmp_path: Path) -> None:
    wired.add("POST", "/download", json_response(200, SUBMITTED))
    wired.add("GET", f"/download/{JOB_ID}", json_response(200, COMPLETED))
    wired.add("GET", CDN_URL, httpx.Response(200, content=b"x"))
    target = tmp_path / "clip.mp4"
    code, out, _ = run_cli(capsys, "download", "https://youtu.be/x", "-o", str(target), "--json", "-q")
    assert code == 0
    data = json.loads(out)  # the whole of stdout is one JSON document
    assert data["status"] == "COMPLETED"
    assert data["path"] == str(target)
    assert data["downloadUrl"] == CDN_URL


def test_download_job_failed_exit_1(capsys: pytest.CaptureFixture[str], wired: FakeAPI) -> None:
    wired.add("POST", "/download", json_response(200, SUBMITTED))
    wired.add("GET", f"/download/{JOB_ID}", json_response(200, FAILED))
    code, out, err = run_cli(capsys, "download", "https://youtu.be/x", "--json")
    assert code == 1
    assert "AGE_RESTRICTED" in err
    data = json.loads(out)["error"]
    assert data["code"] == "AGE_RESTRICTED"
    assert data["jobId"] == JOB_ID
    assert data["retryable"] is False


def test_download_api_error_exit_1(capsys: pytest.CaptureFixture[str], wired: FakeAPI) -> None:
    wired.add("POST", "/download", api_error(403, "LIMIT_REACHED", "Credit limit reached"))
    code, out, err = run_cli(capsys, "download", "https://youtu.be/x")
    assert code == 1
    assert out == ""
    assert "LIMIT_REACHED: Credit limit reached" in err


def test_download_429_reports_retry_after(capsys: pytest.CaptureFixture[str], wired: FakeAPI) -> None:
    wired.add("POST", "/download", api_error(429, "TOO_MANY_PENDING", "Too many", headers={"Retry-After": "300"}))
    code, out, err = run_cli(capsys, "download", "https://youtu.be/x", "--json")
    assert code == 1
    assert "retry after: 300s" in err
    assert json.loads(out)["error"]["retryAfter"] == 300


# --------------------------------------------------------------------------- #
# status / info / me
# --------------------------------------------------------------------------- #


def test_status_json(capsys: pytest.CaptureFixture[str], wired: FakeAPI) -> None:
    wired.add("GET", f"/download/{JOB_ID}", json_response(200, PENDING))
    code, out, _ = run_cli(capsys, "status", JOB_ID, "--json")
    assert code == 0
    assert json.loads(out) == PENDING


def test_status_human(capsys: pytest.CaptureFixture[str], wired: FakeAPI) -> None:
    wired.add("GET", f"/download/{JOB_ID}", json_response(200, COMPLETED))
    code, out, _ = run_cli(capsys, "status", JOB_ID)
    assert code == 0
    assert "status:" in out and "COMPLETED" in out
    assert CDN_URL in out


def test_status_failed_exit_1(capsys: pytest.CaptureFixture[str], wired: FakeAPI) -> None:
    wired.add("GET", f"/download/{JOB_ID}", json_response(200, FAILED))
    code, out, err = run_cli(capsys, "status", JOB_ID, "--json")
    assert code == 1
    assert json.loads(out)["status"] == "FAILED"
    assert "AGE_RESTRICTED" in err


def test_status_not_found(capsys: pytest.CaptureFixture[str], wired: FakeAPI) -> None:
    wired.add("GET", "/download/nope", api_error(404, "NOT_FOUND", "Download job not found"))
    code, _, err = run_cli(capsys, "status", "nope")
    assert code == 1
    assert "NOT_FOUND: Download job not found" in err


def test_info_json(capsys: pytest.CaptureFixture[str], wired: FakeAPI) -> None:
    wired.add("POST", "/info", json_response(200, {"jobId": INFO_ID, "status": "pending"}))
    wired.add("GET", f"/info/{INFO_ID}", json_response(200, INFO_SUCCESS))
    code, out, _ = run_cli(capsys, "info", "https://youtu.be/x", "--audio-tracks", "--json")
    assert code == 0
    assert json.loads(out) == INFO_SUCCESS["result"]
    assert json.loads(wired.requests[0].content)["includeAudioTracks"] is True


def test_info_human(capsys: pytest.CaptureFixture[str], wired: FakeAPI) -> None:
    wired.add("POST", "/info", json_response(200, {"jobId": INFO_ID, "status": "pending"}))
    wired.add("GET", f"/info/{INFO_ID}", json_response(200, INFO_SUCCESS))
    code, out, _ = run_cli(capsys, "info", "https://youtu.be/x", "--audio-tracks")
    assert code == 0
    assert "duration:    3:33" in out
    assert "en (original), es (dubbed)" in out


def test_me_human_and_json(capsys: pytest.CaptureFixture[str], wired: FakeAPI) -> None:
    wired.add("GET", "/me", json_response(200, ACCOUNT))
    code, out, _ = run_cli(capsys, "me")
    assert code == 0
    assert "plan:  starter" in out
    assert "bandwidth_mb: 347520 of 400000 remaining (52480 used)" in out
    code, out, _ = run_cli(capsys, "me", "--json")
    assert code == 0 and json.loads(out) == ACCOUNT


def test_unauthorized_exit_1(capsys: pytest.CaptureFixture[str], wired: FakeAPI) -> None:
    wired.add("GET", "/me", api_error(401, "UNAUTHORIZED", "Invalid API key"))
    code, out, err = run_cli(capsys, "me", "--json")
    assert code == 1
    assert json.loads(out)["error"]["code"] == "UNAUTHORIZED"
    assert "UNAUTHORIZED: Invalid API key" in err
    assert "\033[" not in err  # captured stderr is not a TTY: no color


# --------------------------------------------------------------------------- #
# Entry points
# --------------------------------------------------------------------------- #


def test_python_dash_m(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "vidkraken", "--version"], capture_output=True, text=True, cwd=tmp_path, check=False
    )
    assert result.returncode == 0
    assert result.stdout.strip() == f"vidkraken {vidkraken.__version__}"

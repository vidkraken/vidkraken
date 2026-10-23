from __future__ import annotations

import json
from pathlib import Path
from typing import List

import httpx
import pytest

import vidkraken
from vidkraken import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    DownloadStatus,
    JobFailedError,
    MissingAPIKeyError,
    NotFoundError,
    PermissionDeniedError,
    RateLimit,
    RateLimitError,
    ServerError,
    ServiceUnavailableError,
    VidKraken,
    WaitTimeoutError,
)
from vidkraken._base import MAX_RETRY_AFTER

from conftest import (
    ACCOUNT,
    API_KEY,
    BASE,
    CDN_URL,
    COMPLETED,
    FAILED,
    INFO_ID,
    INFO_SUCCESS,
    JOB_ID,
    PENDING,
    QUEUED,
    RATE_HEADERS,
    SUBMITTED,
    FakeAPI,
    api_error,
    json_response,
)

# --------------------------------------------------------------------------- #
# Construction, auth, request shape
# --------------------------------------------------------------------------- #


def test_missing_api_key_raises_clear_error() -> None:
    with pytest.raises(MissingAPIKeyError) as exc:
        VidKraken()
    assert "VIDKRAKEN_API_KEY" in str(exc.value)
    assert exc.value.code == "MISSING_API_KEY"


def test_api_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIDKRAKEN_API_KEY", "vk_from_env")
    with VidKraken() as vk:
        assert vk.api_key == "vk_from_env"
        assert vk.base_url == BASE


def test_negative_max_retries_rejected() -> None:
    with pytest.raises(ValueError):
        VidKraken(api_key="k", max_retries=-1)


def test_download_sends_auth_ua_and_camelcase_body(api: FakeAPI) -> None:
    api.add("POST", "/download", json_response(200, SUBMITTED, RATE_HEADERS))
    with api.client() as vk:
        job = vk.download(
            "https://youtu.be/dQw4w9WgXcQ",
            format="1080",
            start_time=30,
            end_time=90,
            language="en-US",
            webhook_url="https://example.com/hook",
        )
    req = api.requests[0]
    assert req.headers["Authorization"] == f"Bearer {API_KEY}"
    assert req.headers["User-Agent"] == f"vidkraken-python/{vidkraken.__version__}"
    assert json.loads(req.content) == {
        "url": "https://youtu.be/dQw4w9WgXcQ",
        "format": "1080",
        "startTime": 30,
        "endTime": 90,
        "language": "en-US",
        "webhookUrl": "https://example.com/hook",
    }
    assert job.job_id == JOB_ID
    assert job.status == "IN_QUEUE"
    assert job.duration == 213
    assert job["jobId"] == JOB_ID  # raw access
    assert job.raw == SUBMITTED


def test_download_defaults_to_audio_and_omits_unset_fields(api: FakeAPI) -> None:
    api.add("POST", "/download", json_response(200, SUBMITTED))
    with api.client() as vk:
        job = vk.download("https://youtu.be/x")
    assert json.loads(api.requests[0].content) == {"url": "https://youtu.be/x", "format": "audio"}
    assert job.rate_limit is None


def test_custom_base_url(api: FakeAPI) -> None:
    api.add("GET", "https://example.test/api/v2/me", json_response(200, ACCOUNT))
    with api.client(base_url="https://example.test/api/v2/") as vk:
        assert vk.me().name == "Jane Developer"


# --------------------------------------------------------------------------- #
# Rate limit headers
# --------------------------------------------------------------------------- #


def test_rate_limit_parsed_on_download(api: FakeAPI) -> None:
    api.add("POST", "/download", json_response(200, SUBMITTED, RATE_HEADERS))
    with api.client() as vk:
        job = vk.download("https://youtu.be/x")
    assert job.rate_limit == RateLimit(limit=100, remaining=37, policy="pending", quota_unit="concurrent-requests")


@pytest.mark.parametrize(
    "headers, expected",
    [
        ({}, None),
        ({"RateLimit": '"pending";r=0'}, RateLimit(limit=None, remaining=0, policy="pending")),
        ({"ratelimit-policy": '"pending";q=100', "ratelimit": '"pending";r=99'}, RateLimit(100, 99, "pending")),
        (
            {"RateLimit-Policy": '"burst";q=10, "pending";q=100', "RateLimit": '"burst";r=3, "pending";r=42'},
            RateLimit(100, 42, "pending"),
        ),
        ({"RateLimit-Policy": "garbage", "RateLimit": "junk"}, RateLimit(None, None, "garbage")),
    ],
)
def test_rate_limit_header_parsing(headers: dict, expected: object) -> None:
    assert RateLimit.from_headers(headers) == expected


def test_rate_limit_error_carries_retry_after_and_headroom(api: FakeAPI) -> None:
    api.add(
        "POST",
        "/download",
        api_error(429, "TOO_MANY_PENDING", "Too many pending downloads (100).", headers={"Retry-After": "120", **RATE_HEADERS, "RateLimit": '"pending";r=0'}),
    )
    with api.client() as vk, pytest.raises(RateLimitError) as exc:
        vk.download("https://youtu.be/x")
    err = exc.value
    assert err.status == 429
    assert err.code == "TOO_MANY_PENDING"
    assert err.retry_after == 120
    assert err.rate_limit is not None and err.rate_limit.remaining == 0
    assert len(api.requests) == 1  # Retry-After > cap: raised, not slept on


# --------------------------------------------------------------------------- #
# Error parsing
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "status, code, cls",
    [
        (400, "INVALID_REQUEST", BadRequestError),
        (401, "UNAUTHORIZED", AuthenticationError),
        (403, "LIMIT_REACHED", PermissionDeniedError),
        (404, "NOT_FOUND", NotFoundError),
        (404, "VIDEO_NOT_FOUND", NotFoundError),
        (429, "RATE_LIMITED", RateLimitError),
        (500, "INTERNAL_ERROR", ServerError),
        (503, "UNAVAILABLE", ServiceUnavailableError),
        (418, "SOMETHING_NEW", APIError),  # unknown code + unusual status
        (502, "BRAND_NEW_CODE", ServerError),  # unknown code: classified by status
    ],
)
def test_error_classes(api: FakeAPI, status: int, code: str, cls: type) -> None:
    api.add("GET", "/me", api_error(status, code, "it broke", details="yt-dlp said no"))
    with api.client(max_retries=0) as vk, pytest.raises(cls) as exc:
        vk.me()
    err = exc.value
    assert type(err) is cls
    assert isinstance(err, APIError)
    assert (err.status, err.code, err.message, err.details) == (status, code, "it broke", "yt-dlp said no")
    assert code in str(err) and "it broke" in str(err)


def test_non_json_error_body(api: FakeAPI) -> None:
    api.add("GET", "/me", httpx.Response(502, text="<html>Bad Gateway</html>"))
    with api.client(max_retries=0) as vk, pytest.raises(ServerError) as exc:
        vk.me()
    assert exc.value.status == 502
    assert exc.value.code == "INTERNAL_ERROR"
    assert "Bad Gateway" in exc.value.message


def test_error_without_code_gets_default_code(api: FakeAPI) -> None:
    api.add("GET", "/me", json_response(401, {"error": "Missing API key"}))
    with api.client() as vk, pytest.raises(AuthenticationError) as exc:
        vk.me()
    assert exc.value.code == "UNAUTHORIZED"


# --------------------------------------------------------------------------- #
# Retry policy
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_get_retried_on_transient_status(api: FakeAPI, sleeps: List[float], status: int) -> None:
    api.add("GET", f"/download/{JOB_ID}", api_error(status, "X"), json_response(200, COMPLETED))
    with api.client() as vk:
        assert vk.get_download(JOB_ID).is_completed
    assert len(api.requests) == 2
    assert len(sleeps) == 1 and 0 < sleeps[0] <= 1


def test_get_retried_on_network_error(api: FakeAPI, sleeps: List[float]) -> None:
    api.add("GET", "/me", httpx.ConnectError("reset"), httpx.ReadTimeout("slow"), json_response(200, ACCOUNT))
    with api.client() as vk:
        assert vk.me().email == "jane@example.com"
    assert len(api.requests) == 3
    assert len(sleeps) == 2
    assert sleeps[1] > sleeps[0] * 0.75  # exponential backoff (with jitter)


def test_get_gives_up_after_max_retries(api: FakeAPI, sleeps: List[float]) -> None:
    api.add("GET", "/me", api_error(500, "INTERNAL_ERROR"))
    with api.client(max_retries=3) as vk, pytest.raises(ServerError):
        vk.me()
    assert len(api.requests) == 4
    assert len(sleeps) == 3


def test_max_retries_zero_disables_retries(api: FakeAPI, sleeps: List[float]) -> None:
    api.add("GET", "/me", httpx.ConnectError("down"))
    with api.client(max_retries=0) as vk, pytest.raises(APIConnectionError):
        vk.me()
    assert len(api.requests) == 1
    assert sleeps == []


def test_get_not_retried_on_client_errors(api: FakeAPI) -> None:
    api.add("GET", f"/download/{JOB_ID}", api_error(404, "NOT_FOUND"))
    with api.client() as vk, pytest.raises(NotFoundError):
        vk.get_download(JOB_ID)
    assert len(api.requests) == 1


@pytest.mark.parametrize("status", [500, 502, 504])
def test_post_download_not_retried_on_5xx(api: FakeAPI, sleeps: List[float], status: int) -> None:
    api.add("POST", "/download", api_error(status, "INTERNAL_ERROR"), json_response(200, SUBMITTED))
    with api.client() as vk, pytest.raises(ServerError):
        vk.download("https://youtu.be/x")
    assert len(api.requests) == 1
    assert sleeps == []


@pytest.mark.parametrize("exc", [httpx.ConnectError("reset"), httpx.ReadTimeout("slow")])
def test_post_download_not_retried_on_network_errors(api: FakeAPI, exc: Exception) -> None:
    api.add("POST", "/download", exc, json_response(200, SUBMITTED))
    with api.client() as vk, pytest.raises(APIConnectionError) as raised:
        vk.download("https://youtu.be/x")
    assert len(api.requests) == 1
    assert "not retried" in raised.value.message
    if isinstance(exc, httpx.TimeoutException):
        assert isinstance(raised.value, APITimeoutError)


def test_post_info_not_retried_on_500(api: FakeAPI) -> None:
    api.add("POST", "/info", api_error(500, "INTERNAL_ERROR"))
    with api.client() as vk, pytest.raises(ServerError):
        vk.info("https://youtu.be/x")
    assert len(api.requests) == 1


def test_post_retried_on_429_honoring_retry_after(api: FakeAPI, sleeps: List[float]) -> None:
    api.add(
        "POST",
        "/download",
        api_error(429, "TOO_MANY_PENDING", headers={"Retry-After": "7"}),
        json_response(200, SUBMITTED, RATE_HEADERS),
    )
    with api.client() as vk:
        job = vk.download("https://youtu.be/x")
    assert job.job_id == JOB_ID
    assert len(api.requests) == 2
    assert sleeps == [7.0]


def test_retry_after_http_date(api: FakeAPI, sleeps: List[float]) -> None:
    api.add(
        "POST",
        "/download",
        api_error(429, "RATE_LIMITED", headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}),
        json_response(200, SUBMITTED),
    )
    with api.client() as vk:
        vk.download("https://youtu.be/x")
    assert sleeps == [0.0]  # date in the past -> retry immediately


def test_post_429_retry_after_above_cap_is_raised(api: FakeAPI, sleeps: List[float]) -> None:
    api.add("POST", "/download", api_error(429, "TOO_MANY_PENDING", headers={"Retry-After": str(int(MAX_RETRY_AFTER) + 1)}))
    with api.client() as vk, pytest.raises(RateLimitError):
        vk.download("https://youtu.be/x")
    assert sleeps == []


def test_post_retried_on_503_unavailable(api: FakeAPI, sleeps: List[float]) -> None:
    api.add("POST", "/download", api_error(503, "UNAVAILABLE"), api_error(503, "UNAVAILABLE"), json_response(200, SUBMITTED))
    with api.client() as vk:
        assert vk.download("https://youtu.be/x").job_id == JOB_ID
    assert len(api.requests) == 3
    assert len(sleeps) == 2


def test_post_503_gives_up_after_max_retries(api: FakeAPI) -> None:
    api.add("POST", "/download", api_error(503, "UNAVAILABLE"))
    with api.client(max_retries=1) as vk, pytest.raises(ServiceUnavailableError):
        vk.download("https://youtu.be/x")
    assert len(api.requests) == 2


def test_post_not_retried_on_400(api: FakeAPI) -> None:
    api.add("POST", "/download", api_error(400, "INVALID_REQUEST", "Invalid format"))
    with api.client() as vk, pytest.raises(BadRequestError):
        vk.download("https://youtu.be/x", format="999")
    assert len(api.requests) == 1


# --------------------------------------------------------------------------- #
# Waiting
# --------------------------------------------------------------------------- #


def test_wait_for_download_happy_path(api: FakeAPI, sleeps: List[float]) -> None:
    api.add(
        "GET",
        f"/download/{JOB_ID}",
        json_response(200, QUEUED),
        json_response(200, PENDING),
        json_response(200, COMPLETED),
    )
    seen: List[DownloadStatus] = []
    with api.client() as vk:
        done = vk.wait_for_download(JOB_ID, poll_interval=2.5, on_progress=seen.append)
    assert done.is_completed
    assert done.download_url == CDN_URL
    assert done.actual_format == "1080"
    assert done.file_size == 11
    assert [s.status for s in seen] == ["IN_QUEUE", "IN_PROGRESS", "COMPLETED"]
    progress = seen[1].progress
    assert progress is not None
    assert (progress.stage, progress.percent, progress.bytes_downloaded, progress.eta_seconds) == ("downloading", 41, 43500000, 12)
    assert sleeps == [2.5, 2.5]


def test_wait_for_download_failed(api: FakeAPI) -> None:
    api.add("GET", f"/download/{JOB_ID}", json_response(200, PENDING), json_response(200, FAILED))
    with api.client() as vk, pytest.raises(JobFailedError) as exc:
        vk.wait_for_download(JOB_ID)
    err = exc.value
    assert err.job_id == JOB_ID
    assert err.error_code == "AGE_RESTRICTED"
    assert err.retryable is False
    assert "sign-in" in err.message
    assert isinstance(err.job, DownloadStatus) and err.job.title == "Some Restricted Video"


def test_failed_network_error_is_retryable(api: FakeAPI) -> None:
    api.add("GET", f"/download/{JOB_ID}", json_response(200, {**FAILED, "errorCode": "NETWORK_ERROR"}))
    with api.client() as vk, pytest.raises(JobFailedError) as exc:
        vk.wait_for_download(JOB_ID)
    assert exc.value.retryable is True


def test_failed_unknown_or_missing_code_is_retryable(api: FakeAPI) -> None:
    for code in ("QUEUE_UNAVAILABLE", None):
        api.add("GET", f"/download/{JOB_ID}", json_response(200, {**FAILED, "errorCode": code}))
        with api.client() as vk, pytest.raises(JobFailedError) as exc:
            vk.wait_for_download(JOB_ID)
        assert exc.value.retryable is True, code


def test_wait_for_download_timeout(api: FakeAPI) -> None:
    api.add("GET", f"/download/{JOB_ID}", json_response(200, PENDING))
    with api.client() as vk, pytest.raises(WaitTimeoutError) as exc:
        vk.wait_for_download(JOB_ID, timeout=0)
    assert exc.value.job_id == JOB_ID
    assert isinstance(exc.value, TimeoutError)


def test_download_and_wait(api: FakeAPI) -> None:
    api.add("POST", "/download", json_response(200, SUBMITTED))
    api.add("GET", f"/download/{JOB_ID}", json_response(200, PENDING), json_response(200, COMPLETED))
    with api.client() as vk:
        done = vk.download_and_wait("https://youtu.be/x", format="1080", poll_interval=0.1)
    assert done.job_id == JOB_ID and done.is_completed


def test_wait_survives_transient_poll_errors(api: FakeAPI) -> None:
    api.add(
        "GET",
        f"/download/{JOB_ID}",
        json_response(200, PENDING),
        api_error(502, "INTERNAL_ERROR"),
        json_response(200, COMPLETED),
    )
    with api.client() as vk:
        assert vk.wait_for_download(JOB_ID).is_completed


# --------------------------------------------------------------------------- #
# Info, me, channel
# --------------------------------------------------------------------------- #


def test_info_and_wait(api: FakeAPI) -> None:
    api.add("POST", "/info", json_response(200, {"jobId": INFO_ID, "status": "pending"}))
    api.add("GET", f"/info/{INFO_ID}", json_response(200, {"jobId": INFO_ID, "status": "pending"}), json_response(200, INFO_SUCCESS))
    with api.client() as vk:
        info = vk.info_and_wait("https://youtu.be/dQw4w9WgXcQ", include_audio_tracks=True)
    assert json.loads(api.requests[0].content) == {"url": "https://youtu.be/dQw4w9WgXcQ", "includeAudioTracks": True}
    assert info.title == "Rick Astley - Never Gonna Give You Up"
    assert info.audio is not None
    assert [(t.type, t.language) for t in info.audio] == [("original", "en"), ("dubbed", "es")]


def test_info_omits_include_audio_tracks_by_default(api: FakeAPI) -> None:
    api.add("POST", "/info", json_response(200, {"jobId": INFO_ID, "status": "pending"}))
    with api.client() as vk:
        job = vk.info("https://youtu.be/x")
    assert json.loads(api.requests[0].content) == {"url": "https://youtu.be/x"}
    assert job.job_id == INFO_ID


def test_info_failed(api: FakeAPI) -> None:
    api.add(
        "GET",
        f"/info/{INFO_ID}",
        json_response(200, {"jobId": INFO_ID, "status": "failed", "error": "Video unavailable", "errorCode": "PRIVATE_VIDEO"}),
    )
    with api.client() as vk, pytest.raises(JobFailedError) as exc:
        vk.wait_for_info(INFO_ID)
    assert exc.value.error_code == "PRIVATE_VIDEO"
    assert exc.value.message == "Video unavailable"


def test_me(api: FakeAPI) -> None:
    api.add("GET", "/me", json_response(200, ACCOUNT))
    with api.client() as vk:
        me = vk.me()
    assert me.plan.id == "starter"
    assert me.plan.renews_at == 1758326400000
    assert me.balances["bandwidth_mb"].remaining == 347520
    assert me.balances["info"].resets_at == 1758326400000
    assert me["balances"]["download_count"]["used"] == 123


def test_list_channel(api: FakeAPI) -> None:
    payload = {"id": "UC1", "title": "Rick Astley", "channel_id": "UC1", "entries": [{"id": "dQw4w9WgXcQ", "title": "NGGYU"}]}
    api.add("POST", "/list-channel", json_response(200, payload))
    with api.client() as vk:
        listing = vk.list_channel("https://www.youtube.com/@RickAstleyYT", max_videos=25)
    assert json.loads(api.requests[0].content) == {"channelUrl": "https://www.youtube.com/@RickAstleyYT", "maxVideos": 25}
    assert listing.entries[0]["id"] == "dQw4w9WgXcQ"
    assert listing.channel_id == "UC1"


# --------------------------------------------------------------------------- #
# save_file
# --------------------------------------------------------------------------- #


def test_save_file_into_directory_without_leaking_key(api: FakeAPI, tmp_path: Path) -> None:
    api.add("GET", CDN_URL, httpx.Response(200, content=b"hello world", headers={"content-length": "11"}))
    progress: List[tuple] = []
    with api.client() as vk:
        path = vk.save_file(DownloadStatus.from_dict(COMPLETED), tmp_path, chunk_size=4, on_bytes=lambda w, t: progress.append((w, t)))
    assert path == tmp_path / "Rick Astley - Never Gonna Give You Up.mp4"
    assert path.read_bytes() == b"hello world"
    assert not list(tmp_path.glob("*.part"))
    assert "Authorization" not in api.requests[0].headers
    assert progress[-1] == (11, 11)


def test_save_file_from_url_to_explicit_path(api: FakeAPI, tmp_path: Path) -> None:
    api.add("GET", CDN_URL, httpx.Response(200, content=b"abc"))
    target = tmp_path / "sub" / "out.bin"
    with api.client() as vk:
        assert vk.save_file(CDN_URL, target) == target
    assert target.read_bytes() == b"abc"


def test_save_file_retries_get_and_raises_on_404(api: FakeAPI, tmp_path: Path) -> None:
    api.add("GET", CDN_URL, httpx.Response(503), httpx.Response(200, content=b"ok"))
    with api.client() as vk:
        assert vk.save_file(CDN_URL, tmp_path / "a.mp4").read_bytes() == b"ok"
    api.routes.clear()
    api.add("GET", CDN_URL, httpx.Response(404, text="gone"))
    with api.client() as vk, pytest.raises(NotFoundError):
        vk.save_file(CDN_URL, tmp_path / "b.mp4")
    assert not (tmp_path / "b.mp4").exists() and not (tmp_path / "b.mp4.part").exists()


def test_save_file_requires_completed_status(api: FakeAPI) -> None:
    with api.client() as vk, pytest.raises(ValueError):
        vk.save_file(DownloadStatus.from_dict(PENDING))


# --------------------------------------------------------------------------- #
# Filenames and lifecycle
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "fmt, url, ext",
    [
        ("1080", "https://cdn/x.mp4", "mp4"),
        ("360", None, "mp4"),
        ("audio-mp3", "https://cdn/x.mp3", "mp3"),
        ("audio-aac", "https://cdn/x.m4a", "m4a"),
        ("audio", "https://cdn/x.webm", "webm"),
        ("audio", "https://cdn/x.m4a?sig=1", "m4a"),
        ("audio", "https://cdn/noext", "m4a"),
    ],
)
def test_extension_for(fmt: str, url: str, ext: str) -> None:
    assert vidkraken.extension_for(fmt, url) == ext


def test_suggested_filename_sanitizes_title() -> None:
    status = DownloadStatus.from_dict({**COMPLETED, "title": 'AC/DC: "Thunder" <live>?', "actualFormat": "audio", "downloadUrl": "https://cdn/x.webm"})
    assert vidkraken.suggested_filename(status) == "AC DC Thunder live.webm"
    assert vidkraken.suggested_filename(DownloadStatus.from_dict({**COMPLETED, "title": "..."})) == f"{JOB_ID}.mp4"


def test_close_only_closes_owned_client() -> None:
    external = httpx.Client()
    VidKraken(api_key="k", http_client=external).close()
    assert not external.is_closed
    owned = VidKraken(api_key="k")
    owned.close()
    assert owned._client.is_closed

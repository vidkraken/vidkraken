"""Async client parity. Uses ``asyncio.run`` so no pytest plugin is needed."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Coroutine, List, TypeVar

import httpx
import pytest

from vidkraken import (
    APIConnectionError,
    AsyncVidKraken,
    DownloadStatus,
    JobFailedError,
    MissingAPIKeyError,
    RateLimitError,
    ServerError,
)

from conftest import (
    ACCOUNT,
    API_KEY,
    COMPLETED,
    FAILED,
    JOB_ID,
    PENDING,
    RATE_HEADERS,
    SUBMITTED,
    FakeAPI,
    api_error,
    json_response,
)

T = TypeVar("T")


def run(coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def test_async_missing_key() -> None:
    with pytest.raises(MissingAPIKeyError):
        AsyncVidKraken()


def test_async_download_auth_and_rate_limit(api: FakeAPI) -> None:
    api.add("POST", "/download", json_response(200, SUBMITTED, RATE_HEADERS))

    async def go() -> Any:
        async with api.async_client() as vk:
            return await vk.download("https://youtu.be/x", format="720")

    job = run(go())
    assert api.requests[0].headers["Authorization"] == f"Bearer {API_KEY}"
    assert json.loads(api.requests[0].content) == {"url": "https://youtu.be/x", "format": "720"}
    assert job.job_id == JOB_ID
    assert job.rate_limit is not None and (job.rate_limit.limit, job.rate_limit.remaining) == (100, 37)


def test_async_post_download_not_retried_on_500(api: FakeAPI, sleeps: List[float]) -> None:
    api.add("POST", "/download", api_error(500, "INTERNAL_ERROR"), json_response(200, SUBMITTED))

    async def go() -> None:
        async with api.async_client() as vk:
            await vk.download("https://youtu.be/x")

    with pytest.raises(ServerError):
        run(go())
    assert len(api.requests) == 1
    assert sleeps == []


def test_async_post_not_retried_on_network_error(api: FakeAPI) -> None:
    api.add("POST", "/download", httpx.ConnectError("reset"))

    async def go() -> None:
        async with api.async_client() as vk:
            await vk.download("https://youtu.be/x")

    with pytest.raises(APIConnectionError):
        run(go())
    assert len(api.requests) == 1


def test_async_post_retried_on_429_with_retry_after(api: FakeAPI, sleeps: List[float]) -> None:
    api.add(
        "POST",
        "/download",
        api_error(429, "TOO_MANY_PENDING", headers={"Retry-After": "3"}),
        json_response(200, SUBMITTED),
    )

    async def go() -> Any:
        async with api.async_client() as vk:
            return await vk.download("https://youtu.be/x")

    assert run(go()).job_id == JOB_ID
    assert sleeps == [3.0]


def test_async_429_exhausted(api: FakeAPI) -> None:
    api.add("POST", "/download", api_error(429, "TOO_MANY_PENDING", headers={"Retry-After": "1"}))

    async def go() -> None:
        async with api.async_client(max_retries=1) as vk:
            await vk.download("https://youtu.be/x")

    with pytest.raises(RateLimitError):
        run(go())
    assert len(api.requests) == 2


def test_async_get_retried(api: FakeAPI) -> None:
    api.add("GET", "/me", httpx.ConnectError("reset"), api_error(503, "UNAVAILABLE"), json_response(200, ACCOUNT))

    async def go() -> Any:
        async with api.async_client() as vk:
            return await vk.me()

    assert run(go()).name == "Jane Developer"
    assert len(api.requests) == 3


def test_async_wait_with_async_progress_callback(api: FakeAPI) -> None:
    api.add("GET", f"/download/{JOB_ID}", json_response(200, PENDING), json_response(200, COMPLETED))
    seen: List[str] = []

    async def on_progress(status: DownloadStatus) -> None:
        seen.append(status.status)

    async def go() -> DownloadStatus:
        async with api.async_client() as vk:
            return await vk.wait_for_download(JOB_ID, on_progress=on_progress)

    assert run(go()).is_completed
    assert seen == ["IN_PROGRESS", "COMPLETED"]


def test_async_wait_failed(api: FakeAPI) -> None:
    api.add("GET", f"/download/{JOB_ID}", json_response(200, FAILED))

    async def go() -> None:
        async with api.async_client() as vk:
            await vk.wait_for_download(JOB_ID)

    with pytest.raises(JobFailedError) as exc:
        run(go())
    assert exc.value.error_code == "AGE_RESTRICTED"


def test_async_save_file(api: FakeAPI, tmp_path: Path) -> None:
    api.add("GET", COMPLETED["downloadUrl"], httpx.Response(200, content=b"async bytes"))

    async def go() -> Path:
        async with api.async_client() as vk:
            return await vk.save_file(DownloadStatus.from_dict(COMPLETED), tmp_path)

    path = run(go())
    assert path.read_bytes() == b"async bytes"
    assert path.suffix == ".mp4"
    assert "Authorization" not in api.requests[0].headers

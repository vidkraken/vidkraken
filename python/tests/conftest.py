"""Shared test helpers. No test ever touches the network: every client is wired
to an ``httpx.MockTransport`` that serves scripted responses."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import httpx
import pytest

import vidkraken._async_client as async_client_module
import vidkraken._client as client_module
from vidkraken import AsyncVidKraken, VidKraken

BASE = "https://vidkraken.com/api/v2"
API_KEY = "vk_test_key"

Reply = Union[httpx.Response, Exception, Callable[[httpx.Request], httpx.Response]]


def json_response(status: int, body: Any, headers: Optional[Dict[str, str]] = None) -> httpx.Response:
    return httpx.Response(status, content=json.dumps(body).encode(), headers={"content-type": "application/json", **(headers or {})})


def api_error(status: int, code: str, message: str = "boom", **extra: Any) -> httpx.Response:
    headers = extra.pop("headers", None)
    return json_response(status, {"error": message, "code": code, **extra}, headers)


class FakeAPI:
    """Scripted responses keyed by ``(METHOD, url-or-path)``; each key holds a
    queue of replies (the last one repeats). Every request is recorded."""

    def __init__(self) -> None:
        self.routes: Dict[Tuple[str, str], List[Reply]] = {}
        self.requests: List[httpx.Request] = []

    def add(self, method: str, path: str, *replies: Reply) -> "FakeAPI":
        url = path if path.startswith("http") else BASE + path
        self.routes.setdefault((method, url), []).extend(replies)
        return self

    def calls(self, method: str, path: str) -> List[httpx.Request]:
        url = path if path.startswith("http") else BASE + path
        return [r for r in self.requests if r.method == method and str(r.url) == url]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = (request.method, str(request.url))
        queue = self.routes.get(key)
        if not queue:
            return json_response(404, {"error": f"no route for {key}", "code": "NOT_FOUND"})
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reply, Exception):
            raise reply
        if callable(reply) and not isinstance(reply, httpx.Response):
            return reply(request)
        return reply

    async def async_handler(self, request: httpx.Request) -> httpx.Response:
        return self.handler(request)

    def client(self, **kwargs: Any) -> VidKraken:
        kwargs.setdefault("api_key", API_KEY)
        return VidKraken(http_client=httpx.Client(transport=httpx.MockTransport(self.handler)), **kwargs)

    def async_client(self, **kwargs: Any) -> AsyncVidKraken:
        kwargs.setdefault("api_key", API_KEY)
        return AsyncVidKraken(
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(self.async_handler)), **kwargs
        )


@pytest.fixture
def api() -> FakeAPI:
    return FakeAPI()


@pytest.fixture(autouse=True)
def sleeps(monkeypatch: pytest.MonkeyPatch) -> List[float]:
    """Replace real sleeping with a recorder so retry/poll tests are instant."""
    recorded: List[float] = []

    def fake_sleep(seconds: float) -> None:
        recorded.append(seconds)

    async def fake_async_sleep(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr(client_module, "_sleep", fake_sleep)
    monkeypatch.setattr(async_client_module, "_sleep", fake_async_sleep)
    return recorded


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VIDKRAKEN_API_KEY", raising=False)
    monkeypatch.delenv("VIDKRAKEN_BASE_URL", raising=False)


# Canned API payloads ---------------------------------------------------------

JOB_ID = "69cfc45e8a3b4d2f9c1e0a7b"
CDN_URL = f"https://proxy.vidkraken.com/media/{JOB_ID}.mp4"

SUBMITTED = {
    "jobId": JOB_ID,
    "title": "Rick Astley - Never Gonna Give You Up",
    "duration": 213,
    "status": "IN_QUEUE",
    "message": "Download job enqueued. Poll GET /api/v2/download/{jobId} for status.",
}
RATE_HEADERS = {"RateLimit-Policy": '"pending";q=100;qu="concurrent-requests"', "RateLimit": '"pending";r=37'}

PENDING = {
    "jobId": JOB_ID,
    "status": "IN_PROGRESS",
    "title": SUBMITTED["title"],
    "duration": 213,
    "url": "https://youtu.be/dQw4w9WgXcQ",
    "format": "1080",
    "language": None,
    "createdAt": "2026-09-23T20:10:00Z",
    "startedAt": "2026-09-23T20:10:04Z",
    "completedAt": None,
    "progress": {
        "stage": "downloading",
        "percent": 41,
        "bytesDownloaded": 43500000,
        "totalBytes": 102300000,
        "totalBytesIsEstimate": False,
        "etaSeconds": 12,
        "updatedAt": "2026-09-23T20:10:19Z",
    },
}
QUEUED = {**PENDING, "status": "IN_QUEUE", "startedAt": None, "progress": None}
COMPLETED = {
    "jobId": JOB_ID,
    "status": "COMPLETED",
    "url": "https://youtu.be/dQw4w9WgXcQ",
    "format": "1080",
    "actualFormat": "1080",
    "language": None,
    "downloadUrl": CDN_URL,
    "title": SUBMITTED["title"],
    "duration": 213,
    "fileSize": 11,
}
FAILED = {
    "jobId": JOB_ID,
    "status": "FAILED",
    "errorCode": "AGE_RESTRICTED",
    "title": "Some Restricted Video",
    "duration": 412,
    "url": "https://youtu.be/example",
    "format": "1080",
    "language": None,
}
ACCOUNT = {
    "name": "Jane Developer",
    "email": "jane@example.com",
    "plan": {"id": "starter", "renewsAt": 1758326400000, "canceledAt": None},
    "balances": {
        "download_count": {"granted": 1000, "used": 123, "remaining": 877, "unlimited": False, "resetsAt": 1758326400000},
        "bandwidth_mb": {"granted": 400000, "used": 52480, "remaining": 347520, "unlimited": False, "resetsAt": 1758326400000},
        "info": {"granted": 5000, "used": 310, "remaining": 4690, "unlimited": False, "resetsAt": 1758326400000},
    },
}
INFO_ID = "6f9c1234-5e8a-4b2f-9c1e-0a7b69cfc45e"
INFO_SUCCESS = {
    "jobId": INFO_ID,
    "status": "success",
    "result": {
        "title": "Rick Astley - Never Gonna Give You Up",
        "duration": 213,
        "url": "https://youtu.be/dQw4w9WgXcQ",
        "audio": [{"type": "original", "language": "en"}, {"type": "dubbed", "language": "es"}],
    },
}

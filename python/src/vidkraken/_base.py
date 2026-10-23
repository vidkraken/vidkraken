"""Transport-agnostic logic shared by the sync and async clients.

Everything that does not depend on *how* bytes are sent lives here: config,
headers, request bodies, the retry policy, error parsing and the polling
state machine. ``_client.py`` and ``_async_client.py`` only add the I/O.
"""

from __future__ import annotations

import os
import random
import time
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Dict, Optional, Union

import httpx

from ._exceptions import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    JobFailedError,
    MissingAPIKeyError,
    WaitTimeoutError,
    error_class_for_status,
)
from ._models import DownloadStatus, InfoStatus, RateLimit, VideoInfo
from ._version import __version__

DEFAULT_BASE_URL = "https://vidkraken.com/api/v2"
DEFAULT_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
DEFAULT_MAX_RETRIES = 2
USER_AGENT = f"vidkraken-python/{__version__}"

#: Backoff between retries: 0.5s, 1s, 2s... capped, with +-25% jitter.
INITIAL_RETRY_DELAY = 0.5
MAX_RETRY_DELAY = 8.0
#: A ``Retry-After`` longer than this is not slept on; the error is raised instead.
MAX_RETRY_AFTER = 60.0

#: GETs are idempotent: retry these statuses (and network errors).
RETRYABLE_GET_STATUSES = frozenset({429, 500, 502, 503, 504})

# Fallback codes when an error body has none (mirrors the API's defaults).
_DEFAULT_CODES = {
    400: "INVALID_REQUEST",
    401: "UNAUTHORIZED",
    403: "LIMIT_REACHED",
    404: "NOT_FOUND",
    429: "RATE_LIMITED",
    503: "UNAVAILABLE",
}

TimeoutTypes = Union[float, httpx.Timeout, None]


def parse_retry_after(value: Optional[str]) -> Optional[float]:
    """``Retry-After`` as seconds (accepts delta-seconds or an HTTP date)."""
    if not value:
        return None
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return None
        if when is None:
            return None
        seconds = when.timestamp() - time.time()
    return max(0.0, seconds)


def make_api_error(response: httpx.Response) -> APIError:
    """Turn an HTTP error response into the matching ``APIError`` subclass."""
    status = response.status_code
    body: Any = None
    try:
        body = response.json()
    except Exception:  # non-JSON body (proxy error page, CDN, ...)
        body = None

    message: Optional[str] = None
    code: Optional[str] = None
    details: Optional[str] = None
    if isinstance(body, dict):
        raw_message = body.get("error") or body.get("message")
        message = str(raw_message) if raw_message else None
        code = body.get("code") if isinstance(body.get("code"), str) else None
        if body.get("details") is not None:
            details = str(body["details"])
    if not message:
        text = (response.text or "").strip() if body is None else ""
        message = text[:200] if text else (response.reason_phrase or f"HTTP {status}")
    if not code:
        code = _DEFAULT_CODES.get(status, "INTERNAL_ERROR" if status >= 500 else "HTTP_ERROR")

    cls = error_class_for_status(status)
    return cls(
        message,
        status=status,
        code=code,
        details=details,
        retry_after=parse_retry_after(response.headers.get("retry-after")),
        headers=response.headers,
        body=body,
        rate_limit=RateLimit.from_headers(response.headers),
    )


def wrap_transport_error(exc: Exception, method: str, path: str) -> APIConnectionError:
    unsafe = method != "GET"
    note = (
        f" {method} {path} was not retried automatically because the API has no idempotency "
        "key: the request may have been processed. Check before resubmitting."
        if unsafe
        else ""
    )
    if isinstance(exc, httpx.TimeoutException):
        return APITimeoutError(f"Request timed out: {method} {path}.{note}")
    return APIConnectionError(f"Connection error on {method} {path}: {exc}.{note}")


class BaseClient:
    """Configuration and policy shared by ``VidKraken`` and ``AsyncVidKraken``."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        timeout: TimeoutTypes = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        key = api_key or os.environ.get("VIDKRAKEN_API_KEY")
        if not key or not key.strip():
            raise MissingAPIKeyError()
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        self.api_key = key.strip()
        self.base_url = (base_url or os.environ.get("VIDKRAKEN_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries

    def __repr__(self) -> str:
        return f"{type(self).__name__}(base_url={self.base_url!r}, max_retries={self.max_retries})"

    # ------------------------------------------------------------------ request

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }

    def _build_request(
        self, client: Union[httpx.Client, httpx.AsyncClient], method: str, path: str, json: Optional[Dict[str, Any]]
    ) -> httpx.Request:
        return client.build_request(method, self._url(path), headers=self._headers(), json=json, timeout=self.timeout)

    # ------------------------------------------------------------ retry policy

    def _backoff(self, attempt: int) -> float:
        delay = min(INITIAL_RETRY_DELAY * (2**attempt), MAX_RETRY_DELAY)
        return float(delay * random.uniform(0.75, 1.25))

    def _retry_delay_for_error(self, method: str, attempt: int, exc: Exception) -> Optional[float]:
        """Delay before retrying after a network error, or ``None`` to give up.

        Only GETs are retried: a POST that hit a network error may have been
        processed, and the API has no idempotency key.
        """
        if attempt >= self.max_retries or method != "GET":
            return None
        if not isinstance(exc, httpx.TransportError):
            return None
        return self._backoff(attempt)

    def _retry_delay_for_status(self, method: str, attempt: int, error: APIError) -> Optional[float]:
        """Delay before retrying an HTTP error, or ``None`` to raise it.

        - GET: 429, 500, 502, 503, 504.
        - POST: only 429 (nothing was queued) and 503 ``UNAVAILABLE`` (job not
          queued). Never 500/502/504: the job may have been created and billed.
        """
        if attempt >= self.max_retries:
            return None
        status = error.status
        if method == "GET":
            retryable = status in RETRYABLE_GET_STATUSES
        else:
            retryable = status == 429 or (status == 503 and error.code == "UNAVAILABLE")
        if not retryable:
            return None
        if error.retry_after is not None:
            if error.retry_after > MAX_RETRY_AFTER:
                return None
            return error.retry_after
        return self._backoff(attempt)

    @staticmethod
    def _json(response: httpx.Response) -> Dict[str, Any]:
        try:
            data = response.json()
        except Exception as exc:
            raise APIError(
                f"Invalid JSON in response: {exc}",
                status=response.status_code,
                code="INVALID_RESPONSE",
                headers=response.headers,
            ) from exc
        if not isinstance(data, dict):
            raise APIError(
                "Unexpected response body (expected a JSON object)",
                status=response.status_code,
                code="INVALID_RESPONSE",
                headers=response.headers,
                body=data,
            )
        return data

    # -------------------------------------------------------------- bodies

    @staticmethod
    def _download_body(
        url: str,
        format: Optional[str],
        start_time: Optional[int],
        end_time: Optional[int],
        language: Optional[str],
        webhook_url: Optional[str],
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {"url": url}
        if format is not None:
            body["format"] = format
        if start_time is not None:
            body["startTime"] = start_time
        if end_time is not None:
            body["endTime"] = end_time
        if language is not None:
            body["language"] = language
        if webhook_url is not None:
            body["webhookUrl"] = webhook_url
        return body

    @staticmethod
    def _info_body(url: str, include_audio_tracks: bool) -> Dict[str, Any]:
        body: Dict[str, Any] = {"url": url}
        if include_audio_tracks:
            body["includeAudioTracks"] = True
        return body

    @staticmethod
    def _list_channel_body(channel_url: str, max_videos: Optional[int]) -> Dict[str, Any]:
        body: Dict[str, Any] = {"channelUrl": channel_url}
        if max_videos is not None:
            body["maxVideos"] = max_videos
        return body


# --------------------------------------------------------------------------- #
# Polling
# --------------------------------------------------------------------------- #


class Poller:
    """Deadline bookkeeping for ``wait_for_*`` (no I/O)."""

    def __init__(
        self,
        job_id: str,
        poll_interval: float,
        timeout: Optional[float],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if poll_interval < 0:
            raise ValueError("poll_interval must be >= 0")
        self.job_id = job_id
        self.poll_interval = poll_interval
        self.timeout = timeout
        self._clock = clock
        self._deadline = None if timeout is None else clock() + timeout

    def next_delay(self, last: Any) -> float:
        """Seconds to sleep before the next poll; raises once the deadline passed."""
        if self._deadline is None:
            return self.poll_interval
        remaining = self._deadline - self._clock()
        if remaining <= 0:
            raise WaitTimeoutError(self.job_id, float(self.timeout or 0), last)
        return min(self.poll_interval, remaining)


def check_download(status: DownloadStatus) -> bool:
    """``True`` when completed, ``False`` while pending; raises on FAILED."""
    if status.is_completed:
        return True
    if status.is_failed:
        raise JobFailedError(status.job_id, status.error_code, status.error_message, job=status)
    return False


def check_info(status: InfoStatus) -> Optional[VideoInfo]:
    """The ``VideoInfo`` on success, ``None`` while pending; raises on failure."""
    if status.is_success:
        return status.result if status.result is not None else VideoInfo.from_dict({})
    if status.is_failed:
        raise JobFailedError(status.job_id, status.error_code, status.error or "Info job failed", job=status)
    return None

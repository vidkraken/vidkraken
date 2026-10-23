"""Synchronous client."""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import TracebackType
from typing import Any, Callable, Dict, Optional, Type, Union

import httpx

from ._base import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT,
    USER_AGENT,
    BaseClient,
    Poller,
    TimeoutTypes,
    check_download,
    check_info,
    make_api_error,
    wrap_transport_error,
)
from ._files import PathLike, resolve_target
from ._models import (
    DEFAULT_FORMAT,
    Account,
    ChannelListing,
    DownloadJob,
    DownloadStatus,
    InfoJob,
    InfoStatus,
    MediaFormat,
    VideoInfo,
)

__all__ = ["VidKraken"]

ProgressCallback = Callable[[DownloadStatus], Any]
BytesCallback = Callable[[int, Optional[int]], Any]


def _sleep(seconds: float) -> None:
    """Indirection so tests can patch out real sleeping."""
    time.sleep(seconds)


class VidKraken(BaseClient):
    """Synchronous Vid Kraken client.

    >>> from vidkraken import VidKraken
    >>> with VidKraken() as vk:                       # reads VIDKRAKEN_API_KEY
    ...     done = vk.download_and_wait("https://youtu.be/dQw4w9WgXcQ", format="720")
    ...     vk.save_file(done, "video.mp4")

    Args:
        api_key: API key; defaults to the ``VIDKRAKEN_API_KEY`` environment variable.
        base_url: API root; defaults to ``https://vidkraken.com/api/v2``.
        timeout: Per-request timeout in seconds (float or ``httpx.Timeout``).
        max_retries: Automatic retries for safe failures (see README). ``0`` disables.
        http_client: Your own ``httpx.Client`` (proxies, transports...). It is
            not closed by :meth:`close`.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        timeout: TimeoutTypes = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        super().__init__(api_key, base_url=base_url, timeout=timeout, max_retries=max_retries)
        self._owns_client = http_client is None
        self._client = http_client if http_client is not None else httpx.Client(timeout=timeout)

    # ----------------------------------------------------------------- lifecycle

    def close(self) -> None:
        """Close the underlying HTTP client (unless you passed your own)."""
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "VidKraken":
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        self.close()

    # ------------------------------------------------------------------- core

    def _request(self, method: str, path: str, json: Optional[Dict[str, Any]] = None) -> httpx.Response:
        attempt = 0
        while True:
            request = self._build_request(self._client, method, path, json)
            try:
                response = self._client.send(request)
            except httpx.TransportError as exc:
                delay = self._retry_delay_for_error(method, attempt, exc)
                if delay is None:
                    raise wrap_transport_error(exc, method, path) from exc
            else:
                if response.is_success:
                    return response
                response.read()
                error = make_api_error(response)
                delay = self._retry_delay_for_status(method, attempt, error)
                if delay is None:
                    raise error
            _sleep(delay)
            attempt += 1

    def _get_json(self, path: str) -> Dict[str, Any]:
        return self._json(self._request("GET", path))

    def _post_json(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        return self._json(self._request("POST", path, body))

    # --------------------------------------------------------------- downloads

    def download(
        self,
        url: str,
        format: Union[MediaFormat, str] = DEFAULT_FORMAT,
        *,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        language: Optional[str] = None,
        webhook_url: Optional[str] = None,
    ) -> DownloadJob:
        """Queue a download (``POST /download``). Never retried on 5xx or network errors."""
        body = self._download_body(url, format, start_time, end_time, language, webhook_url)
        response = self._request("POST", "/download", body)
        return DownloadJob.from_response(self._json(response), response.headers)

    def get_download(self, job_id: str) -> DownloadStatus:
        """Current status of a download job (``GET /download/{jobId}``)."""
        return DownloadStatus.from_dict(self._get_json(f"/download/{job_id}"))

    def wait_for_download(
        self,
        job_id: str,
        *,
        poll_interval: float = 3.0,
        timeout: Optional[float] = None,
        on_progress: Optional[ProgressCallback] = None,
    ) -> DownloadStatus:
        """Poll until the job is ``COMPLETED`` and return that status.

        Raises :class:`JobFailedError` on ``FAILED`` and :class:`WaitTimeoutError`
        after ``timeout`` seconds. ``on_progress`` is called with every polled status.
        """
        poller = Poller(job_id, poll_interval, timeout)
        while True:
            status = self.get_download(job_id)
            if on_progress is not None:
                on_progress(status)
            if check_download(status):
                return status
            _sleep(poller.next_delay(status))

    def download_and_wait(
        self,
        url: str,
        format: Union[MediaFormat, str] = DEFAULT_FORMAT,
        *,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        language: Optional[str] = None,
        webhook_url: Optional[str] = None,
        poll_interval: float = 3.0,
        timeout: Optional[float] = None,
        on_progress: Optional[ProgressCallback] = None,
    ) -> DownloadStatus:
        """:meth:`download` then :meth:`wait_for_download`."""
        job = self.download(
            url, format, start_time=start_time, end_time=end_time, language=language, webhook_url=webhook_url
        )
        return self.wait_for_download(job.job_id, poll_interval=poll_interval, timeout=timeout, on_progress=on_progress)

    def save_file(
        self,
        source: Union[str, DownloadStatus],
        path: Optional[PathLike] = None,
        *,
        chunk_size: int = 1024 * 1024,
        on_bytes: Optional[BytesCallback] = None,
    ) -> Path:
        """Stream a finished download to disk and return the file path.

        ``source`` is a completed :class:`DownloadStatus` or a ``downloadUrl``.
        ``path`` may be a file, a directory (the file is named after the video
        title) or ``None`` (current directory). The file is written to
        ``<path>.part`` and renamed when complete. Your API key is not sent to
        the file host. ``on_bytes(written, total)`` reports progress.
        """
        url, target = resolve_target(source, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".part")
        attempt = 0
        while True:
            try:
                with self._client.stream(
                    "GET", url, headers={"User-Agent": USER_AGENT}, timeout=self.timeout, follow_redirects=True
                ) as response:
                    if not response.is_success:
                        response.read()
                        error = make_api_error(response)
                        delay = self._retry_delay_for_status("GET", attempt, error)
                        if delay is None:
                            raise error
                    else:
                        total = response.headers.get("content-length")
                        total_bytes = int(total) if total and total.isdigit() else None
                        written = 0
                        with open(tmp, "wb") as fh:
                            for chunk in response.iter_bytes(chunk_size):
                                fh.write(chunk)
                                written += len(chunk)
                                if on_bytes is not None:
                                    on_bytes(written, total_bytes)
                        os.replace(tmp, target)
                        return target
            except httpx.TransportError as exc:
                _unlink(tmp)
                delay = self._retry_delay_for_error("GET", attempt, exc)
                if delay is None:
                    raise wrap_transport_error(exc, "GET", url) from exc
            except BaseException:
                _unlink(tmp)
                raise
            _sleep(delay)
            attempt += 1

    # -------------------------------------------------------------------- info

    def info(self, url: str, *, include_audio_tracks: bool = False) -> InfoJob:
        """Queue a metadata lookup (``POST /info``). Never retried on 5xx or network errors."""
        return InfoJob.from_dict(self._post_json("/info", self._info_body(url, include_audio_tracks)))

    def get_info(self, job_id: str) -> InfoStatus:
        """Current status of an info job (``GET /info/{jobId}``)."""
        return InfoStatus.from_dict(self._get_json(f"/info/{job_id}"))

    def wait_for_info(
        self,
        job_id: str,
        *,
        poll_interval: float = 1.0,
        timeout: Optional[float] = None,
    ) -> VideoInfo:
        """Poll until the info job succeeds and return its :class:`VideoInfo`."""
        poller = Poller(job_id, poll_interval, timeout)
        while True:
            status = self.get_info(job_id)
            result = check_info(status)
            if result is not None:
                return result
            _sleep(poller.next_delay(status))

    def info_and_wait(
        self,
        url: str,
        *,
        include_audio_tracks: bool = False,
        poll_interval: float = 1.0,
        timeout: Optional[float] = None,
    ) -> VideoInfo:
        """:meth:`info` then :meth:`wait_for_info`."""
        job = self.info(url, include_audio_tracks=include_audio_tracks)
        return self.wait_for_info(job.job_id, poll_interval=poll_interval, timeout=timeout)

    # ----------------------------------------------------------- channel / me

    def list_channel(self, channel_url: str, *, max_videos: Optional[int] = None) -> ChannelListing:
        """List a channel's recent videos (``POST /list-channel``, paid add-on)."""
        return ChannelListing.from_dict(self._post_json("/list-channel", self._list_channel_body(channel_url, max_videos)))

    def me(self) -> Account:
        """Account, plan and live balances (``GET /me``, free)."""
        return Account.from_dict(self._get_json("/me"))


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass

"""Typed response objects.

Every object keeps the untouched JSON body in ``.raw`` and supports
``obj["camelCaseKey"]`` / ``obj.get(...)`` for raw access, so fields the SDK
does not model yet are never lost. Attributes are the snake_case versions of
the API's camelCase field names. Timestamps are kept as the API's ISO 8601
strings (or Unix milliseconds on ``/me``), exactly as sent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Mapping, Optional, Tuple


__all__ = [
    "MediaFormat",
    "FORMATS",
    "DEFAULT_FORMAT",
    "DOWNLOAD_ERROR_CODES",
    "RateLimit",
    "DownloadJob",
    "DownloadProgress",
    "DownloadStatus",
    "InfoJob",
    "AudioTrack",
    "RegionRestriction",
    "VideoInfo",
    "InfoStatus",
    "Plan",
    "FeatureBalance",
    "Account",
    "ChannelListing",
]

MediaFormat = Literal["1080", "720", "480", "360", "audio", "audio-aac", "audio-mp3"]
FORMATS: Tuple[str, ...] = ("1080", "720", "480", "360", "audio", "audio-aac", "audio-mp3")
DEFAULT_FORMAT = "audio"

DownloadJobState = Literal["IN_QUEUE", "IN_PROGRESS", "COMPLETED", "FAILED"]
InfoJobState = Literal["pending", "success", "failed"]

#: ``DownloadErrorCode`` values and what they mean. Other values may appear.
DOWNLOAD_ERROR_CODES: Dict[str, str] = {
    "AGE_RESTRICTED": "YouTube requires sign-in (age restriction) for this video",
    "MEMBERS_ONLY": "The video is restricted to channel members",
    "PAYMENT_REQUIRED": "The video requires a purchase or rental",
    "PRIVATE_VIDEO": "The video is private",
    "VIDEO_PREMIERE": "The premiere has not aired yet; retry after it starts",
    "GEO_RESTRICTED": "The video is not available in the server region",
    "VIDEO_PROCESSING": "YouTube is still processing the upload",
    "VIDEO_UNAVAILABLE": "The video is unavailable",
    "DRM_PROTECTED": "The video is DRM protected",
    "VIDEO_NOT_FOUND": "No such video, or it has been deleted",
    "FORMAT_UNAVAILABLE": "The requested format/quality is not offered for this video",
    "LIVE_STREAM_IN_PROGRESS": "The video is still live; retry once it has ended",
    "COPYRIGHTED_MUSIC": "Copyrighted music cannot be downloaded",
    "NO_DOWNLOAD_OPT_OUT": "The creator opted out of downloads (#nodownload)",
    "FILE_TOO_LARGE": "The file is too large in this format; try a lower quality or a shorter clip",
    "NETWORK_ERROR": "Transient network failure on Vid Kraken's side; retry after a short backoff",
}


class _RawAccess:
    """Dict-style access to the raw JSON body."""

    raw: Dict[str, Any]

    def __getitem__(self, key: str) -> Any:
        return self.raw[key]

    def __contains__(self, key: object) -> bool:
        return key in self.raw

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw.get(key, default)

    def to_dict(self) -> Dict[str, Any]:
        """A shallow copy of the raw JSON body."""
        return dict(self.raw)


def _int_or_none(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Rate limits
# --------------------------------------------------------------------------- #

_PARAM_RE = re.compile(r'\s*([A-Za-z0-9_\-\*\.]+)\s*=\s*("(?:[^"\\]|\\.)*"|[^;,\s]*)\s*')


def _parse_sf_list(value: str) -> List[Tuple[str, Dict[str, str]]]:
    """Parse a (simplified) RFC 8941 list such as ``"pending";q=100;qu="x"``."""
    items: List[Tuple[str, Dict[str, str]]] = []
    for member in value.split(","):
        parts = [p.strip() for p in member.split(";")]
        if not parts or not parts[0]:
            continue
        name = parts[0].strip('"')
        params: Dict[str, str] = {}
        for part in parts[1:]:
            m = _PARAM_RE.fullmatch(part)
            if m:
                params[m.group(1).lower()] = m.group(2).strip('"')
        items.append((name, params))
    return items


@dataclass(frozen=True)
class RateLimit:
    """Pending-downloads headroom from the IETF ``RateLimit``/``RateLimit-Policy`` headers.

    ``limit`` is how many downloads may be queued or in progress at once (``q``),
    ``remaining`` how many more can be queued right now (``r``).
    """

    limit: Optional[int]
    remaining: Optional[int]
    policy: Optional[str] = None
    quota_unit: Optional[str] = None

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> Optional["RateLimit"]:
        lowered = {k.lower(): v for k, v in headers.items()}
        policy_header = lowered.get("ratelimit-policy")
        state_header = lowered.get("ratelimit")
        if not policy_header and not state_header:
            return None
        policies = _parse_sf_list(policy_header or "")
        states = _parse_sf_list(state_header or "")
        # Prefer the "pending" policy; otherwise take the first one listed.
        name: Optional[str] = None
        for candidate, _ in policies + states:
            if candidate == "pending":
                name = candidate
                break
        if name is None:
            name = (policies or states)[0][0] if (policies or states) else None
        policy_params = next((p for n, p in policies if n == name), {})
        state_params = next((p for n, p in states if n == name), {})
        return cls(
            limit=_int_or_none(policy_params.get("q")),
            remaining=_int_or_none(state_params.get("r")),
            policy=name,
            quota_unit=policy_params.get("qu"),
        )


# --------------------------------------------------------------------------- #
# Downloads
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DownloadJob(_RawAccess):
    """Result of ``POST /download``: the job was queued (``status == "IN_QUEUE"``)."""

    job_id: str
    title: Optional[str]
    duration: Optional[int]
    status: str
    message: Optional[str] = None
    rate_limit: Optional[RateLimit] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_response(cls, data: Dict[str, Any], headers: Optional[Mapping[str, str]] = None) -> "DownloadJob":
        return cls(
            job_id=data.get("jobId", ""),
            title=data.get("title"),
            duration=data.get("duration"),
            status=data.get("status", "IN_QUEUE"),
            message=data.get("message"),
            rate_limit=RateLimit.from_headers(headers) if headers is not None else None,
            raw=data,
        )


@dataclass(frozen=True)
class DownloadProgress(_RawAccess):
    """Live progress of a running download job."""

    stage: Optional[str]
    percent: Optional[int]
    bytes_downloaded: Optional[int] = None
    total_bytes: Optional[int] = None
    total_bytes_is_estimate: Optional[bool] = None
    eta_seconds: Optional[int] = None
    queue_position: Optional[int] = None
    updated_at: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DownloadProgress":
        return cls(
            stage=data.get("stage"),
            percent=data.get("percent"),
            bytes_downloaded=data.get("bytesDownloaded"),
            total_bytes=data.get("totalBytes"),
            total_bytes_is_estimate=data.get("totalBytesIsEstimate"),
            eta_seconds=data.get("etaSeconds"),
            queue_position=data.get("queuePosition"),
            updated_at=data.get("updatedAt"),
            raw=data,
        )


@dataclass(frozen=True)
class DownloadStatus(_RawAccess):
    """Result of ``GET /download/{jobId}`` (and the webhook body).

    ``status`` is ``IN_QUEUE``/``IN_PROGRESS`` (see ``progress``), ``COMPLETED``
    (see ``download_url``, ``actual_format``, ``file_size``) or ``FAILED`` (see
    ``error_code``).
    """

    job_id: str
    status: str
    title: Optional[str] = None
    duration: Optional[int] = None
    url: Optional[str] = None
    format: Optional[str] = None
    language: Optional[str] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    progress: Optional[DownloadProgress] = None
    actual_format: Optional[str] = None
    download_url: Optional[str] = None
    file_size: Optional[int] = None
    error_code: Optional[str] = None
    error: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def is_pending(self) -> bool:
        return self.status in ("IN_QUEUE", "IN_PROGRESS")

    @property
    def is_completed(self) -> bool:
        return self.status == "COMPLETED"

    @property
    def is_failed(self) -> bool:
        return self.status == "FAILED"

    @property
    def is_terminal(self) -> bool:
        return self.is_completed or self.is_failed

    @property
    def error_message(self) -> str:
        """Best human-readable reason for a FAILED job."""
        if self.error:
            return self.error
        if self.error_code and self.error_code in DOWNLOAD_ERROR_CODES:
            return DOWNLOAD_ERROR_CODES[self.error_code]
        if self.error_code:
            return f"Download failed ({self.error_code})"
        return "Download failed"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DownloadStatus":
        progress = data.get("progress")
        return cls(
            job_id=data.get("jobId", ""),
            status=data.get("status", ""),
            title=data.get("title"),
            duration=data.get("duration"),
            url=data.get("url"),
            format=data.get("format"),
            language=data.get("language"),
            created_at=data.get("createdAt"),
            started_at=data.get("startedAt"),
            completed_at=data.get("completedAt"),
            progress=DownloadProgress.from_dict(progress) if isinstance(progress, dict) else None,
            actual_format=data.get("actualFormat"),
            download_url=data.get("downloadUrl"),
            file_size=data.get("fileSize"),
            error_code=data.get("errorCode"),
            error=data.get("error") or data.get("errorMessage"),
            raw=data,
        )


# --------------------------------------------------------------------------- #
# Video info
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class InfoJob(_RawAccess):
    """Result of ``POST /info``: the job was queued (``status == "pending"``)."""

    job_id: str
    status: str
    raw: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "InfoJob":
        return cls(job_id=data.get("jobId", ""), status=data.get("status", "pending"), raw=data)


@dataclass(frozen=True)
class AudioTrack(_RawAccess):
    type: Optional[str]
    language: Optional[str]
    raw: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AudioTrack":
        return cls(type=data.get("type"), language=data.get("language"), raw=data)


@dataclass(frozen=True)
class RegionRestriction(_RawAccess):
    allowed: Optional[List[str]] = None
    blocked: Optional[List[str]] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RegionRestriction":
        return cls(allowed=data.get("allowed"), blocked=data.get("blocked"), raw=data)


@dataclass(frozen=True)
class VideoInfo(_RawAccess):
    """Video metadata. ``audio`` is only set when requested with ``include_audio_tracks``."""

    title: Optional[str]
    duration: Optional[int]
    url: Optional[str]
    audio: Optional[List[AudioTrack]] = None
    region_restriction: Optional[RegionRestriction] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VideoInfo":
        audio = data.get("audio")
        region = data.get("regionRestriction")
        return cls(
            title=data.get("title"),
            duration=data.get("duration"),
            url=data.get("url"),
            audio=[AudioTrack.from_dict(a) for a in audio if isinstance(a, dict)] if isinstance(audio, list) else None,
            region_restriction=RegionRestriction.from_dict(region) if isinstance(region, dict) else None,
            raw=data,
        )


@dataclass(frozen=True)
class InfoStatus(_RawAccess):
    """Result of ``GET /info/{jobId}``: ``pending``, ``success`` (see ``result``) or ``failed``."""

    job_id: str
    status: str
    result: Optional[VideoInfo] = None
    error: Optional[str] = None
    error_code: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def is_pending(self) -> bool:
        return self.status == "pending"

    @property
    def is_success(self) -> bool:
        return self.status == "success"

    @property
    def is_failed(self) -> bool:
        return self.status == "failed"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "InfoStatus":
        result = data.get("result")
        return cls(
            job_id=data.get("jobId", ""),
            status=data.get("status", ""),
            result=VideoInfo.from_dict(result) if isinstance(result, dict) else None,
            error=data.get("error"),
            error_code=data.get("errorCode"),
            raw=data,
        )


# --------------------------------------------------------------------------- #
# Account
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Plan(_RawAccess):
    """``id`` is ``free``, ``starter``, ``growth``, ``scale`` or ``custom``. Times are Unix ms."""

    id: Optional[str]
    renews_at: Optional[float] = None
    canceled_at: Optional[float] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Plan":
        return cls(
            id=data.get("id"),
            renews_at=data.get("renewsAt"),
            canceled_at=data.get("canceledAt"),
            raw=data,
        )


@dataclass(frozen=True)
class FeatureBalance(_RawAccess):
    """Live balance for one metered feature. ``resets_at`` is Unix ms or ``None``."""

    granted: Optional[float]
    used: Optional[float]
    remaining: Optional[float]
    unlimited: bool = False
    resets_at: Optional[float] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FeatureBalance":
        return cls(
            granted=data.get("granted"),
            used=data.get("used"),
            remaining=data.get("remaining"),
            unlimited=bool(data.get("unlimited", False)),
            resets_at=data.get("resetsAt"),
            raw=data,
        )


@dataclass(frozen=True)
class Account(_RawAccess):
    """Result of ``GET /me``. ``balances`` is keyed by feature id (``download_count``,
    ``bandwidth_mb``, ``info``, and add-ons such as ``channel_list``)."""

    name: Optional[str]
    email: Optional[str]
    plan: Plan
    balances: Dict[str, FeatureBalance]
    raw: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Account":
        balances = data.get("balances") or {}
        return cls(
            name=data.get("name"),
            email=data.get("email"),
            plan=Plan.from_dict(data.get("plan") or {}),
            balances={k: FeatureBalance.from_dict(v) for k, v in balances.items() if isinstance(v, dict)},
            raw=data,
        )


# --------------------------------------------------------------------------- #
# Channel listing
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ChannelListing(_RawAccess):
    """Result of ``POST /list-channel``: raw ``yt-dlp --flat-playlist -J`` output.

    Field names are yt-dlp's (already snake_case); ``entries`` are plain dicts.
    """

    id: Optional[str]
    title: Optional[str]
    channel: Optional[str] = None
    channel_id: Optional[str] = None
    channel_url: Optional[str] = None
    entries: List[Dict[str, Any]] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChannelListing":
        entries = data.get("entries")
        return cls(
            id=data.get("id"),
            title=data.get("title"),
            channel=data.get("channel"),
            channel_id=data.get("channel_id"),
            channel_url=data.get("channel_url"),
            entries=[e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else [],
            raw=data,
        )

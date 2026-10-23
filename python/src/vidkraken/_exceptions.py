"""Exception hierarchy for the Vid Kraken SDK.

::

    VidKrakenError
    ├── MissingAPIKeyError          no key passed and VIDKRAKEN_API_KEY unset
    ├── APIConnectionError          network failure, no HTTP response
    │   └── APITimeoutError
    ├── APIError                    the API answered with an HTTP error
    │   ├── BadRequestError         400 INVALID_REQUEST
    │   ├── AuthenticationError     401 UNAUTHORIZED
    │   ├── PermissionDeniedError   403 LIMIT_REACHED / FORBIDDEN
    │   ├── NotFoundError           404 NOT_FOUND / VIDEO_NOT_FOUND
    │   ├── RateLimitError          429 TOO_MANY_PENDING / RATE_LIMITED
    │   └── ServerError             5xx INTERNAL_ERROR
    │       └── ServiceUnavailableError   503 UNAVAILABLE
    ├── JobFailedError              a job reached FAILED / failed
    └── WaitTimeoutError            wait_for_* gave up (also a TimeoutError)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Mapping, Optional, Type

from ._models import DOWNLOAD_ERROR_CODES

if TYPE_CHECKING:
    from ._models import RateLimit

__all__ = [
    "VidKrakenError",
    "MissingAPIKeyError",
    "APIConnectionError",
    "APITimeoutError",
    "APIError",
    "BadRequestError",
    "AuthenticationError",
    "PermissionDeniedError",
    "NotFoundError",
    "RateLimitError",
    "ServerError",
    "ServiceUnavailableError",
    "JobFailedError",
    "WaitTimeoutError",
]


class VidKrakenError(Exception):
    """Base class for every error raised by this package.

    Attributes mirror the API's error body ``{ error, code, details? }``:

    - ``status``: HTTP status code, or ``None`` when there was no HTTP response.
    - ``code``: machine-readable code (e.g. ``"TOO_MANY_PENDING"``). Branch on this.
    - ``message``: human-readable message (the API's ``error`` field).
    - ``details``: optional extra context from the API.
    - ``retry_after``: seconds from the ``Retry-After`` header, if any.
    """

    def __init__(
        self,
        message: str,
        *,
        status: Optional[int] = None,
        code: Optional[str] = None,
        details: Optional[str] = None,
        retry_after: Optional[float] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.details = details
        self.retry_after = retry_after

    def __str__(self) -> str:
        parts = []
        if self.code:
            parts.append(self.code)
        if self.status is not None:
            parts.append(f"(HTTP {self.status})")
        prefix = " ".join(parts)
        text = f"{prefix}: {self.message}" if prefix else self.message
        if self.details:
            text += f" [{self.details}]"
        return text

    def to_dict(self) -> Dict[str, Any]:
        """A JSON-serialisable view of the error (used by the CLI's ``--json``)."""
        return {
            "error": self.message,
            "code": self.code,
            "status": self.status,
            "details": self.details,
            "retryAfter": self.retry_after,
        }


class MissingAPIKeyError(VidKrakenError):
    """No API key was given and ``VIDKRAKEN_API_KEY`` is not set."""

    def __init__(self) -> None:
        super().__init__(
            "No API key provided. Pass api_key=... or set the VIDKRAKEN_API_KEY "
            "environment variable. Get a key at https://vidkraken.com/login",
            code="MISSING_API_KEY",
        )


class APIConnectionError(VidKrakenError):
    """The request never got an HTTP response (DNS, TLS, connection reset...)."""

    def __init__(self, message: str = "Connection error.", *, code: str = "CONNECTION_ERROR") -> None:
        super().__init__(message, code=code)


class APITimeoutError(APIConnectionError):
    """The request timed out."""

    def __init__(self, message: str = "Request timed out.") -> None:
        super().__init__(message, code="TIMEOUT")


class APIError(VidKrakenError):
    """The API returned an HTTP error response."""

    status: int

    def __init__(
        self,
        message: str,
        *,
        status: int,
        code: Optional[str] = None,
        details: Optional[str] = None,
        retry_after: Optional[float] = None,
        headers: Optional[Mapping[str, str]] = None,
        body: Any = None,
        rate_limit: Optional["RateLimit"] = None,
    ) -> None:
        super().__init__(message, status=status, code=code, details=details, retry_after=retry_after)
        self.headers: Dict[str, str] = dict(headers or {})
        self.body = body
        self.rate_limit = rate_limit


class BadRequestError(APIError):
    """400: the request is invalid; retrying it unchanged fails again."""


class AuthenticationError(APIError):
    """401: missing or invalid API key."""


class PermissionDeniedError(APIError):
    """403: the plan's allowance is used up (``LIMIT_REACHED``) or access is denied."""


class NotFoundError(APIError):
    """404: job not found/expired (``NOT_FOUND``) or no such video (``VIDEO_NOT_FOUND``)."""


class RateLimitError(APIError):
    """429: too many pending downloads or requests. See ``retry_after``."""


class ServerError(APIError):
    """5xx: an error on Vid Kraken's side."""


class ServiceUnavailableError(ServerError):
    """503 ``UNAVAILABLE``: temporarily unable to accept the request; nothing was queued."""


_STATUS_CLASSES: Dict[int, Type[APIError]] = {
    400: BadRequestError,
    401: AuthenticationError,
    403: PermissionDeniedError,
    404: NotFoundError,
    429: RateLimitError,
    503: ServiceUnavailableError,
}


def error_class_for_status(status: int) -> Type[APIError]:
    """Pick the exception class by HTTP status (codes are informational)."""
    if status in _STATUS_CLASSES:
        return _STATUS_CLASSES[status]
    if status >= 500:
        return ServerError
    return APIError


class JobFailedError(VidKrakenError):
    """A download or info job finished in the failed state.

    ``error_code`` is a ``DownloadErrorCode`` such as ``"AGE_RESTRICTED"`` (may be
    ``None``). ``retryable`` is ``True`` for ``NETWORK_ERROR`` and for a missing or
    unrecognised code (the API documents unlisted codes as transient); every
    listed code describes the video or policy, so resubmitting returns the same
    result.
    ``job`` holds the final status object.
    """

    def __init__(
        self,
        job_id: str,
        error_code: Optional[str],
        message: str,
        *,
        job: Any = None,
    ) -> None:
        super().__init__(message, code=error_code)
        self.job_id = job_id
        self.error_code = error_code
        self.retryable = (
            error_code == "NETWORK_ERROR"
            or not error_code
            or error_code not in DOWNLOAD_ERROR_CODES
        )
        self.job = job

    def __str__(self) -> str:
        code = self.error_code or "JOB_FAILED"
        return f"{code}: {self.message} (job {self.job_id})"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error": self.message,
            "code": self.error_code or "JOB_FAILED",
            "jobId": self.job_id,
            "retryable": self.retryable,
        }


class WaitTimeoutError(VidKrakenError, TimeoutError):
    """``wait_for_*`` did not see a terminal state within ``timeout`` seconds.

    The job keeps running server-side; poll it again later with ``job_id``.
    """

    def __init__(self, job_id: str, timeout: float, last: Any = None) -> None:
        super().__init__(
            f"Job {job_id} did not finish within {timeout:g}s; it is still running server-side.",
            code="WAIT_TIMEOUT",
        )
        self.job_id = job_id
        self.timeout = timeout
        self.last = last

    def to_dict(self) -> Dict[str, Any]:
        data = super().to_dict()
        data["jobId"] = self.job_id
        return data

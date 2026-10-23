"""Official Python SDK for the Vid Kraken YouTube download API.

    >>> from vidkraken import VidKraken
    >>> vk = VidKraken()                    # or VidKraken(api_key="...")
    >>> done = vk.download_and_wait("https://youtu.be/dQw4w9WgXcQ", format="audio-mp3")
    >>> vk.save_file(done, "song.mp3")

Docs: https://vidkraken.com/docs
"""

from ._async_client import AsyncVidKraken
from ._base import DEFAULT_BASE_URL, DEFAULT_MAX_RETRIES, DEFAULT_TIMEOUT
from ._client import VidKraken
from ._exceptions import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    JobFailedError,
    MissingAPIKeyError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
    ServiceUnavailableError,
    VidKrakenError,
    WaitTimeoutError,
)
from ._files import extension_for, sanitize_filename, suggested_filename
from ._models import (
    DEFAULT_FORMAT,
    DOWNLOAD_ERROR_CODES,
    FORMATS,
    Account,
    AudioTrack,
    ChannelListing,
    DownloadJob,
    DownloadProgress,
    DownloadStatus,
    FeatureBalance,
    InfoJob,
    InfoStatus,
    MediaFormat,
    Plan,
    RateLimit,
    RegionRestriction,
    VideoInfo,
)
from ._version import __version__

__all__ = [
    "__version__",
    # clients
    "VidKraken",
    "AsyncVidKraken",
    "DEFAULT_BASE_URL",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_TIMEOUT",
    # models
    "MediaFormat",
    "FORMATS",
    "DEFAULT_FORMAT",
    "DOWNLOAD_ERROR_CODES",
    "RateLimit",
    "DownloadJob",
    "DownloadProgress",
    "DownloadStatus",
    "InfoJob",
    "InfoStatus",
    "VideoInfo",
    "AudioTrack",
    "RegionRestriction",
    "Account",
    "Plan",
    "FeatureBalance",
    "ChannelListing",
    # errors
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
    # helpers
    "extension_for",
    "sanitize_filename",
    "suggested_filename",
]

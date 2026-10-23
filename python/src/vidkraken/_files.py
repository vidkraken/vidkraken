"""Filename helpers shared by ``save_file`` and the CLI."""

from __future__ import annotations

import os
import posixpath
import re
import unicodedata
from pathlib import Path
from typing import Optional, Tuple, Union
from urllib.parse import unquote, urlparse

from ._models import DownloadStatus

__all__ = ["extension_for", "sanitize_filename", "suggested_filename", "resolve_target"]

_VIDEO_FORMATS = {"1080", "720", "480", "360"}
_FIXED_EXTENSIONS = {"audio-mp3": "mp3", "audio-aac": "m4a"}
_UNSAFE = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]+')
_MAX_STEM = 150


def _url_extension(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    ext = posixpath.splitext(unquote(urlparse(url).path))[1].lstrip(".").lower()
    return ext if ext and ext.isalnum() and len(ext) <= 5 else None


def extension_for(format: Optional[str], download_url: Optional[str] = None) -> str:
    """File extension (no dot) for a delivered format.

    Video formats are MP4, ``audio-mp3`` is MP3 and ``audio-aac`` is M4A. Plain
    ``audio`` keeps YouTube's container, so its extension comes from the
    download URL (``m4a`` if the URL has none).
    """
    if format in _VIDEO_FORMATS:
        return "mp4"
    if format in _FIXED_EXTENSIONS:
        return _FIXED_EXTENSIONS[format]
    return _url_extension(download_url) or ("m4a" if format == "audio" or format is None else "bin")


def sanitize_filename(name: str, fallback: str = "download") -> str:
    """Make ``name`` safe as a file name on Windows, macOS and Linux."""
    name = unicodedata.normalize("NFC", name)
    name = _UNSAFE.sub(" ", name)
    name = re.sub(r"\s+", " ", name).strip().strip(".").strip()
    if len(name) > _MAX_STEM:
        name = name[:_MAX_STEM].rstrip(" .")
    if not name or name.upper() in {"CON", "PRN", "AUX", "NUL"} or re.fullmatch(r"(?i)(COM|LPT)\d", name):
        return fallback
    return name


def suggested_filename(status: DownloadStatus) -> str:
    """``<sanitised title>.<ext>`` for a completed download status."""
    fmt = status.actual_format or status.format
    stem = sanitize_filename(status.title or "", fallback=status.job_id or "download")
    return f"{stem}.{extension_for(fmt, status.download_url)}"


PathLike = Union[str, "os.PathLike[str]"]


def resolve_target(source: Union[str, DownloadStatus], path: Optional[PathLike]) -> Tuple[str, Path]:
    """Work out ``(download_url, destination_file)`` for ``save_file``.

    ``path`` may be a file path, an existing directory (or one ending in a path
    separator), or ``None`` for the current directory.
    """
    if isinstance(source, DownloadStatus):
        if not source.download_url:
            raise ValueError(
                f"Job {source.job_id} has no downloadUrl (status {source.status}); "
                "wait for it to complete first."
            )
        url = source.download_url
        default_name = suggested_filename(source)
    elif isinstance(source, str) and source:
        url = source
        basename = posixpath.basename(unquote(urlparse(source).path))
        default_name = sanitize_filename(basename) if basename else "download"
    else:
        raise TypeError("save_file() expects a download URL or a DownloadStatus")

    if path is None:
        return url, Path.cwd() / default_name
    raw = os.fspath(path)
    target = Path(raw).expanduser()
    if target.is_dir() or raw.endswith(("/", os.sep)):
        return url, target / default_name
    return url, target

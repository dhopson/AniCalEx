"""Cover image cache. Runs on a worker thread."""

from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from anical.net.http import HttpClient, NetworkError, allowed_url

MAX_COVER_BYTES = 5 * 1024 * 1024
_EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def cover_filename(url: str, content_type: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:24] + _EXTENSIONS[content_type]


def download_covers(
    http: HttpClient,
    items: Iterable[tuple[int, str]],
    directory: Path,
    *,
    delay: float = 0.2,
    sleep: Callable[[float], None] = time.sleep,
) -> list[tuple[int, str]]:
    """Download covers; returns (anime_id, path) for each stored image.

    Failures skip that cover (it is retried on the next sync); losing the
    network stops the batch and keeps what was saved so far.
    """
    directory.mkdir(parents=True, exist_ok=True)
    saved: list[tuple[int, str]] = []
    for anime_id, url in items:
        if not allowed_url(url):
            continue  # not a web address: never fetched
        try:
            response = http.request("GET", url)
        except NetworkError:
            break
        content_type = (response.header("Content-Type") or "").split(";")[0].strip().lower()
        if response.status != 200 or content_type not in _EXTENSIONS or not 0 < len(response.body) <= MAX_COVER_BYTES:
            continue
        path = directory / cover_filename(url, content_type)
        temporary = path.with_suffix(path.suffix + ".part")
        temporary.write_bytes(response.body)
        os.replace(temporary, path)
        saved.append((anime_id, str(path)))
        sleep(delay)
    return saved

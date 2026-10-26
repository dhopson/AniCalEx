"""Request pacing and retry backoff shared by the API clients."""

from __future__ import annotations

import time
from collections.abc import Callable

from anical.net.http import Response

MAX_BACKOFF_S = 60.0


class Throttle:
    """Keeps at least `min_interval` seconds between consecutive requests."""

    def __init__(
        self,
        min_interval: float,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self._min_interval = min_interval
        self._sleep = sleep
        self._monotonic = monotonic
        self._last: float | None = None

    def wait(self) -> None:
        now = self._monotonic()
        if self._last is not None:
            remaining = self._min_interval - (now - self._last)
            if remaining > 0:
                self._sleep(remaining)
                now += remaining
        self._last = now

    def sleep(self, seconds: float) -> None:
        self._sleep(seconds)


def backoff_seconds(response: Response, attempt: int) -> float:
    """Honour Retry-After when present, else exponential backoff (2, 4, 8… s), bounded."""
    retry_after = response.header("Retry-After")
    if retry_after and retry_after.isdecimal():
        return min(float(retry_after), MAX_BACKOFF_S)
    return min(2.0**attempt, 30.0)

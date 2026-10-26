"""When an automatic refresh should run.

The time of the last successful refresh is persisted, so restarting the app
inside the interval does not trigger another one.
"""

from __future__ import annotations

REFRESH_INTERVAL_S = 24 * 60 * 60


def refresh_due(last_run: int | None, now: int, interval: int = REFRESH_INTERVAL_S) -> bool:
    """True if no successful refresh happened within `interval` seconds.

    A last-run time in the future (clock moved backwards) counts as due, so
    a bad timestamp can't suppress refreshes indefinitely.
    """
    if last_run is None or last_run > now:
        return True
    return now - last_run >= interval

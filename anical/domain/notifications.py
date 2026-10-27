"""Which episodes to announce as newly available."""

from __future__ import annotations

from collections.abc import Iterable

from anical.domain.calendar import CalendarEntry
from anical.domain.models import WatchState

# Episodes that became available longer ago than this are not announced,
# so starting AniCal after a week away doesn't produce a burst of notifications.
MAX_AGE_S = 12 * 60 * 60
# Above this many at once, one summary notification replaces individual ones.
SUMMARY_THRESHOLD = 3


def to_announce(entries: Iterable[CalendarEntry], now: int, availability_lag_s: int) -> list[CalendarEntry]:
    """Episodes that became probably-available recently and are worth a notification.

    Estimated times are skipped: announcing a guessed date would be noise.
    """
    due = []
    for entry in entries:
        available_at = entry.airs_at + availability_lag_s
        if entry.state is WatchState.UNWATCHED and not entry.estimated and now - MAX_AGE_S < available_at <= now:
            due.append(entry)
    return sorted(due, key=lambda e: (e.airs_at, e.title.casefold(), e.number))

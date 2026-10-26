"""Calendar rules: visible ranges, navigation, grouping and entry status."""

from __future__ import annotations

import calendar
import datetime
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from zoneinfo import ZoneInfo

from anical.domain.models import WatchState
from anical.domain.schedule import AiringSource, Precision

DEFAULT_AVAILABILITY_LAG_S = 2 * 60 * 60


class CalendarMode(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class EntryStatus(StrEnum):
    UPCOMING = "upcoming"
    JUST_AIRED = "just_aired"  # aired, but probably not available yet
    AVAILABLE = "available"  # aired long enough ago that it is probably available
    WATCHED = "watched"

    @property
    def label(self) -> str:
        return {
            EntryStatus.UPCOMING: "Upcoming",
            EntryStatus.JUST_AIRED: "Just aired",
            EntryStatus.AVAILABLE: "Aired",
            EntryStatus.WATCHED: "Watched",
        }[self]


@dataclass(frozen=True)
class CalendarEntry:
    episode_id: int
    anime_id: int
    title: str
    number: int
    airs_at: int
    precision: Precision
    source: AiringSource
    state: WatchState = WatchState.UNWATCHED
    delayed: bool = False
    cover_path: str | None = None
    episode_title: str | None = None
    title_en: str | None = None

    def display_title(self, prefer_english: bool) -> str:
        return self.title_en if prefer_english and self.title_en else self.title

    @property
    def estimated(self) -> bool:
        return self.precision is Precision.ESTIMATED

    def status(self, now: int, availability_lag_s: int = DEFAULT_AVAILABILITY_LAG_S) -> EntryStatus:
        # A scheduled time is only an expectation: "available" means "probably
        # watchable by now", never a confirmed fact.
        if self.state is WatchState.WATCHED:
            return EntryStatus.WATCHED
        if self.airs_at > now:
            return EntryStatus.UPCOMING
        if self.airs_at + availability_lag_s > now:
            return EntryStatus.JUST_AIRED
        return EntryStatus.AVAILABLE


def week_start(day: datetime.date, first_weekday: int) -> datetime.date:
    """first_weekday: 0 = Monday … 6 = Sunday."""
    return day - datetime.timedelta(days=(day.weekday() - first_weekday) % 7)


def visible_range(mode: CalendarMode, anchor: datetime.date, first_weekday: int) -> tuple[datetime.date, datetime.date]:
    """[start, end) dates shown for `mode` around `anchor`. Month views cover whole weeks."""
    if mode is CalendarMode.DAY:
        return anchor, anchor + datetime.timedelta(days=1)
    if mode is CalendarMode.WEEK:
        start = week_start(anchor, first_weekday)
        return start, start + datetime.timedelta(days=7)
    first = anchor.replace(day=1)
    last = anchor.replace(day=calendar.monthrange(anchor.year, anchor.month)[1])
    return week_start(first, first_weekday), week_start(last, first_weekday) + datetime.timedelta(days=7)


def shift(mode: CalendarMode, anchor: datetime.date, steps: int) -> datetime.date:
    if mode is CalendarMode.DAY:
        return anchor + datetime.timedelta(days=steps)
    if mode is CalendarMode.WEEK:
        return anchor + datetime.timedelta(weeks=steps)
    month_index = anchor.year * 12 + anchor.month - 1 + steps
    year, month = divmod(month_index, 12)
    day = min(anchor.day, calendar.monthrange(year, month + 1)[1])
    return datetime.date(year, month + 1, day)


def local_midnight(day: datetime.date, tz: ZoneInfo) -> int:
    return int(datetime.datetime(day.year, day.month, day.day, tzinfo=tz).timestamp())


def utc_bounds(start: datetime.date, end: datetime.date, tz: ZoneInfo) -> tuple[int, int]:
    return local_midnight(start, tz), local_midnight(end, tz)


def local_datetime(epoch: int, tz: ZoneInfo) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(epoch, tz)


def group_by_day(entries: Iterable[CalendarEntry], tz: ZoneInfo) -> dict[datetime.date, list[CalendarEntry]]:
    days: dict[datetime.date, list[CalendarEntry]] = {}
    for entry in sorted(entries, key=lambda e: (e.airs_at, e.title.casefold(), e.number)):
        days.setdefault(local_datetime(entry.airs_at, tz).date(), []).append(entry)
    return days


def format_time(moment: datetime.datetime, use_24h: bool) -> str:
    if use_24h:
        return moment.strftime("%H:%M")
    return moment.strftime("%I:%M %p").lstrip("0")


def range_title(mode: CalendarMode, anchor: datetime.date, first_weekday: int) -> str:
    if mode is CalendarMode.DAY:
        return f"{anchor:%A}, {anchor.day} {anchor:%B %Y}"
    if mode is CalendarMode.MONTH:
        return f"{anchor:%B %Y}"
    start, end = visible_range(mode, anchor, first_weekday)
    last = end - datetime.timedelta(days=1)
    if start.year != last.year:
        return f"{start.day} {start:%b %Y} – {last.day} {last:%b %Y}"
    if start.month != last.month:
        return f"{start.day} {start:%b} – {last.day} {last:%b %Y}"
    return f"{start.day} – {last.day} {last:%b %Y}"

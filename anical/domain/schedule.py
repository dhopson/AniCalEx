"""Airing-time rules: sources, precedence, and weekly projection."""

from __future__ import annotations

import datetime
import re
from enum import StrEnum
from zoneinfo import ZoneInfo

from anical.domain.models import MAX_EPISODES, ValidationError

WEEK = datetime.timedelta(weeks=1)
# Upper bound on generated episodes, so bad data can't create thousands of rows.
MAX_PROJECTED = 200
_FULL_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class AiringSource(StrEnum):
    """Where an airing time came from, in descending precedence."""

    USER = "user"
    ANILIST = "anilist"
    ANIMESCHEDULE = "animeschedule"
    MAL_BROADCAST = "mal_broadcast"


class Precision(StrEnum):
    EXACT = "exact"
    DAY = "day"
    ESTIMATED = "estimated"


def first_broadcast(start: datetime.date, weekday: int, time_hhmm: str, tz: ZoneInfo) -> datetime.datetime | None:
    """The first `weekday` at `time_hhmm` (in `tz`) on or after `start`."""
    match = _TIME.match(time_hhmm or "")
    if not match or not 0 <= weekday <= 6:
        return None
    day = start + datetime.timedelta(days=(weekday - start.weekday()) % 7)
    return datetime.datetime(day.year, day.month, day.day, int(match[1]), int(match[2]), tzinfo=tz)


def weekly_series(first: datetime.datetime, count: int) -> list[int]:
    """Epoch seconds of `count` weekly airings at the same wall-clock time as `first`.

    Arithmetic on zone-aware datetimes is wall-clock arithmetic, so a series
    keeps its local time across DST changes in the source zone.
    """
    return [int((first + WEEK * i).timestamp()) for i in range(max(0, min(count, MAX_PROJECTED)))]


def project_broadcast(
    start_date: str | None,
    weekday: int | None,
    time_hhmm: str | None,
    tz_name: str | None,
    total_episodes: int | None,
    now: int,
    horizon_weeks: int = 4,
) -> list[tuple[int, int]]:
    """Estimate (episode, airs_at) pairs from a weekly broadcast slot.

    Needs an exact start date. Without a known episode count, projects up to
    `horizon_weeks` past `now` rather than guessing an end.
    """
    if not start_date or not _FULL_DATE.match(start_date) or weekday is None or not time_hhmm or not tz_name:
        return []
    try:
        start = datetime.date.fromisoformat(start_date)
        first = first_broadcast(start, weekday, time_hhmm, ZoneInfo(tz_name))
    except (ValueError, KeyError):
        return []
    if first is None:
        return []
    if total_episodes:
        count = total_episodes
    else:
        elapsed_weeks = max(0, (now - int(first.timestamp())) // int(WEEK.total_seconds()))
        count = elapsed_weeks + 1 + horizon_weeks
    return list(enumerate(weekly_series(first, count), start=1))


def manual_series(
    first_episode: int, last_episode: int | None, date_text: str, time_text: str, tz: ZoneInfo
) -> list[tuple[int, int]]:
    """User-entered air dates: one episode, or weekly from `first_episode` through `last_episode`."""
    errors: dict[str, str] = {}
    try:
        day = datetime.date.fromisoformat(date_text.strip())
    except ValueError:
        errors["date"] = "Use YYYY-MM-DD"
    match = _TIME.match(time_text.strip())
    if not match:
        errors["time"] = "Use 24-hour HH:MM"
    if not 1 <= first_episode <= MAX_EPISODES:
        errors["first_episode"] = "Episode numbers start at 1"
    if last_episode is not None and not first_episode <= last_episode <= MAX_EPISODES:
        errors["last_episode"] = "Must be at or after the first episode"
    elif last_episode is not None and last_episode - first_episode >= MAX_PROJECTED:
        errors["last_episode"] = f"At most {MAX_PROJECTED} episodes at once"
    if errors:
        raise ValidationError(errors)
    first = datetime.datetime(day.year, day.month, day.day, int(match[1]), int(match[2]), tzinfo=tz)
    count = 1 if last_episode is None else last_episode - first_episode + 1
    return list(zip(range(first_episode, first_episode + count), weekly_series(first, count)))

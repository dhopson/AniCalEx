"""Library search, filtering and sorting rules."""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from anical.domain.models import AiringStatus, AnimeSummary, WatchProgress


class WatchFilter(StrEnum):
    ALL = "all"
    HAS_UNWATCHED = "has_unwatched"
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    FINISHED = "finished"

    @property
    def label(self) -> str:
        return {
            WatchFilter.ALL: "Any Progress",
            WatchFilter.HAS_UNWATCHED: "Has Unwatched",
            WatchFilter.NOT_STARTED: "Not Started",
            WatchFilter.IN_PROGRESS: "In Progress",
            WatchFilter.FINISHED: "Finished",
        }[self]


class SortKey(StrEnum):
    TITLE = "title"
    RECENTLY_ADDED = "recently_added"
    RECENTLY_UPDATED = "recently_updated"
    AIRING_STATUS = "airing_status"
    UNWATCHED = "unwatched"

    @property
    def label(self) -> str:
        return {
            SortKey.TITLE: "Title",
            SortKey.RECENTLY_ADDED: "Recently Added",
            SortKey.RECENTLY_UPDATED: "Recently Updated",
            SortKey.AIRING_STATUS: "Airing Status",
            SortKey.UNWATCHED: "Most Unwatched",
        }[self]


# Shows the user is most likely acting on come first.
_STATUS_ORDER = {
    AiringStatus.AIRING: 0,
    AiringStatus.UPCOMING: 1,
    AiringStatus.UNKNOWN: 2,
    AiringStatus.COMPLETED: 3,
    AiringStatus.CANCELLED: 4,
}


@dataclass(frozen=True)
class LibraryQuery:
    text: str = ""
    airing_status: AiringStatus | None = None
    watch_filter: WatchFilter = WatchFilter.ALL
    sort: SortKey = SortKey.TITLE
    mal_list_status: str | None = None  # a MAL list status, or None for any


def normalize(text: str) -> str:
    """Case-, accent- and width-insensitive form used for searching and sorting."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(stripped.casefold().split())


def matches(summary: AnimeSummary, query: LibraryQuery) -> bool:
    if query.airing_status is not None and summary.airing_status is not query.airing_status:
        return False
    if not _matches_watch_filter(summary, query.watch_filter):
        return False
    if query.mal_list_status is not None and summary.mal_list_status != query.mal_list_status:
        return False
    needle = normalize(query.text)
    if not needle:
        return True
    haystacks = (summary.title, *summary.alt_titles, *summary.tags)
    return any(needle in normalize(h) for h in haystacks)


def _matches_watch_filter(summary: AnimeSummary, watch_filter: WatchFilter) -> bool:
    match watch_filter:
        case WatchFilter.ALL:
            return True
        case WatchFilter.HAS_UNWATCHED:
            return summary.unwatched_count > 0
        case WatchFilter.NOT_STARTED:
            return summary.progress is WatchProgress.NOT_STARTED
        case WatchFilter.IN_PROGRESS:
            return summary.progress is WatchProgress.IN_PROGRESS
        case WatchFilter.FINISHED:
            return summary.progress is WatchProgress.FINISHED


def sort_key(summary: AnimeSummary, key: SortKey) -> tuple:
    title = normalize(summary.title)
    match key:
        case SortKey.TITLE:
            return (title,)
        case SortKey.RECENTLY_ADDED:
            return (-summary.created_at, title)
        case SortKey.RECENTLY_UPDATED:
            return (-summary.updated_at, title)
        case SortKey.AIRING_STATUS:
            return (_STATUS_ORDER[summary.airing_status], title)
        case SortKey.UNWATCHED:
            return (-summary.unwatched_count, title)


def apply(summaries: Iterable[AnimeSummary], query: LibraryQuery) -> list[AnimeSummary]:
    return sorted((s for s in summaries if matches(s, query)), key=lambda s: sort_key(s, query.sort))

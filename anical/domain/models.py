"""Typed domain models. Nothing here imports GTK or touches the database."""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field, replace
from enum import StrEnum


class AiringStatus(StrEnum):
    UPCOMING = "upcoming"
    AIRING = "airing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        return {
            AiringStatus.UPCOMING: "Upcoming",
            AiringStatus.AIRING: "Airing",
            AiringStatus.COMPLETED: "Finished Airing",
            AiringStatus.CANCELLED: "Cancelled",
            AiringStatus.UNKNOWN: "Unknown Status",
        }[self]


class MediaType(StrEnum):
    TV = "tv"
    MOVIE = "movie"
    OVA = "ova"
    ONA = "ona"
    SPECIAL = "special"
    MUSIC = "music"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        return {
            MediaType.TV: "TV",
            MediaType.MOVIE: "Movie",
            MediaType.OVA: "OVA",
            MediaType.ONA: "ONA",
            MediaType.SPECIAL: "Special",
            MediaType.MUSIC: "Music",
            MediaType.UNKNOWN: "Unknown Type",
        }[self]


class EpisodeKind(StrEnum):
    REGULAR = "regular"
    SPECIAL = "special"
    RECAP = "recap"


class WatchState(StrEnum):
    WATCHED = "watched"
    UNWATCHED = "unwatched"


class WatchSource(StrEnum):
    USER = "user"
    PLAYBACK = "playback"
    MAL_IMPORT = "mal_import"


class WatchProgress(StrEnum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    FINISHED = "finished"


class ValidationError(ValueError):
    """Raised with a mapping of field name to user-facing message."""

    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(errors.values()))
        self.errors = errors


MAX_EPISODES = 10_000
_PARTIAL_DATE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _unique(values: tuple[str, ...], casefold: bool) -> tuple[str, ...]:
    seen: set[str] = set()
    result = []
    for value in values:
        value = value.strip()
        key = value.casefold() if casefold else value
        if value and key not in seen:
            seen.add(key)
            result.append(value)
    return tuple(result)


def _date_error(value: str) -> str | None:
    if not _PARTIAL_DATE.match(value):
        return "Use YYYY, YYYY-MM or YYYY-MM-DD"
    parts = [int(p) for p in value.split("-")]
    try:
        datetime.date(parts[0], parts[1] if len(parts) > 1 else 1, parts[2] if len(parts) > 2 else 1)
    except ValueError:
        return "Not a valid date"
    return None


@dataclass(frozen=True, kw_only=True)
class AnimeDraft:
    """The user-editable fields of an anime entry."""

    title: str
    title_en: str | None = None
    title_ja: str | None = None
    synonyms: tuple[str, ...] = ()
    synopsis: str | None = None
    media_type: MediaType = MediaType.UNKNOWN
    airing_status: AiringStatus = AiringStatus.UNKNOWN
    total_episodes: int | None = None
    start_date: str | None = None
    end_date: str | None = None
    notes: str | None = None
    tags: tuple[str, ...] = ()
    mal_id: int | None = None

    def validated(self) -> AnimeDraft:
        """Return a normalized copy, or raise ValidationError."""
        draft = replace(
            self,
            title=(self.title or "").strip(),
            title_en=_clean(self.title_en),
            title_ja=_clean(self.title_ja),
            synonyms=_unique(self.synonyms, casefold=False),
            synopsis=_clean(self.synopsis),
            start_date=_clean(self.start_date),
            end_date=_clean(self.end_date),
            notes=_clean(self.notes),
            tags=_unique(self.tags, casefold=True),
        )
        errors: dict[str, str] = {}
        if not draft.title:
            errors["title"] = "A title is required"
        if draft.total_episodes is not None and not 0 <= draft.total_episodes <= MAX_EPISODES:
            errors["total_episodes"] = f"Episode count must be between 0 and {MAX_EPISODES}"
        if draft.mal_id is not None and draft.mal_id <= 0:
            errors["mal_id"] = "MyAnimeList IDs are positive numbers"
        for name in ("start_date", "end_date"):
            value = getattr(draft, name)
            if value and (message := _date_error(value)):
                errors[name] = message
        if (
            draft.start_date
            and draft.end_date
            and "start_date" not in errors
            and "end_date" not in errors
            and draft.end_date < draft.start_date
            and not draft.start_date.startswith(draft.end_date)
        ):
            errors["end_date"] = "End date is before the start date"
        if errors:
            raise ValidationError(errors)
        return draft


# Fields an external source supplies and the user may override.
OVERRIDABLE_FIELDS = (
    "title",
    "title_en",
    "title_ja",
    "synonyms",
    "synopsis",
    "media_type",
    "airing_status",
    "total_episodes",
    "start_date",
    "end_date",
)

MAL_LIST_STATUS_LABELS = {
    "watching": "Watching",
    "completed": "Completed",
    "on_hold": "On Hold",
    "dropped": "Dropped",
    "plan_to_watch": "Plan to Watch",
    "removed": "Removed From List",
}


@dataclass(frozen=True, kw_only=True)
class Anime(AnimeDraft):
    id: int
    added_manually: bool
    created_at: int
    updated_at: int
    metadata_source: str | None = None  # None: user-entered; "mal": imported
    overridden: frozenset[str] = frozenset()
    cover_path: str | None = None
    mal_list_status: str | None = None
    mal_episodes_watched: int | None = None


@dataclass(frozen=True, kw_only=True)
class AnimeSummary:
    """A library row: just enough to list, search, filter and sort."""

    id: int
    title: str
    alt_titles: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    media_type: MediaType = MediaType.UNKNOWN
    airing_status: AiringStatus = AiringStatus.UNKNOWN
    total_episodes: int | None = None
    episode_count: int = 0
    watched_count: int = 0
    created_at: int = 0
    updated_at: int = 0
    cover_path: str | None = None
    mal_list_status: str | None = None

    @property
    def progress(self) -> WatchProgress:
        if self.watched_count == 0:
            return WatchProgress.NOT_STARTED
        if self.total_episodes and self.watched_count >= self.total_episodes:
            return WatchProgress.FINISHED
        return WatchProgress.IN_PROGRESS

    @property
    def unwatched_count(self) -> int:
        return max(self.episode_count - self.watched_count, 0)


@dataclass(frozen=True)
class Episode:
    id: int
    anime_id: int
    number: int
    kind: EpisodeKind = EpisodeKind.REGULAR
    title: str | None = None
    state: WatchState = WatchState.UNWATCHED

    @property
    def label(self) -> str:
        prefix = {EpisodeKind.REGULAR: "Episode", EpisodeKind.SPECIAL: "Special", EpisodeKind.RECAP: "Recap"}
        return f"{prefix[self.kind]} {self.number}"


@dataclass(frozen=True)
class EpisodeList:
    episodes: list[Episode] = field(default_factory=list)

    @property
    def watched_count(self) -> int:
        return sum(1 for e in self.episodes if e.kind is EpisodeKind.REGULAR and e.state is WatchState.WATCHED)

    @property
    def regular_count(self) -> int:
        return sum(1 for e in self.episodes if e.kind is EpisodeKind.REGULAR)

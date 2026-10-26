"""MyAnimeList API v2 client: list retrieval and response validation.

API reference: https://myanimelist.net/apiconfig/references/api/v2
"""

from __future__ import annotations

import datetime
import re
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from anical.domain.models import AiringStatus, MediaType
from anical.net.http import HttpClient, Response
from anical.net.throttle import Throttle, backoff_seconds

API_BASE = "https://api.myanimelist.net/v2"
LIST_FIELDS = ",".join(
    (
        "list_status",
        "alternative_titles",
        "main_picture",
        "synopsis",
        "media_type",
        "status",
        "num_episodes",
        "start_date",
        "end_date",
        "broadcast",
        "average_episode_duration",
    )
)
# MAL documents no timezone for `broadcast`; in practice it is JST.
BROADCAST_TZ = "Asia/Tokyo"
LIST_STATUSES = ("watching", "completed", "on_hold", "dropped", "plan_to_watch")
_MAX_PAGES = 100

_MEDIA_TYPES = {
    "tv": MediaType.TV,
    "tv_special": MediaType.SPECIAL,
    "special": MediaType.SPECIAL,
    "movie": MediaType.MOVIE,
    "ova": MediaType.OVA,
    "ona": MediaType.ONA,
    "music": MediaType.MUSIC,
}
_STATUSES = {
    "finished_airing": AiringStatus.COMPLETED,
    "currently_airing": AiringStatus.AIRING,
    "not_yet_aired": AiringStatus.UPCOMING,
}
_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_PARTIAL_DATE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class MalError(Exception):
    pass


class MalAuthError(MalError):
    """The access token was rejected (HTTP 401)."""


@dataclass(frozen=True)
class MalAnime:
    mal_id: int
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
    broadcast_day: int | None = None
    broadcast_time: str | None = None
    episode_duration_s: int | None = None
    picture_url: str | None = None


@dataclass(frozen=True)
class MalListEntry:
    anime: MalAnime
    list_status: str
    episodes_watched: int
    is_rewatching: bool = False
    updated_at: int | None = None


@dataclass(frozen=True)
class MalListResult:
    """A complete list. Entries that failed validation are counted, not guessed at."""

    entries: tuple[MalListEntry, ...]
    skipped: int = 0


def _str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _date(value: Any) -> str | None:
    value = _str(value)
    return value if value and _PARTIAL_DATE.match(value) else None


def _timestamp(value: Any) -> int | None:
    value = _str(value)
    if not value:
        return None
    try:
        return int(datetime.datetime.fromisoformat(value).timestamp())
    except ValueError:
        return None


def _https_url(value: Any) -> str | None:
    value = _str(value)
    return value if value and value.startswith("https://") else None


def parse_anime(node: Any) -> MalAnime | None:
    """Validate one anime node. Returns None if it lacks a usable id or title."""
    if not isinstance(node, dict):
        return None
    mal_id = _non_negative_int(node.get("id"))
    title = _str(node.get("title"))
    if not mal_id or not title:
        return None
    alt = node.get("alternative_titles") if isinstance(node.get("alternative_titles"), dict) else {}
    synonyms = tuple(s for s in (_str(x) for x in alt.get("synonyms") or ()) if s)
    broadcast = node.get("broadcast") if isinstance(node.get("broadcast"), dict) else {}
    day = broadcast.get("day_of_the_week")
    start_time = _str(broadcast.get("start_time"))
    picture = node.get("main_picture") if isinstance(node.get("main_picture"), dict) else {}
    episodes = _non_negative_int(node.get("num_episodes"))
    duration = _non_negative_int(node.get("average_episode_duration"))
    return MalAnime(
        mal_id=mal_id,
        title=title,
        title_en=_str(alt.get("en")),
        title_ja=_str(alt.get("ja")),
        synonyms=synonyms,
        synopsis=_str(node.get("synopsis")),
        media_type=_MEDIA_TYPES.get(node.get("media_type"), MediaType.UNKNOWN),
        airing_status=_STATUSES.get(node.get("status"), AiringStatus.UNKNOWN),
        total_episodes=episodes or None,  # MAL uses 0 for "unknown"
        start_date=_date(node.get("start_date")),
        end_date=_date(node.get("end_date")),
        broadcast_day=_DAYS.index(day) if day in _DAYS else None,
        broadcast_time=start_time if start_time and _TIME.match(start_time) else None,
        episode_duration_s=duration or None,
        picture_url=_https_url(picture.get("large")) or _https_url(picture.get("medium")),
    )


def parse_list_item(item: Any) -> MalListEntry | None:
    if not isinstance(item, dict):
        return None
    anime = parse_anime(item.get("node"))
    status = item.get("list_status") if isinstance(item.get("list_status"), dict) else None
    if anime is None or status is None or status.get("status") not in LIST_STATUSES:
        return None
    watched = _non_negative_int(status.get("num_episodes_watched")) or 0
    if anime.total_episodes is not None:
        watched = min(watched, anime.total_episodes)
    return MalListEntry(
        anime=anime,
        list_status=status["status"],
        episodes_watched=watched,
        is_rewatching=status.get("is_rewatching") is True,
        updated_at=_timestamp(status.get("updated_at")),
    )


def _error_message(response: Response) -> str:
    try:
        body = response.json()
        return str(body.get("message") or body.get("error") or "")
    except (ValueError, AttributeError):
        return ""


class MalClient:
    """Read-only MAL API access. Authenticates with a user token if given, else the client ID."""

    def __init__(
        self,
        http: HttpClient,
        client_id: str,
        access_token: str | None = None,
        *,
        min_interval: float = 1.0,
        max_attempts: int = 4,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self._http = http
        self._client_id = client_id
        self._access_token = access_token
        self._max_attempts = max_attempts
        self._throttle = Throttle(min_interval, sleep, monotonic)

    def fetch_list(self, user: str = "@me") -> MalListResult:
        """Fetch the whole anime list, following `paging.next`."""
        url = f"{API_BASE}/users/{urllib.parse.quote(user, safe='@')}/animelist"
        params: dict[str, str] | None = {"fields": LIST_FIELDS, "limit": "1000", "nsfw": "true"}
        entries: list[MalListEntry] = []
        skipped = 0
        for _ in range(_MAX_PAGES):
            page = self._get(url, params)
            data = page.get("data") if isinstance(page, dict) else None
            if not isinstance(data, list):
                raise MalError("MyAnimeList returned an unexpected response")
            for item in data:
                entry = parse_list_item(item)
                if entry is None:
                    skipped += 1
                else:
                    entries.append(entry)
            next_url = (page.get("paging") or {}).get("next")
            if not isinstance(next_url, str) or not next_url.startswith(API_BASE + "/"):
                break
            url, params = next_url, None  # the next link already carries the query
        else:
            raise MalError("MyAnimeList list paging did not finish")
        return MalListResult(tuple(entries), skipped)

    def fetch_username(self) -> str:
        body = self._get(f"{API_BASE}/users/@me", None)
        name = _str(body.get("name")) if isinstance(body, dict) else None
        if not name:
            raise MalError("MyAnimeList returned no user name")
        return name

    def _get(self, url: str, params: dict[str, str] | None) -> Any:
        if self._access_token:
            headers = {"Authorization": f"Bearer {self._access_token}"}
        else:
            headers = {"X-MAL-CLIENT-ID": self._client_id}
        for attempt in range(1, self._max_attempts + 1):
            self._throttle.wait()
            response = self._http.request("GET", url, params=params, headers=headers)
            if response.status == 200:
                try:
                    return response.json()
                except ValueError as e:
                    raise MalError("MyAnimeList returned invalid JSON") from e
            if response.status == 401:
                raise MalAuthError("MyAnimeList rejected the login; please log in again")
            if response.status == 404:
                raise MalError("MyAnimeList user not found")
            # 403 is also MAL's rate-limit response, so it is retried like 429 and 5xx.
            retryable = response.status in (403, 429) or response.status >= 500
            if not retryable or attempt == self._max_attempts:
                detail = _error_message(response)
                if response.status == 403:
                    raise MalError("MyAnimeList refused the request (the list may be private)")
                raise MalError(f"MyAnimeList error {response.status}{': ' + detail if detail else ''}")
            self._throttle.sleep(backoff_seconds(response, attempt))
        raise AssertionError("unreachable")

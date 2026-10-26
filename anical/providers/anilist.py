"""AniList GraphQL client for airing schedules.

Used only for schedule data on shows already in the library (no bulk
collection). Rate limit: 90 requests/minute normally, 30 while AniList
reports a degraded state; requests are spaced accordingly.
Reference: https://docs.anilist.co/
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from anical.net.http import HttpClient
from anical.net.throttle import Throttle, backoff_seconds

API_URL = "https://graphql.anilist.co"
BATCH = 50  # AniList's maximum page size
_MAX_PAGES = 40
# Plausibility bounds for incoming data.
_EARLIEST = 946_684_800  # 2000-01-01
_LATEST_AHEAD_S = 3 * 365 * 86400
_MAX_EPISODE = 5000

_MEDIA_QUERY = """
query ($ids: [Int], $page: Int) {
  Page(page: $page, perPage: 50) {
    pageInfo { hasNextPage }
    media(idMal_in: $ids, type: ANIME) { id idMal status episodes }
  }
}
"""

_SCHEDULE_QUERY = """
query ($ids: [Int], $start: Int, $end: Int, $page: Int) {
  Page(page: $page, perPage: 50) {
    pageInfo { hasNextPage }
    airingSchedules(mediaId_in: $ids, airingAt_greater: $start, airingAt_lesser: $end, sort: TIME) {
      mediaId episode airingAt
    }
  }
}
"""


class AniListError(Exception):
    pass


@dataclass(frozen=True)
class AniListMedia:
    anilist_id: int
    mal_id: int
    status: str  # FINISHED, RELEASING, NOT_YET_RELEASED, CANCELLED, HIATUS
    episodes: int | None


@dataclass(frozen=True)
class AiringSlot:
    anilist_id: int
    episode: int
    airs_at: int  # UTC epoch seconds


def _positive_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _chunks(values: Sequence[int], size: int) -> Iterable[list[int]]:
    for start in range(0, len(values), size):
        yield list(values[start : start + size])


class AniListClient:
    def __init__(
        self,
        http: HttpClient,
        *,
        min_interval: float = 2.1,  # stays under the degraded 30/minute limit
        max_attempts: int = 4,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        clock: Callable[[], float] = time.time,
    ):
        self._http = http
        self._throttle = Throttle(min_interval, sleep, monotonic)
        self._max_attempts = max_attempts
        self._clock = clock

    def media_by_mal_ids(self, mal_ids: Sequence[int]) -> list[AniListMedia]:
        found: list[AniListMedia] = []
        for chunk in _chunks(sorted(set(mal_ids)), BATCH):
            for page in self._pages(_MEDIA_QUERY, {"ids": chunk}):
                for item in page.get("media") or ():
                    media = self._parse_media(item)
                    if media is not None:
                        found.append(media)
        return found

    def airing_schedules(self, anilist_ids: Sequence[int], start: int, end: int) -> list[AiringSlot]:
        """Every known airing time in (start, end) for the given shows."""
        latest = int(self._clock()) + _LATEST_AHEAD_S
        slots: dict[tuple[int, int], AiringSlot] = {}
        for chunk in _chunks(sorted(set(anilist_ids)), BATCH):
            variables = {"ids": chunk, "start": start, "end": end}
            for page in self._pages(_SCHEDULE_QUERY, variables):
                for item in page.get("airingSchedules") or ():
                    if not isinstance(item, dict):
                        continue
                    media_id = _positive_int(item.get("mediaId"))
                    episode = _positive_int(item.get("episode"))
                    airs_at = _positive_int(item.get("airingAt"))
                    if (
                        media_id in chunk
                        and episode is not None
                        and episode <= _MAX_EPISODE
                        and airs_at is not None
                        and _EARLIEST <= airs_at <= latest
                    ):
                        slots[(media_id, episode)] = AiringSlot(media_id, episode, airs_at)
        return sorted(slots.values(), key=lambda s: (s.airs_at, s.anilist_id, s.episode))

    @staticmethod
    def _parse_media(item: Any) -> AniListMedia | None:
        if not isinstance(item, dict):
            return None
        anilist_id = _positive_int(item.get("id"))
        mal_id = _positive_int(item.get("idMal"))
        if anilist_id is None or mal_id is None:
            return None
        status = item.get("status") if isinstance(item.get("status"), str) else "UNKNOWN"
        return AniListMedia(anilist_id, mal_id, status, _positive_int(item.get("episodes")))

    def _pages(self, query: str, variables: dict) -> Iterable[dict]:
        for page_number in range(1, _MAX_PAGES + 1):
            body = self._post(query, {**variables, "page": page_number})
            page = (body.get("data") or {}).get("Page") if isinstance(body, dict) else None
            if not isinstance(page, dict):
                raise AniListError("AniList returned an unexpected response")
            yield page
            if not (page.get("pageInfo") or {}).get("hasNextPage"):
                return
        raise AniListError("AniList paging did not finish")

    def _post(self, query: str, variables: dict) -> Any:
        for attempt in range(1, self._max_attempts + 1):
            self._throttle.wait()
            response = self._http.request("POST", API_URL, json_body={"query": query, "variables": variables})
            if response.status == 200:
                try:
                    body = response.json()
                except ValueError as e:
                    raise AniListError("AniList returned invalid JSON") from e
                if isinstance(body, dict) and body.get("errors"):
                    message = body["errors"][0].get("message", "unknown error") if body["errors"] else "unknown error"
                    raise AniListError(f"AniList error: {message}")
                return body
            if (response.status == 429 or response.status >= 500) and attempt < self._max_attempts:
                self._throttle.sleep(backoff_seconds(response, attempt))
                continue
            raise AniListError(f"AniList error {response.status}")
        raise AssertionError("unreachable")

"""Airing schedule refresh: network side only (runs on a worker thread)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from anical.db.schedule import ScheduleTarget
from anical.net.http import HttpClient
from anical.providers.anilist import AiringSlot, AniListClient, AniListMedia

PAST_WINDOW_S = 90 * 86400  # re-checked each refresh; older rows are kept, not re-fetched
# About one season ahead. Further out, AniList can list placeholder dates for open-ended shows,
# which would create episodes that may never exist.
FUTURE_WINDOW_S = 120 * 86400


@dataclass(frozen=True)
class ScheduleFetch:
    media: tuple[AniListMedia, ...]
    slots: tuple[AiringSlot, ...]
    window_end: int


def fetch_schedules(http: HttpClient, targets: Sequence[ScheduleTarget], now: int, client=None) -> ScheduleFetch:
    """Map shows to AniList (only those not yet linked) and fetch their airing times."""
    client = client or AniListClient(http)
    known = [AniListMedia(t.anilist_id, t.mal_id, "UNKNOWN", None) for t in targets if t.anilist_id]
    unlinked = [t.mal_id for t in targets if not t.anilist_id]
    media = known + (client.media_by_mal_ids(unlinked) if unlinked else [])
    window_end = now + FUTURE_WINDOW_S
    slots = client.airing_schedules([m.anilist_id for m in media], now - PAST_WINDOW_S, window_end) if media else []
    return ScheduleFetch(tuple(media), tuple(slots), window_end)

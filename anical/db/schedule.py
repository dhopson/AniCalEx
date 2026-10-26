"""Episode airing times: storing source data, projection, and calendar queries.

Rules:
- Each source keeps its own row per episode; the `episode_effective_airing`
  view picks one by precedence (user > anilist > animeschedule > mal_broadcast).
- Past AniList rows are never deleted (AniList only guarantees future data).
  Future rows the source stops listing are removed and logged as changes, so
  the calendar shows "unknown" instead of a stale date.
- A moved airing time is logged in `airing_change`; moving later marks the
  episode as delayed.
- MAL broadcast projections exist only for shows with no AniList data and
  are labelled "estimated".
"""

from __future__ import annotations

import datetime
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass

from anical.db.database import transaction
from anical.db.repository import Clock, sync_episodes, system_clock
from anical.domain.calendar import CalendarEntry
from anical.domain.models import WatchState
from anical.domain.schedule import AiringSource, Precision, project_broadcast
from anical.providers.anilist import AiringSlot, AniListMedia

# Changes smaller than this are treated as noise, not as reschedules.
CHANGE_THRESHOLD_S = 10 * 60
# Finished shows keep getting schedule refreshes for this long after their end date.
RECENTLY_ENDED_DAYS = 60


@dataclass(frozen=True)
class ScheduleTarget:
    anime_id: int
    mal_id: int
    anilist_id: int | None


@dataclass(frozen=True)
class ScheduleSummary:
    shows: int = 0
    airings: int = 0
    changed: int = 0
    removed: int = 0


@dataclass(frozen=True)
class EpisodeAiring:
    airs_at: int
    precision: Precision
    source: AiringSource
    delayed: bool = False

    @property
    def estimated(self) -> bool:
        return self.precision is Precision.ESTIMATED


@dataclass(frozen=True)
class UndatedShow:
    anime_id: int
    title: str
    cover_path: str | None
    title_en: str | None = None

    def display_title(self, prefer_english: bool) -> str:
        return self.title_en if prefer_english and self.title_en else self.title


_ENTRY_COLUMNS = """
    e.id AS episode_id, e.anime_id, e.number, e.title AS episode_title,
    a.title, a.title_en, a.cover_path, x.airs_at, x.precision, x.source, w.state,
    EXISTS (SELECT 1 FROM airing_change c
            WHERE c.episode_id = e.id AND c.source = x.source
              AND c.new_airs_at = x.airs_at AND c.new_airs_at > c.old_airs_at) AS delayed
"""
_ENTRY_JOINS = """
    FROM episode_effective_airing x
    JOIN episode e ON e.id = x.episode_id
    JOIN anime_effective a ON a.id = e.anime_id
    JOIN episode_watch_state w ON w.episode_id = e.id
"""


def _entry(row: sqlite3.Row) -> CalendarEntry:
    return CalendarEntry(
        episode_id=row["episode_id"],
        anime_id=row["anime_id"],
        title=row["title"],
        number=row["number"],
        airs_at=row["airs_at"],
        precision=Precision(row["precision"]),
        source=AiringSource(row["source"]),
        state=WatchState(row["state"]),
        delayed=bool(row["delayed"]),
        cover_path=row["cover_path"],
        episode_title=row["episode_title"],
        title_en=row["title_en"],
    )


class ScheduleRepository:
    def __init__(self, conn: sqlite3.Connection, clock: Clock = system_clock):
        self._conn = conn
        self._clock = clock

    # Refresh

    def targets(self, now: int) -> list[ScheduleTarget]:
        """Library shows with a MAL ID whose schedule may still change."""
        cutoff = (datetime.date.fromtimestamp(now) - datetime.timedelta(days=RECENTLY_ENDED_DAYS)).isoformat()
        rows = self._conn.execute(
            """SELECT a.id, mal.ext_id AS mal_id, al.ext_id AS anilist_id
               FROM anime_effective a
               JOIN external_id mal ON mal.anime_id = a.id AND mal.provider = 'mal'
               LEFT JOIN external_id al ON al.anime_id = a.id AND al.provider = 'anilist'
               WHERE a.in_library = 1
                 AND (a.airing_status IN ('airing', 'upcoming', 'unknown')
                      OR (a.end_date IS NOT NULL AND a.end_date >= ?))
               ORDER BY a.id""",
            (cutoff,),
        )
        return [
            ScheduleTarget(r["id"], int(r["mal_id"]), int(r["anilist_id"]) if r["anilist_id"] else None)
            for r in rows
        ]

    def apply_anilist(
        self, media: Iterable[AniListMedia], slots: Iterable[AiringSlot], window_end: int, now: int
    ) -> ScheduleSummary:
        slots_by_media: dict[int, list[AiringSlot]] = {}
        for slot in slots:
            slots_by_media.setdefault(slot.anilist_id, []).append(slot)
        shows = airings = changed = removed = 0

        with transaction(self._conn):
            for item in media:
                anime_id = self._anime_for_mal(item.mal_id)
                if anime_id is None or not self._link_anilist(anime_id, item.anilist_id):
                    continue
                shows += 1
                show_slots = slots_by_media.get(item.anilist_id, [])
                total = self._conn.execute(
                    "SELECT total_episodes FROM anime_effective WHERE id = ?", (anime_id,)
                ).fetchone()[0]
                sync_episodes(self._conn, anime_id, total, at_least=max((s.episode for s in show_slots), default=0))

                listed: set[int] = set()
                for slot in show_slots:
                    episode_id = self._episode_id(anime_id, slot.episode)
                    listed.add(episode_id)
                    changed += self._record_change(episode_id, AiringSource.ANILIST, slot.airs_at, now)
                    self._upsert_airing(episode_id, AiringSource.ANILIST, slot.airs_at, Precision.EXACT, now)
                    airings += 1

                stale = self._conn.execute(
                    """SELECT ea.episode_id, ea.airs_at FROM episode_airing ea JOIN episode e ON e.id = ea.episode_id
                       WHERE e.anime_id = ? AND ea.source = 'anilist' AND ea.airs_at > ? AND ea.airs_at < ?""",
                    (anime_id, now, window_end),
                ).fetchall()
                for row in stale:
                    if row["episode_id"] in listed:
                        continue
                    self._conn.execute(
                        """INSERT INTO airing_change (episode_id, source, old_airs_at, new_airs_at, detected_at)
                           VALUES (?, 'anilist', ?, NULL, ?)""",
                        (row["episode_id"], row["airs_at"], now),
                    )
                    self._conn.execute(
                        "DELETE FROM episode_airing WHERE episode_id = ? AND source = 'anilist'", (row["episode_id"],)
                    )
                    removed += 1
        return ScheduleSummary(shows, airings, changed, removed)

    def project_broadcasts(self, now: int) -> int:
        """Rebuild estimated airings from MAL broadcast slots. Returns the number of shows projected."""
        with transaction(self._conn):
            # Drop projections that a better source superseded or that no longer apply.
            self._conn.execute(
                """DELETE FROM episode_airing WHERE source = 'mal_broadcast' AND episode_id IN (
                       SELECT e.id FROM episode e JOIN anime_effective a ON a.id = e.anime_id
                       WHERE a.airing_status NOT IN ('airing', 'upcoming')
                          OR EXISTS (SELECT 1 FROM episode e2 JOIN episode_airing x ON x.episode_id = e2.id
                                     WHERE e2.anime_id = a.id AND x.source = 'anilist'))"""
            )
            candidates = self._conn.execute(
                """SELECT a.id, a.start_date, a.broadcast_day, a.broadcast_time, a.broadcast_tz, a.total_episodes
                   FROM anime_effective a
                   WHERE a.in_library = 1 AND a.airing_status IN ('airing', 'upcoming')
                     AND a.broadcast_day IS NOT NULL
                     AND NOT EXISTS (SELECT 1 FROM episode e JOIN episode_airing x ON x.episode_id = e.id
                                     WHERE e.anime_id = a.id AND x.source = 'anilist')"""
            ).fetchall()
            projected = 0
            for row in candidates:
                series = project_broadcast(
                    row["start_date"], row["broadcast_day"], row["broadcast_time"], row["broadcast_tz"],
                    row["total_episodes"], now,
                )
                if not series:
                    continue
                projected += 1
                sync_episodes(self._conn, row["id"], row["total_episodes"], at_least=len(series))
                self._conn.execute(
                    """DELETE FROM episode_airing WHERE source = 'mal_broadcast'
                       AND episode_id IN (SELECT id FROM episode WHERE anime_id = ?)""",
                    (row["id"],),
                )
                for number, airs_at in series:
                    self._upsert_airing(
                        self._episode_id(row["id"], number), AiringSource.MAL_BROADCAST, airs_at, Precision.ESTIMATED, now
                    )
        return projected

    # User-entered schedules

    def set_user_airings(self, anime_id: int, airings: Iterable[tuple[int, int]]) -> None:
        """Set (episode number, airs_at) overrides; they win over every source."""
        airings = list(airings)
        if not airings:
            return
        now = self._clock()
        with transaction(self._conn):
            total = self._conn.execute(
                "SELECT total_episodes FROM anime_effective WHERE id = ?", (anime_id,)
            ).fetchone()[0]
            sync_episodes(self._conn, anime_id, total, at_least=max(n for n, _ in airings))
            for number, airs_at in airings:
                self._upsert_airing(self._episode_id(anime_id, number), AiringSource.USER, airs_at, Precision.EXACT, now)

    def clear_user_airings(self, anime_id: int) -> int:
        with transaction(self._conn):
            return self._conn.execute(
                """DELETE FROM episode_airing WHERE source = 'user'
                   AND episode_id IN (SELECT id FROM episode WHERE anime_id = ?)""",
                (anime_id,),
            ).rowcount

    def has_user_airings(self, anime_id: int) -> bool:
        return bool(
            self._conn.execute(
                """SELECT 1 FROM episode_airing x JOIN episode e ON e.id = x.episode_id
                   WHERE e.anime_id = ? AND x.source = 'user' LIMIT 1""",
                (anime_id,),
            ).fetchone()
        )

    # Queries

    def episode_airings(self, anime_id: int) -> dict[int, EpisodeAiring]:
        rows = self._conn.execute(f"SELECT {_ENTRY_COLUMNS} {_ENTRY_JOINS} WHERE e.anime_id = ?", (anime_id,))
        return {
            r["episode_id"]: EpisodeAiring(
                r["airs_at"], Precision(r["precision"]), AiringSource(r["source"]), bool(r["delayed"])
            )
            for r in rows
        }

    def calendar_entries(self, start: int, end: int) -> list[CalendarEntry]:
        rows = self._conn.execute(
            f"""SELECT {_ENTRY_COLUMNS} {_ENTRY_JOINS}
                WHERE x.airs_at >= ? AND x.airs_at < ? AND a.in_library = 1
                ORDER BY x.airs_at, a.title, e.number""",
            (start, end),
        )
        return [_entry(r) for r in rows]

    def aired_unwatched(self, now: int) -> list[CalendarEntry]:
        rows = self._conn.execute(
            f"""SELECT {_ENTRY_COLUMNS} {_ENTRY_JOINS}
                WHERE x.airs_at <= ? AND w.state = 'unwatched' AND a.in_library = 1
                ORDER BY x.airs_at DESC, a.title, e.number""",
            (now,),
        )
        return [_entry(r) for r in rows]

    def undated_shows(self, now: int) -> list[UndatedShow]:
        """Airing or upcoming shows with no known future airing time."""
        rows = self._conn.execute(
            """SELECT a.id, a.title, a.title_en, a.cover_path FROM anime_effective a
               WHERE a.in_library = 1 AND a.airing_status IN ('airing', 'upcoming')
                 AND NOT EXISTS (SELECT 1 FROM episode e JOIN episode_effective_airing x ON x.episode_id = e.id
                                 WHERE e.anime_id = a.id AND x.airs_at > ?)
               ORDER BY a.title""",
            (now,),
        )
        return [UndatedShow(r["id"], r["title"], r["cover_path"], r["title_en"]) for r in rows]

    # Internals

    def _anime_for_mal(self, mal_id: int) -> int | None:
        row = self._conn.execute(
            "SELECT anime_id FROM external_id WHERE provider = 'mal' AND ext_id = ?", (str(mal_id),)
        ).fetchone()
        return row[0] if row else None

    def _link_anilist(self, anime_id: int, anilist_id: int) -> bool:
        owner = self._conn.execute(
            "SELECT anime_id FROM external_id WHERE provider = 'anilist' AND ext_id = ?", (str(anilist_id),)
        ).fetchone()
        if owner and owner[0] != anime_id:
            return False  # two library entries claim the same show; leave it alone
        self._conn.execute(
            """INSERT INTO external_id (anime_id, provider, ext_id) VALUES (?, 'anilist', ?)
               ON CONFLICT (anime_id, provider) DO UPDATE SET ext_id = excluded.ext_id""",
            (anime_id, str(anilist_id)),
        )
        return True

    def _episode_id(self, anime_id: int, number: int) -> int:
        return self._conn.execute(
            "SELECT id FROM episode WHERE anime_id = ? AND kind = 'regular' AND number = ?", (anime_id, number)
        ).fetchone()[0]

    def _record_change(self, episode_id: int, source: AiringSource, airs_at: int, now: int) -> int:
        old = self._conn.execute(
            "SELECT airs_at FROM episode_airing WHERE episode_id = ? AND source = ?", (episode_id, source.value)
        ).fetchone()
        if old is None or old[0] is None or abs(old[0] - airs_at) <= CHANGE_THRESHOLD_S:
            return 0
        self._conn.execute(
            """INSERT INTO airing_change (episode_id, source, old_airs_at, new_airs_at, detected_at)
               VALUES (?, ?, ?, ?, ?)""",
            (episode_id, source.value, old[0], airs_at, now),
        )
        return 1

    def _upsert_airing(
        self, episode_id: int, source: AiringSource, airs_at: int, precision: Precision, now: int
    ) -> None:
        self._conn.execute(
            """INSERT INTO episode_airing (episode_id, source, airs_at, precision, fetched_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (episode_id, source) DO UPDATE SET
                   airs_at = excluded.airs_at, precision = excluded.precision, fetched_at = excluded.fetched_at""",
            (episode_id, source.value, airs_at, precision.value, now),
        )

"""Library persistence. The UI talks to this class, never to SQL directly."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable, Iterable

from anical.db.database import transaction
from anical.domain.models import (
    OVERRIDABLE_FIELDS,
    AiringStatus,
    Anime,
    AnimeDraft,
    AnimeSummary,
    Episode,
    EpisodeKind,
    EpisodeList,
    MediaType,
    WatchSource,
    WatchState,
)

Clock = Callable[[], int]

_UNIT_SEP = "\x1f"


class DuplicateExternalIdError(Exception):
    def __init__(self, provider: str, ext_id: str, anime_id: int, title: str):
        super().__init__(f"{provider} ID {ext_id} already belongs to “{title}”")
        self.provider = provider
        self.ext_id = ext_id
        self.anime_id = anime_id
        self.title = title


def system_clock() -> int:
    return int(time.time())


def sync_episodes(conn: sqlite3.Connection, anime_id: int, total: int | None, at_least: int = 0) -> None:
    """Ensure regular episodes 1..max(total, at_least) exist.

    When the total shrinks, surplus episodes are removed only if nothing
    refers to them (no watch history, no airing data); user data is never
    discarded. An unknown total only ever adds episodes.
    """
    upper = max(total or 0, at_least)
    conn.executemany(
        "INSERT OR IGNORE INTO episode (anime_id, number, kind) VALUES (?, ?, 'regular')",
        [(anime_id, n) for n in range(1, upper + 1)],
    )
    if total is None:
        return
    conn.execute(
        """DELETE FROM episode
           WHERE anime_id = ? AND kind = 'regular' AND number > ?
             AND NOT EXISTS (SELECT 1 FROM watch_event w WHERE w.episode_id = episode.id)
             AND NOT EXISTS (SELECT 1 FROM episode_airing x WHERE x.episode_id = episode.id)""",
        (anime_id, upper),
    )


def _draft_value(draft: AnimeDraft, field: str):
    value = getattr(draft, field)
    if field == "synonyms":
        return list(value)
    if isinstance(value, (MediaType, AiringStatus)):
        return value.value
    return value


def _row_value(row: sqlite3.Row, field: str):
    if field == "synonyms":
        return json.loads(row["synonyms_json"])
    return row[field]


class LibraryRepository:
    def __init__(self, conn: sqlite3.Connection, clock: Clock = system_clock):
        self._conn = conn
        self._clock = clock

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    # Anime

    def create_anime(self, draft: AnimeDraft) -> int:
        """Add a manual entry. Works without any external ID."""
        draft = draft.validated()
        now = self._clock()
        with transaction(self._conn):
            cursor = self._conn.execute(
                """INSERT INTO anime (title, title_en, title_ja, synonyms_json, synopsis, media_type,
                                      airing_status, total_episodes, start_date, end_date, notes,
                                      added_manually, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                (*self._draft_columns(draft), draft.notes, now, now),
            )
            anime_id = cursor.lastrowid
            self._write_related(anime_id, draft)
        return anime_id

    def update_anime(self, anime_id: int, draft: AnimeDraft) -> None:
        """Save user edits.

        For user-entered shows the row itself is edited. For imported shows,
        values that differ from the source become overrides (so the next sync
        can't undo them) and values equal to the source drop their override.
        """
        draft = draft.validated()
        now = self._clock()
        with transaction(self._conn):
            row = self._conn.execute("SELECT * FROM anime WHERE id = ?", (anime_id,)).fetchone()
            if row is None:
                raise KeyError(anime_id)
            if row["metadata_source"] is None:
                self._conn.execute(
                    """UPDATE anime SET title = ?, title_en = ?, title_ja = ?, synonyms_json = ?, synopsis = ?,
                                        media_type = ?, airing_status = ?, total_episodes = ?, start_date = ?,
                                        end_date = ?
                       WHERE id = ?""",
                    (*self._draft_columns(draft), anime_id),
                )
            else:
                for field in OVERRIDABLE_FIELDS:
                    value = _draft_value(draft, field)
                    if value == _row_value(row, field):
                        self._conn.execute(
                            "DELETE FROM field_override WHERE anime_id = ? AND field = ?", (anime_id, field)
                        )
                    else:
                        self._conn.execute(
                            """INSERT INTO field_override (anime_id, field, value_json, set_at) VALUES (?, ?, ?, ?)
                               ON CONFLICT (anime_id, field) DO UPDATE
                               SET value_json = excluded.value_json, set_at = excluded.set_at
                               WHERE value_json IS NOT excluded.value_json""",
                            (anime_id, field, json.dumps(value, ensure_ascii=False), now),
                        )
            self._conn.execute("UPDATE anime SET notes = ?, updated_at = ? WHERE id = ?", (draft.notes, now, anime_id))
            self._write_related(anime_id, draft)

    def revert_overrides(self, anime_id: int) -> None:
        """Drop all user overrides so the source's values show again."""
        with transaction(self._conn):
            self._conn.execute("DELETE FROM field_override WHERE anime_id = ?", (anime_id,))
            total = self._conn.execute(
                "SELECT total_episodes FROM anime_effective WHERE id = ?", (anime_id,)
            ).fetchone()[0]
            sync_episodes(self._conn, anime_id, total)
            self._touch(anime_id)

    def delete_anime(self, anime_id: int) -> None:
        with transaction(self._conn):
            self._conn.execute("DELETE FROM anime WHERE id = ?", (anime_id,))

    def get_anime(self, anime_id: int) -> Anime | None:
        row = self._conn.execute(
            """SELECT a.*, m.list_status AS mal_list_status, m.episodes_watched AS mal_episodes_watched,
                      (SELECT ext_id FROM external_id x WHERE x.anime_id = a.id AND x.provider = 'mal') AS mal_id
               FROM anime_effective a LEFT JOIN mal_list_entry m ON m.anime_id = a.id
               WHERE a.id = ?""",
            (anime_id,),
        ).fetchone()
        if row is None:
            return None
        tags = tuple(
            r["tag"] for r in self._conn.execute("SELECT tag FROM tag WHERE anime_id = ? ORDER BY tag", (anime_id,))
        )
        overridden = frozenset(
            r["field"] for r in self._conn.execute("SELECT field FROM field_override WHERE anime_id = ?", (anime_id,))
        )
        return Anime(
            id=row["id"],
            title=row["title"],
            title_en=row["title_en"],
            title_ja=row["title_ja"],
            synonyms=tuple(json.loads(row["synonyms_json"])),
            synopsis=row["synopsis"],
            media_type=MediaType(row["media_type"]),
            airing_status=AiringStatus(row["airing_status"]),
            total_episodes=row["total_episodes"],
            start_date=row["start_date"],
            end_date=row["end_date"],
            notes=row["notes"],
            tags=tags,
            mal_id=int(row["mal_id"]) if row["mal_id"] else None,
            added_manually=bool(row["added_manually"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metadata_source=row["metadata_source"],
            overridden=overridden,
            cover_path=row["cover_path"],
            mal_list_status=row["mal_list_status"],
            mal_episodes_watched=row["mal_episodes_watched"],
        )

    def list_summaries(self) -> list[AnimeSummary]:
        rows = self._conn.execute(
            """SELECT a.id, a.title, a.title_en, a.title_ja, a.synonyms_json, a.media_type, a.airing_status,
                      a.total_episodes, a.created_at, a.updated_at, a.cover_path, m.list_status AS mal_list_status,
                      (SELECT COUNT(*) FROM episode e
                        WHERE e.anime_id = a.id AND e.kind = 'regular') AS episode_count,
                      (SELECT COUNT(*) FROM episode e JOIN episode_watch_state s ON s.episode_id = e.id
                        WHERE e.anime_id = a.id AND e.kind = 'regular' AND s.state = 'watched') AS watched_count,
                      (SELECT group_concat(t.tag, char(31)) FROM tag t WHERE t.anime_id = a.id) AS tags
               FROM anime_effective a LEFT JOIN mal_list_entry m ON m.anime_id = a.id
               WHERE a.in_library = 1"""
        )
        return [
            AnimeSummary(
                id=r["id"],
                title=r["title"],
                alt_titles=tuple(t for t in (r["title_en"], r["title_ja"], *json.loads(r["synonyms_json"])) if t),
                tags=tuple(r["tags"].split(_UNIT_SEP)) if r["tags"] else (),
                media_type=MediaType(r["media_type"]),
                airing_status=AiringStatus(r["airing_status"]),
                total_episodes=r["total_episodes"],
                episode_count=r["episode_count"],
                watched_count=r["watched_count"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
                cover_path=r["cover_path"],
                mal_list_status=r["mal_list_status"],
            )
            for r in rows
        ]

    # Covers

    def covers_to_fetch(self) -> list[tuple[int, str]]:
        """(anime_id, url) for shows with a cover URL but no stored image."""
        rows = self._conn.execute(
            "SELECT id, cover_url FROM anime WHERE cover_url IS NOT NULL AND cover_path IS NULL"
        )
        return [(r["id"], r["cover_url"]) for r in rows]

    def set_cover_paths(self, paths: Iterable[tuple[int, str]]) -> None:
        with transaction(self._conn):
            self._conn.executemany("UPDATE anime SET cover_path = ? WHERE id = ?", [(p, i) for i, p in paths])

    # Episodes and watched status

    def episodes(self, anime_id: int) -> EpisodeList:
        rows = self._conn.execute(
            """SELECT e.id, e.anime_id, e.number, e.kind, e.title, s.state
               FROM episode e JOIN episode_watch_state s ON s.episode_id = e.id
               WHERE e.anime_id = ?
               ORDER BY CASE e.kind WHEN 'regular' THEN 0 WHEN 'special' THEN 1 ELSE 2 END, e.number""",
            (anime_id,),
        )
        return EpisodeList(
            [
                Episode(r["id"], r["anime_id"], r["number"], EpisodeKind(r["kind"]), r["title"], WatchState(r["state"]))
                for r in rows
            ]
        )

    def episode(self, episode_id: int) -> Episode:
        row = self._conn.execute(
            """SELECT e.id, e.anime_id, e.number, e.kind, e.title, s.state
               FROM episode e JOIN episode_watch_state s ON s.episode_id = e.id WHERE e.id = ?""",
            (episode_id,),
        ).fetchone()
        if row is None:
            raise KeyError(episode_id)
        return Episode(row["id"], row["anime_id"], row["number"], EpisodeKind(row["kind"]), row["title"], WatchState(row["state"]))

    def watch_state(self, episode_id: int) -> WatchState:
        row = self._conn.execute("SELECT state FROM episode_watch_state WHERE episode_id = ?", (episode_id,)).fetchone()
        if row is None:
            raise KeyError(episode_id)
        return WatchState(row[0])

    def add_episode(self, anime_id: int, kind: EpisodeKind = EpisodeKind.REGULAR) -> int:
        """Append the next episode number of `kind`, for shows whose length is unknown."""
        with transaction(self._conn):
            (number,) = self._conn.execute(
                "SELECT COALESCE(MAX(number), 0) + 1 FROM episode WHERE anime_id = ? AND kind = ?",
                (anime_id, kind.value),
            ).fetchone()
            cursor = self._conn.execute(
                "INSERT INTO episode (anime_id, number, kind) VALUES (?, ?, ?)", (anime_id, number, kind.value)
            )
            self._touch(anime_id)
        return cursor.lastrowid

    def set_watch_state(
        self, episode_ids: Iterable[int], state: WatchState, source: WatchSource = WatchSource.USER
    ) -> int:
        """Record a watched/unwatched event for each episode whose effective state differs.

        Returns the number of episodes that changed.
        """
        ids = list(episode_ids)
        if not ids:
            return 0
        now = self._clock()
        placeholders = ",".join("?" * len(ids))
        with transaction(self._conn):
            rows = self._conn.execute(
                f"""SELECT s.episode_id, s.anime_id FROM episode_watch_state s
                    WHERE s.episode_id IN ({placeholders}) AND s.state != ?""",
                (*ids, state.value),
            ).fetchall()
            self._conn.executemany(
                "INSERT INTO watch_event (episode_id, state, source, occurred_at) VALUES (?, ?, ?, ?)",
                [(r["episode_id"], state.value, source.value, now) for r in rows],
            )
            for anime_id in {r["anime_id"] for r in rows}:
                self._touch(anime_id)
        return len(rows)

    # Internals

    @staticmethod
    def _draft_columns(draft: AnimeDraft) -> tuple:
        return (
            draft.title,
            draft.title_en,
            draft.title_ja,
            json.dumps(list(draft.synonyms), ensure_ascii=False),
            draft.synopsis,
            draft.media_type.value,
            draft.airing_status.value,
            draft.total_episodes,
            draft.start_date,
            draft.end_date,
        )

    def _write_related(self, anime_id: int, draft: AnimeDraft) -> None:
        self._conn.execute("DELETE FROM tag WHERE anime_id = ?", (anime_id,))
        self._conn.executemany("INSERT INTO tag (anime_id, tag) VALUES (?, ?)", [(anime_id, t) for t in draft.tags])
        self._set_external_id(anime_id, "mal", None if draft.mal_id is None else str(draft.mal_id))
        total = self._conn.execute("SELECT total_episodes FROM anime_effective WHERE id = ?", (anime_id,)).fetchone()[0]
        sync_episodes(self._conn, anime_id, total)

    def _set_external_id(self, anime_id: int, provider: str, ext_id: str | None) -> None:
        if ext_id is not None:
            owner = self._conn.execute(
                """SELECT a.id, a.title FROM external_id x JOIN anime_effective a ON a.id = x.anime_id
                   WHERE x.provider = ? AND x.ext_id = ? AND x.anime_id != ?""",
                (provider, ext_id, anime_id),
            ).fetchone()
            if owner:
                raise DuplicateExternalIdError(provider, ext_id, owner["id"], owner["title"])
        self._conn.execute("DELETE FROM external_id WHERE anime_id = ? AND provider = ?", (anime_id, provider))
        if ext_id is not None:
            self._conn.execute(
                "INSERT INTO external_id (anime_id, provider, ext_id) VALUES (?, ?, ?)", (anime_id, provider, ext_id)
            )

    def _touch(self, anime_id: int) -> None:
        self._conn.execute("UPDATE anime SET updated_at = ? WHERE id = ?", (self._clock(), anime_id))

"""Apply a fetched MyAnimeList list to the local library.

Rules:
- Shows are matched by MAL ID only, never by title.
- MAL values replace the base row; user overrides (field_override) still win.
- Notes, tags and local watch history are never touched.
- MAL progress adds `mal_import` watched events for episodes 1..N. It never
  un-watches anything, and local user/playback events always take precedence.
- Shows that left the MAL list stay in the library, marked 'removed'.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from anical.db.database import transaction
from anical.db.repository import sync_episodes
from anical.providers.mal import BROADCAST_TZ, MalListEntry, MalListResult


@dataclass(frozen=True)
class ImportSummary:
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0
    progress_episodes: int = 0
    skipped: int = 0


_SOURCE_COLUMNS = (
    "title",
    "title_en",
    "title_ja",
    "synonyms_json",
    "synopsis",
    "media_type",
    "airing_status",
    "total_episodes",
    "start_date",
    "end_date",
    "broadcast_day",
    "broadcast_time",
    "broadcast_tz",
    "episode_duration_s",
    "cover_url",
)


def _source_values(entry: MalListEntry) -> tuple:
    a = entry.anime
    return (
        a.title,
        a.title_en,
        a.title_ja,
        json.dumps(list(a.synonyms), ensure_ascii=False),
        a.synopsis,
        a.media_type.value,
        a.airing_status.value,
        a.total_episodes,
        a.start_date,
        a.end_date,
        a.broadcast_day,
        a.broadcast_time,
        BROADCAST_TZ if a.broadcast_day is not None else None,
        a.episode_duration_s,
        a.picture_url,
    )


def apply_mal_list(conn: sqlite3.Connection, result: MalListResult, now: int) -> ImportSummary:
    added = updated = unchanged = progress = 0
    seen: list[int] = []
    columns = ", ".join(_SOURCE_COLUMNS)
    assignments = ", ".join(f"{c} = ?" for c in _SOURCE_COLUMNS)

    with transaction(conn):
        batch_id = conn.execute("INSERT INTO import_batch (kind, started_at) VALUES ('mal_list', ?)", (now,)).lastrowid
        for entry in result.entries:
            values = _source_values(entry)
            existing = conn.execute(
                f"""SELECT a.id, a.metadata_source, a.in_library, {", ".join("a." + c for c in _SOURCE_COLUMNS)}
                    FROM external_id x JOIN anime a ON a.id = x.anime_id
                    WHERE x.provider = 'mal' AND x.ext_id = ?""",  # noqa: S608 - constant column lists and ? placeholders only
                (str(entry.anime.mal_id),),
            ).fetchone()
            if existing is None:
                anime_id = conn.execute(
                    f"""INSERT INTO anime ({columns}, metadata_source, added_manually, created_at, updated_at)
                        VALUES ({", ".join("?" * len(_SOURCE_COLUMNS))}, 'mal', 0, ?, ?)""",  # noqa: S608 - constant column lists and ? placeholders only
                    (*values, now, now),
                ).lastrowid
                conn.execute(
                    "INSERT INTO external_id (anime_id, provider, ext_id) VALUES (?, 'mal', ?)",
                    (anime_id, str(entry.anime.mal_id)),
                )
                added += 1
            else:
                anime_id = existing["id"]
                old = tuple(existing[c] for c in _SOURCE_COLUMNS)
                if old != values or existing["metadata_source"] != "mal" or not existing["in_library"]:
                    if old[-1] != values[-1]:
                        conn.execute("UPDATE anime SET cover_path = NULL WHERE id = ?", (anime_id,))
                    conn.execute(
                        f"""UPDATE anime SET {assignments}, metadata_source = 'mal', in_library = 1, updated_at = ?
                            WHERE id = ?""",  # noqa: S608 - constant column lists and ? placeholders only
                        (*values, now, anime_id),
                    )
                    updated += 1
                else:
                    unchanged += 1
            seen.append(anime_id)

            conn.execute(
                """INSERT INTO mal_list_entry (anime_id, list_status, episodes_watched, is_rewatching,
                                               mal_updated_at, imported_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT (anime_id) DO UPDATE SET
                       list_status = excluded.list_status, episodes_watched = excluded.episodes_watched,
                       is_rewatching = excluded.is_rewatching, mal_updated_at = excluded.mal_updated_at,
                       imported_at = excluded.imported_at""",
                (anime_id, entry.list_status, entry.episodes_watched, int(entry.is_rewatching), entry.updated_at, now),
            )
            total = conn.execute("SELECT total_episodes FROM anime_effective WHERE id = ?", (anime_id,)).fetchone()[0]
            sync_episodes(conn, anime_id, total, at_least=entry.episodes_watched)
            progress += conn.execute(
                """INSERT INTO watch_event (episode_id, state, source, batch_id, occurred_at)
                   SELECT e.id, 'watched', 'mal_import', ?, ? FROM episode e
                   WHERE e.anime_id = ? AND e.kind = 'regular' AND e.number <= ?
                     AND NOT EXISTS (SELECT 1 FROM watch_event w
                                     WHERE w.episode_id = e.id AND w.source = 'mal_import' AND w.state = 'watched')""",
                (batch_id, now, anime_id, entry.episodes_watched),
            ).rowcount

        placeholders = ",".join("?" * len(seen))
        removed = conn.execute(
            f"""UPDATE mal_list_entry SET list_status = 'removed', imported_at = ?
                WHERE list_status != 'removed' AND anime_id NOT IN ({placeholders})""",  # noqa: S608 - constant column lists and ? placeholders only
            (now, *seen),
        ).rowcount

        summary = ImportSummary(added, updated, unchanged, removed, progress, result.skipped)
        conn.execute("UPDATE import_batch SET summary_json = ? WHERE id = ?", (json.dumps(summary.__dict__), batch_id))
    return summary

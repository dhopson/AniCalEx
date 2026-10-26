"""Show-to-ani-cli mappings and playback session records."""

from __future__ import annotations

import sqlite3

from anical.db.database import transaction
from anical.db.repository import Clock, system_clock
from anical.domain.playback import MODES, PlaybackMapping


class PlaybackRepository:
    def __init__(self, conn: sqlite3.Connection, clock: Clock = system_clock):
        self._conn = conn
        self._clock = clock

    def mapping(self, anime_id: int) -> PlaybackMapping | None:
        row = self._conn.execute(
            "SELECT query, result_title, episode_offset, mode FROM playback_mapping WHERE anime_id = ?", (anime_id,)
        ).fetchone()
        if row is None:
            return None
        return PlaybackMapping(anime_id, row["query"], row["result_title"], row["episode_offset"], row["mode"])

    def save_mapping(self, mapping: PlaybackMapping) -> None:
        if not mapping.query.strip():
            raise ValueError("search query is empty")
        if mapping.mode is not None and mapping.mode not in MODES:
            raise ValueError(f"unknown mode {mapping.mode!r}")
        with transaction(self._conn):
            self._conn.execute(
                """INSERT INTO playback_mapping (anime_id, query, result_title, episode_offset, mode, verified_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT (anime_id) DO UPDATE SET
                       query = excluded.query, result_title = excluded.result_title,
                       episode_offset = excluded.episode_offset, mode = excluded.mode,
                       verified_at = excluded.verified_at""",
                (
                    mapping.anime_id, mapping.query.strip(), mapping.result_title, mapping.episode_offset,
                    mapping.mode, self._clock(),
                ),
            )

    def delete_mapping(self, anime_id: int) -> None:
        with transaction(self._conn):
            self._conn.execute("DELETE FROM playback_mapping WHERE anime_id = ?", (anime_id,))

    def start_session(self, episode_id: int) -> int:
        with transaction(self._conn):
            return self._conn.execute(
                "INSERT INTO playback_session (episode_id, started_at) VALUES (?, ?)", (episode_id, self._clock())
            ).lastrowid

    def finish_session(
        self, session_id: int, exit_code: int | None, end_reason: str, percent: float | None, error: str | None
    ) -> None:
        with transaction(self._conn):
            self._conn.execute(
                """UPDATE playback_session SET ended_at = ?, exit_code = ?, end_reason = ?, percent = ?, error = ?
                   WHERE id = ?""",
                (self._clock(), exit_code, end_reason, percent, error, session_id),
            )

    def close_interrupted(self) -> int:
        """Sessions left open by a crash or by quitting AniCal mid-playback."""
        with transaction(self._conn):
            return self._conn.execute(
                "UPDATE playback_session SET ended_at = ?, end_reason = 'interrupted' WHERE ended_at IS NULL",
                (self._clock(),),
            ).rowcount

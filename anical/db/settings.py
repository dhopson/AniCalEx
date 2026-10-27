"""User preferences (`settings`) and synchronization bookkeeping (`sync_state`)."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from anical.db.database import transaction
from anical.db.repository import Clock, system_clock

# Settings keys
MAL_CLIENT_ID = "mal.client_id"
MAL_MODE = "mal.mode"  # "account" (OAuth login) or "username" (public list, client ID only)
MAL_USERNAME = "mal.username"
DISPLAY_TIMEZONE = "display.timezone"  # IANA name; unset = system time zone
DISPLAY_WEEK_START = "display.week_start"  # 0 = Monday, 6 = Sunday
AVAILABILITY_LAG_HOURS = "schedule.availability_lag_hours"
DISPLAY_ENGLISH_TITLES = "display.english_titles"  # calendar shows English titles when known
PLAYBACK_EXECUTABLE = "playback.executable"
PLAYBACK_MODE = "playback.mode"  # "sub" or "dub"
PLAYBACK_QUALITY = "playback.quality"
PLAYBACK_AUTO_MARK = "playback.auto_mark"  # mark watched at end/90% without asking
PLAYBACK_BACKEND = "playback.backend"  # "ani-cli" (default) or "external" (the link or command below)
PLAYBACK_EXTERNAL = "playback.external"  # link or command template with {title} {query} {episode} {mal_id}
PLAYBACK_PLAYER = "playback.player"  # the real mpv anical-mpv starts; unset = found automatically
PLAYBACK_BASH = "playback.bash"  # Windows: Git Bash's bash.exe; unset = found automatically
NOTIFICATIONS_ENABLED = "notifications.enabled"
SETUP_SEEN = "setup.seen"  # the first-run wizard was finished or closed; it isn't shown again by itself

# Sync-state keys
LAST_REFRESH_RUN = "refresh.last_run"  # epoch seconds of the last successful refresh; no automatic refresh within 24h of it
LAST_REFRESH_ERROR = "refresh.last_error"


class SettingsRepository:
    def __init__(self, conn: sqlite3.Connection, clock: Clock = system_clock):
        self._conn = conn
        self._clock = clock

    def get(self, key: str, default: Any = None) -> Any:
        row = self._conn.execute("SELECT value_json FROM settings WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key: str, value: Any) -> None:
        with transaction(self._conn):
            if value is None:
                self._conn.execute("DELETE FROM settings WHERE key = ?", (key,))
            else:
                self._conn.execute(
                    """INSERT INTO settings (key, value_json) VALUES (?, ?)
                       ON CONFLICT (key) DO UPDATE SET value_json = excluded.value_json""",
                    (key, json.dumps(value)),
                )

    def get_state(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_state(self, key: str, value: str | None) -> None:
        with transaction(self._conn):
            self._conn.execute(
                """INSERT INTO sync_state (key, value, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
                (key, value, self._clock()),
            )

    def last_refresh_run(self) -> int | None:
        value = self.get_state(LAST_REFRESH_RUN)
        return int(value) if value and value.isdecimal() else None

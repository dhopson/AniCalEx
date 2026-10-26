"""Library backups: automatic weekly copies, plus manual backup and restore.

Backups are complete SQLite databases made with SQLite's online backup API,
so they are consistent even while AniCal is running.
"""

from __future__ import annotations

import contextlib
import datetime
import os
import sqlite3
from pathlib import Path

from anical.db.database import load_migrations, migrate

AUTO_PREFIX = "anical-auto-"
AUTO_INTERVAL_S = 7 * 24 * 60 * 60
AUTO_KEEP = 4


class BackupError(Exception):
    pass


def backup_to(conn: sqlite3.Connection, path: Path) -> None:
    """Write a complete copy of the library to `path` (replaced atomically)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    partial.unlink(missing_ok=True)
    with contextlib.closing(sqlite3.connect(partial)) as target:
        conn.backup(target)
    os.replace(partial, path)


def validate_backup(path: Path) -> int:
    """Check that `path` is an intact AniCal library this version can open. Returns its schema version."""
    supported = load_migrations()[-1].version
    try:
        with contextlib.closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as source:
            if source.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise BackupError("The backup file is damaged")
            version = source.execute("PRAGMA user_version").fetchone()[0]
            has_library = source.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'anime'"
            ).fetchone()
    except sqlite3.DatabaseError as e:
        raise BackupError(f"Not an AniCal backup ({e})") from e
    if not has_library or version < 1:
        raise BackupError("Not an AniCal backup")
    if version > supported:
        raise BackupError("The backup was made by a newer version of AniCal")
    return version


def restore_from(conn: sqlite3.Connection, path: Path, safety_copy: Path) -> None:
    """Replace the library with a backup, keeping a copy of the current library first.

    Older backups are upgraded to the current schema after restoring.
    """
    validate_backup(path)
    backup_to(conn, safety_copy)
    with contextlib.closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as source:
        source.backup(conn)
    migrate(conn)


def automatic_backup(conn: sqlite3.Connection, directory: Path, now: int, keep: int = AUTO_KEEP) -> Path | None:
    """Make a weekly rolling backup if the newest one is older than a week; keep the last `keep`."""
    existing = sorted(directory.glob(f"{AUTO_PREFIX}*.db")) if directory.exists() else []
    if existing and now - existing[-1].stat().st_mtime < AUTO_INTERVAL_S:
        return None
    stamp = datetime.datetime.fromtimestamp(now).strftime("%Y%m%d-%H%M%S")
    path = directory / f"{AUTO_PREFIX}{stamp}.db"
    backup_to(conn, path)
    for old in sorted(directory.glob(f"{AUTO_PREFIX}*.db"))[:-keep]:
        old.unlink()
    return path

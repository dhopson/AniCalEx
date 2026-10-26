"""SQLite connection handling, migrations and transactions.

Connections run in autocommit mode; all writes go through `transaction()`
so that a failure or interruption never leaves partial changes behind.
"""

from __future__ import annotations

import contextlib
import importlib.resources
import re
import sqlite3
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

_MIGRATION_NAME = re.compile(r"^(\d{3})_([a-z0-9_]+)\.sql$")


class DatabaseError(Exception):
    """Base class for errors opening or upgrading the database."""


class DatabaseCorruptError(DatabaseError):
    pass


class MigrationError(DatabaseError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


def load_migrations() -> list[Migration]:
    """Load the bundled migrations, ordered and checked to be contiguous."""
    directory = importlib.resources.files("anical.db") / "migrations"
    migrations = []
    for entry in directory.iterdir():
        match = _MIGRATION_NAME.match(entry.name)
        if match:
            migrations.append(Migration(int(match[1]), match[2], entry.read_text(encoding="utf-8")))
    migrations.sort(key=lambda m: m.version)
    for expected, migration in enumerate(migrations, start=1):
        if migration.version != expected:
            raise MigrationError(f"migration {expected:03d} is missing")
    return migrations


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def check_integrity(conn: sqlite3.Connection) -> None:
    try:
        result = conn.execute("PRAGMA quick_check").fetchone()[0]
    except sqlite3.DatabaseError as e:
        raise DatabaseCorruptError(str(e)) from e
    if result != "ok":
        raise DatabaseCorruptError(result)


def migrate(
    conn: sqlite3.Connection,
    migrations: Sequence[Migration] | None = None,
    backup_path: Path | None = None,
) -> int:
    """Apply pending migrations, each in its own transaction.

    If the database already holds data and `backup_path` is given, a copy is
    written there before anything changes. Returns the resulting version.
    """
    if migrations is None:
        migrations = load_migrations()
    current = schema_version(conn)
    latest = migrations[-1].version if migrations else 0
    if current > latest:
        raise MigrationError(
            f"the library was created by a newer version of AniCal (schema {current}, supported {latest})"
        )
    pending = [m for m in migrations if m.version > current]
    if not pending:
        return current

    if backup_path is not None and current > 0:
        with contextlib.closing(sqlite3.connect(backup_path)) as backup:
            conn.backup(backup)

    for migration in pending:
        try:
            conn.executescript(
                f"BEGIN IMMEDIATE;\n{migration.sql}\nPRAGMA user_version = {migration.version};\nCOMMIT;"
            )
        except sqlite3.Error as e:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise MigrationError(f"migration {migration.version:03d}_{migration.name} failed: {e}") from e
    return latest


@contextlib.contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def open_database(path: Path) -> sqlite3.Connection:
    """Open (creating if needed) and upgrade the library database at `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = connect(path)
    except sqlite3.DatabaseError as e:
        raise DatabaseCorruptError(str(e)) from e
    try:
        check_integrity(conn)
        backup = path.with_name(f"{path.name}.pre-migration-{int(time.time())}.bak")
        migrate(conn, backup_path=backup)
    except BaseException:
        conn.close()
        raise
    return conn

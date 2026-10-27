import sqlite3

import pytest

from anical.db.database import (
    DatabaseCorruptError,
    Migration,
    MigrationError,
    connect,
    load_migrations,
    migrate,
    open_database,
    schema_version,
    transaction,
)


def table_names(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")}


def test_bundled_migrations_are_contiguous():
    migrations = load_migrations()
    assert [m.version for m in migrations] == list(range(1, len(migrations) + 1))


def test_fresh_database_gets_full_schema():
    conn = connect(":memory:")
    assert migrate(conn) == load_migrations()[-1].version
    assert {"anime", "episode", "watch_event", "external_id", "sync_state", "episode_watch_state"} <= table_names(conn)
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_migrate_is_idempotent(conn):
    version = schema_version(conn)
    assert migrate(conn) == version


def test_failed_migration_rolls_back_completely():
    conn = connect(":memory:")
    migrations = [
        Migration(1, "ok", "CREATE TABLE a (x INTEGER);"),
        Migration(2, "broken", "CREATE TABLE b (x INTEGER); INSERT INTO missing VALUES (1);"),
    ]
    with pytest.raises(MigrationError, match="002_broken"):
        migrate(conn, migrations)
    assert schema_version(conn) == 1
    assert "a" in table_names(conn)
    assert "b" not in table_names(conn)
    assert not conn.in_transaction


def test_newer_database_is_refused():
    conn = connect(":memory:")
    conn.execute("PRAGMA user_version = 99")
    with pytest.raises(MigrationError, match="newer version"):
        migrate(conn)


def test_upgrade_backs_up_existing_data(tmp_path):
    db = tmp_path / "lib.db"
    backup = tmp_path / "lib.bak"
    v1 = [Migration(1, "one", "CREATE TABLE a (x INTEGER);")]
    conn = connect(db)
    migrate(conn, v1, backup_path=backup)
    assert not backup.exists()  # nothing to protect on a fresh database
    conn.execute("INSERT INTO a VALUES (42)")

    migrate(conn, [*v1, Migration(2, "two", "ALTER TABLE a ADD COLUMN y TEXT;")], backup_path=backup)
    saved = sqlite3.connect(backup)
    assert saved.execute("SELECT x FROM a").fetchall() == [(42,)]
    assert saved.execute("PRAGMA user_version").fetchone()[0] == 1


def test_open_database_creates_parent_directories(tmp_path):
    conn = open_database(tmp_path / "nested" / "anical.db")
    assert schema_version(conn) == load_migrations()[-1].version


def test_garbage_file_is_reported_as_corrupt(tmp_path):
    path = tmp_path / "anical.db"
    path.write_bytes(b"this is not a sqlite database" * 100)
    with pytest.raises(DatabaseCorruptError):
        open_database(path)


def test_transaction_rolls_back_on_error(conn):
    with pytest.raises(RuntimeError), transaction(conn):
        conn.execute("INSERT INTO settings (key, value_json) VALUES ('a', '1')")
        raise RuntimeError
    assert conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 0


def test_schedule_migration_forces_a_refresh():
    conn = connect(":memory:")
    migrations = load_migrations()
    migrate(conn, migrations[:2])
    conn.execute("INSERT INTO sync_state (key, value, updated_at) VALUES ('refresh.last_run', '1790000000', 0)")
    migrate(conn, migrations)
    assert conn.execute("SELECT COUNT(*) FROM sync_state WHERE key = 'refresh.last_run'").fetchone()[0] == 0

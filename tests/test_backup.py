import os
import sqlite3

import pytest

from anical.db.backup import AUTO_INTERVAL_S, BackupError, automatic_backup, backup_to, restore_from, validate_backup
from anical.db.database import Migration, connect, load_migrations, migrate, schema_version
from anical.db.repository import LibraryRepository
from anical.domain.models import AnimeDraft, WatchState

NOW = 1_793_040_000


def titles(conn):
    return [r[0] for r in conn.execute("SELECT title FROM anime ORDER BY title")]


@pytest.fixture
def live(tmp_path):
    conn = connect(tmp_path / "anical.db")  # a file, with WAL, like the real library
    migrate(conn)
    return conn


def test_backup_and_restore_round_trip(live, tmp_path):
    repo = LibraryRepository(live)
    anime_id = repo.create_anime(AnimeDraft(title="Kept", total_episodes=2))
    repo.set_watch_state([repo.episodes(anime_id).episodes[0].id], WatchState.WATCHED)
    backup = tmp_path / "backup.db"
    backup_to(live, backup)

    repo.create_anime(AnimeDraft(title="Added later"))
    repo.delete_anime(anime_id)
    restore_from(live, backup, tmp_path / "safety.db")

    assert titles(live) == ["Kept"]
    assert repo.episodes(anime_id).watched_count == 1
    assert titles(sqlite3.connect(tmp_path / "safety.db")) == ["Added later"]  # nothing is lost


def test_older_backups_are_upgraded(live, tmp_path):
    old = connect(tmp_path / "old.db")
    migrate(old, load_migrations()[:1])
    old.execute("INSERT INTO anime (title, created_at, updated_at) VALUES ('From v1', 0, 0)")
    old.close()
    restore_from(live, tmp_path / "old.db", tmp_path / "safety.db")
    assert titles(live) == ["From v1"]
    assert schema_version(live) == load_migrations()[-1].version


@pytest.mark.parametrize(
    ("make", "message"),
    [
        (lambda p: p.write_bytes(b"not sqlite" * 100), "Not an AniCal backup"),
        (lambda p: sqlite3.connect(p).execute("CREATE TABLE other (x)"), "Not an AniCal backup"),
    ],
)
def test_invalid_backups_are_refused_without_touching_the_library(live, tmp_path, make, message):
    LibraryRepository(live).create_anime(AnimeDraft(title="Precious"))
    bad = tmp_path / "bad.db"
    make(bad)
    with pytest.raises(BackupError, match=message):
        restore_from(live, bad, tmp_path / "safety.db")
    assert titles(live) == ["Precious"]
    assert not (tmp_path / "safety.db").exists()


def test_newer_backups_are_refused(tmp_path):
    newer = connect(tmp_path / "newer.db")
    migrate(newer, [*load_migrations(), Migration(99, "future", "SELECT 1;")])
    newer.close()
    with pytest.raises(BackupError, match="newer version"):
        validate_backup(tmp_path / "newer.db")


def test_automatic_backups_are_weekly_and_rolling(live, tmp_path):
    directory = tmp_path / "backups"
    made = []
    for week in range(6):
        now = NOW + week * AUTO_INTERVAL_S
        path = automatic_backup(live, directory, now)
        assert path is not None
        os.utime(path, (now, now))
        made.append(path)
        assert automatic_backup(live, directory, now + 3600) is None  # not again within the week
    assert sorted(directory.glob("*.db")) == made[-4:]

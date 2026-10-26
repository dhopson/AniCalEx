import sqlite3
from dataclasses import replace

import pytest

from anical.db.mal_import import apply_mal_list
from anical.domain.models import AiringStatus, AnimeDraft, WatchSource, WatchState
from anical.providers.mal import MalListResult


def by_mal_id(repo, mal_id):
    return next(repo.get_anime(s.id) for s in repo.list_summaries() if repo.get_anime(s.id).mal_id == mal_id)


def watched_numbers(repo, anime_id):
    return [e.number for e in repo.episodes(anime_id).episodes if e.state is WatchState.WATCHED]


def with_entry(result, mal_id, **changes):
    """Copy of `result` with one entry's anime/list fields changed."""
    entries = []
    for entry in result.entries:
        if entry.anime.mal_id == mal_id:
            anime_changes = {k: v for k, v in changes.items() if hasattr(entry.anime, k)}
            list_changes = {k: v for k, v in changes.items() if k not in anime_changes}
            entry = replace(entry, anime=replace(entry.anime, **anime_changes), **list_changes)
        entries.append(entry)
    return MalListResult(tuple(entries), result.skipped)


def test_first_import(repo, conn, clock, mal_list_result):
    summary = apply_mal_list(conn, mal_list_result, clock())
    assert (summary.added, summary.updated, summary.progress_episodes, summary.skipped) == (3, 0, 31, 1)

    frieren = by_mal_id(repo, 52991)
    assert frieren.metadata_source == "mal" and not frieren.added_manually
    assert frieren.mal_list_status == "completed" and frieren.mal_episodes_watched == 28
    assert repo.episodes(frieren.id).watched_count == 28

    ongoing = by_mal_id(repo, 99001)
    assert ongoing.total_episodes is None
    assert watched_numbers(repo, ongoing.id) == [1, 2, 3]  # episodes created up to MAL progress


def test_reimport_is_idempotent(repo, conn, clock, mal_list_result):
    apply_mal_list(conn, mal_list_result, clock())
    clock.advance(86400)
    summary = apply_mal_list(conn, mal_list_result, clock())
    assert (summary.added, summary.updated, summary.unchanged, summary.progress_episodes) == (0, 0, 3, 0)
    assert conn.execute("SELECT COUNT(*) FROM anime").fetchone()[0] == 3


def test_source_changes_update_the_show(repo, conn, clock, mal_list_result):
    apply_mal_list(conn, mal_list_result, clock())
    changed = with_entry(mal_list_result, 99001, total_episodes=12, airing_status=AiringStatus.COMPLETED)
    assert apply_mal_list(conn, changed, clock()).updated == 1
    ongoing = by_mal_id(repo, 99001)
    assert ongoing.total_episodes == 12
    assert repo.episodes(ongoing.id).regular_count == 12


def test_user_overrides_survive_reimport(repo, conn, clock, mal_list_result):
    apply_mal_list(conn, mal_list_result, clock())
    frieren = by_mal_id(repo, 52991)
    repo.update_anime(frieren.id, replace(frieren, title="Frieren (my title)", notes="great", tags=("fav",)))
    assert repo.get_anime(frieren.id).overridden == {"title"}

    apply_mal_list(conn, with_entry(mal_list_result, 52991, title="Sousou no Frieren (renamed)"), clock())
    after = repo.get_anime(frieren.id)
    assert after.title == "Frieren (my title)"
    assert (after.notes, after.tags) == ("great", ("fav",))

    repo.revert_overrides(frieren.id)
    assert repo.get_anime(frieren.id).title == "Sousou no Frieren (renamed)"


def test_editing_back_to_the_source_value_drops_the_override(repo, conn, clock, mal_list_result):
    apply_mal_list(conn, mal_list_result, clock())
    frieren = by_mal_id(repo, 52991)
    repo.update_anime(frieren.id, replace(frieren, total_episodes=None))
    assert repo.get_anime(frieren.id).total_episodes is None  # explicit "unknown" override
    repo.update_anime(frieren.id, replace(frieren, total_episodes=28))
    assert repo.get_anime(frieren.id).overridden == frozenset()


def test_local_watch_state_beats_mal_progress(repo, conn, clock, mal_list_result):
    apply_mal_list(conn, mal_list_result, clock())
    ongoing = by_mal_id(repo, 99001)
    first = repo.episodes(ongoing.id).episodes[0]
    clock.advance()
    repo.set_watch_state([first.id], WatchState.UNWATCHED)
    clock.advance()
    apply_mal_list(conn, with_entry(mal_list_result, 99001, episodes_watched=4), clock())
    assert watched_numbers(repo, ongoing.id) == [2, 3, 4]


def test_lower_mal_progress_never_unwatches(repo, conn, clock, mal_list_result):
    apply_mal_list(conn, mal_list_result, clock())
    apply_mal_list(conn, with_entry(mal_list_result, 99001, episodes_watched=1), clock())
    ongoing = by_mal_id(repo, 99001)
    assert watched_numbers(repo, ongoing.id) == [1, 2, 3]
    assert ongoing.mal_episodes_watched == 1  # the discrepancy stays visible


def test_shows_removed_from_mal_stay_in_library(repo, conn, clock, mal_list_result):
    apply_mal_list(conn, mal_list_result, clock())
    shorter = MalListResult(tuple(e for e in mal_list_result.entries if e.anime.mal_id != 99002))
    assert apply_mal_list(conn, shorter, clock()).removed == 1
    special = by_mal_id(repo, 99002)
    assert special.mal_list_status == "removed"
    assert len(repo.list_summaries()) == 3


def test_manual_entry_with_mal_id_is_merged_not_duplicated(repo, conn, clock, mal_list_result):
    manual = repo.create_anime(AnimeDraft(title="frieren typed by me", mal_id=52991, total_episodes=2, notes="mine"))
    repo.set_watch_state([repo.episodes(manual).episodes[0].id], WatchState.WATCHED)
    summary = apply_mal_list(conn, mal_list_result, clock())
    assert summary.added == 2 and summary.updated == 1
    merged = repo.get_anime(manual)
    assert merged.title == "Sousou no Frieren"
    assert merged.added_manually and merged.metadata_source == "mal"
    assert merged.notes == "mine"
    sources = {r[0] for r in conn.execute("SELECT DISTINCT source FROM watch_event")}
    assert sources == {WatchSource.USER.value, WatchSource.MAL_IMPORT.value}


def test_cover_change_clears_cached_path(repo, conn, clock, mal_list_result):
    apply_mal_list(conn, mal_list_result, clock())
    frieren = by_mal_id(repo, 52991)
    repo.set_cover_paths([(frieren.id, "/tmp/old.jpg")])
    apply_mal_list(conn, mal_list_result, clock())
    assert repo.get_anime(frieren.id).cover_path == "/tmp/old.jpg"
    apply_mal_list(conn, with_entry(mal_list_result, 52991, picture_url="https://cdn.myanimelist.net/new.jpg"), clock())
    assert repo.get_anime(frieren.id).cover_path is None
    assert (frieren.id, "https://cdn.myanimelist.net/new.jpg") in repo.covers_to_fetch()


def test_failed_import_changes_nothing(repo, conn, clock, mal_list_result):
    broken = with_entry(mal_list_result, 99002, title=None)  # violates NOT NULL mid-import
    with pytest.raises(sqlite3.IntegrityError):
        apply_mal_list(conn, broken, clock())
    assert conn.execute("SELECT COUNT(*) FROM anime").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM import_batch").fetchone()[0] == 0

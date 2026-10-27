import pytest

from anical.db.repository import DuplicateExternalIdError
from anical.domain.models import (
    AiringStatus,
    AnimeDraft,
    EpisodeKind,
    ValidationError,
    WatchSource,
    WatchState,
)


def ids(episode_list, *numbers):
    by_number = {e.number: e.id for e in episode_list.episodes if e.kind is EpisodeKind.REGULAR}
    return [by_number[n] for n in numbers]


def states(repo, anime_id):
    return [e.state.value[0] for e in repo.episodes(anime_id).episodes]  # 'w' / 'u'


def test_manual_entry_without_external_ids(repo):
    anime_id = repo.create_anime(AnimeDraft(title="Frieren: Beyond Journey’s End", total_episodes=3, tags=("fantasy",)))
    anime = repo.get_anime(anime_id)
    assert anime.title == "Frieren: Beyond Journey’s End"
    assert anime.added_manually
    assert anime.mal_id is None
    assert anime.tags == ("fantasy",)
    assert [e.number for e in repo.episodes(anime_id).episodes] == [1, 2, 3]


def test_unicode_and_shell_sensitive_titles_round_trip(repo):
    title = "Re:ゼロ — \"$(rm -rf ~)\"; `x` & 'y'"
    anime_id = repo.create_anime(AnimeDraft(title=title, synonyms=("Ré:Zero",)))
    assert repo.get_anime(anime_id).title == title
    assert repo.get_anime(anime_id).synonyms == ("Ré:Zero",)


def test_invalid_draft_is_not_saved(repo):
    with pytest.raises(ValidationError):
        repo.create_anime(AnimeDraft(title=" "))
    assert repo.list_summaries() == []


def test_mal_id_is_unique_across_entries(repo):
    first = repo.create_anime(AnimeDraft(title="One", mal_id=52991))
    second = repo.create_anime(AnimeDraft(title="Two"))
    with pytest.raises(DuplicateExternalIdError) as info:
        repo.update_anime(second, AnimeDraft(title="Two", mal_id=52991))
    assert info.value.anime_id == first
    assert repo.get_anime(second).mal_id is None  # failed update left nothing behind


def test_mal_id_can_be_cleared(repo):
    anime_id = repo.create_anime(AnimeDraft(title="One", mal_id=21))
    repo.update_anime(anime_id, AnimeDraft(title="One"))
    assert repo.get_anime(anime_id).mal_id is None


def test_watch_state_and_summary_counts(repo, clock):
    anime_id = repo.create_anime(AnimeDraft(title="Show", total_episodes=4, airing_status=AiringStatus.AIRING))
    episodes = repo.episodes(anime_id)
    assert repo.set_watch_state(ids(episodes, 1, 2), WatchState.WATCHED) == 2
    assert repo.set_watch_state(ids(episodes, 1, 2), WatchState.WATCHED) == 0  # already watched
    assert states(repo, anime_id) == ["w", "w", "u", "u"]
    (summary,) = repo.list_summaries()
    assert (summary.episode_count, summary.watched_count) == (4, 2)

    clock.advance()
    repo.set_watch_state(ids(episodes, 2), WatchState.UNWATCHED)
    assert states(repo, anime_id) == ["w", "u", "u", "u"]


def test_local_events_beat_mal_import_regardless_of_order(repo, conn, clock):
    anime_id = repo.create_anime(AnimeDraft(title="Show", total_episodes=3))
    episodes = repo.episodes(anime_id)
    # User explicitly marks episode 1 unwatched (needs a prior watched state to register).
    repo.set_watch_state(ids(episodes, 1), WatchState.WATCHED)
    clock.advance()
    repo.set_watch_state(ids(episodes, 1), WatchState.UNWATCHED)
    clock.advance()
    # A later MAL import claims episodes 1-2 are watched.
    repo.set_watch_state(ids(episodes, 1, 2), WatchState.WATCHED, WatchSource.MAL_IMPORT)
    assert states(repo, anime_id) == ["u", "w", "u"]


def test_shrinking_episode_count_keeps_episodes_with_history(repo):
    anime_id = repo.create_anime(AnimeDraft(title="Show", total_episodes=5))
    repo.set_watch_state(ids(repo.episodes(anime_id), 5), WatchState.WATCHED)
    repo.update_anime(anime_id, AnimeDraft(title="Show", total_episodes=2))
    assert [e.number for e in repo.episodes(anime_id).episodes] == [1, 2, 5]


def test_unknown_total_leaves_episodes_alone(repo):
    anime_id = repo.create_anime(AnimeDraft(title="Show", total_episodes=2))
    repo.update_anime(anime_id, AnimeDraft(title="Show", total_episodes=None))
    assert len(repo.episodes(anime_id).episodes) == 2


def test_add_episode_appends_next_number(repo):
    anime_id = repo.create_anime(AnimeDraft(title="Ongoing"))
    repo.add_episode(anime_id)
    repo.add_episode(anime_id)
    repo.add_episode(anime_id, EpisodeKind.SPECIAL)
    labels = [e.label for e in repo.episodes(anime_id).episodes]
    assert labels == ["Episode 1", "Episode 2", "Special 1"]
    (summary,) = repo.list_summaries()
    assert summary.episode_count == 2  # specials don't count towards progress


def test_delete_cascades(repo, conn):
    anime_id = repo.create_anime(AnimeDraft(title="Show", total_episodes=2, tags=("a",), mal_id=5))
    repo.set_watch_state(ids(repo.episodes(anime_id), 1), WatchState.WATCHED)
    repo.delete_anime(anime_id)
    for table in ("anime", "episode", "watch_event", "tag", "external_id"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0  # noqa: S608 - fixed table names


def test_updates_bump_timestamp(repo, clock):
    anime_id = repo.create_anime(AnimeDraft(title="Show", total_episodes=1))
    created = repo.get_anime(anime_id).updated_at
    clock.advance(60)
    repo.set_watch_state(ids(repo.episodes(anime_id), 1), WatchState.WATCHED)
    assert repo.get_anime(anime_id).updated_at == created + 60

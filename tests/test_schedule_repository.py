import pytest

from anical.db.mal_import import apply_mal_list
from anical.db.schedule import ScheduleRepository
from anical.domain.models import AiringStatus, AnimeDraft, WatchState
from anical.domain.schedule import AiringSource, Precision
from anical.providers.anilist import AiringSlot, AniListMedia

NOW = 1_793_040_000
DAY = 86400
WINDOW_END = NOW + 365 * DAY


@pytest.fixture
def schedule(conn, clock):
    clock.now = NOW
    return ScheduleRepository(conn, clock)


@pytest.fixture
def imported(conn, mal_list_result):
    """Library from the MAL fixture: 52991 (finished, 28 eps), 99001 (airing, unknown length), 99002."""
    apply_mal_list(conn, mal_list_result, NOW)
    return {
        int(r["ext_id"]): r["anime_id"]
        for r in conn.execute("SELECT anime_id, ext_id FROM external_id WHERE provider = 'mal'")
    }


def ongoing_media():
    return AniListMedia(5001, 99001, "RELEASING", None)


def slots(*pairs):
    return [AiringSlot(5001, episode, airs_at) for episode, airs_at in pairs]


def test_targets_are_shows_whose_schedule_can_change(schedule, imported, repo):
    repo.create_anime(AnimeDraft(title="Manual, no MAL id", airing_status=AiringStatus.AIRING))
    targets = {t.mal_id for t in schedule.targets(NOW)}
    assert targets == {99001, 99002}  # Frieren ended in 2024


def test_apply_creates_episodes_links_ids_and_reports_delays(schedule, imported, conn, repo):
    anime_id = imported[99001]
    summary = schedule.apply_anilist([ongoing_media()], slots((4, NOW - DAY), (5, NOW + 6 * DAY)), WINDOW_END, NOW)
    assert (summary.shows, summary.airings, summary.changed) == (1, 2, 0)
    assert repo.episodes(anime_id).regular_count == 5  # MAL progress was 3; schedule extends it
    assert {t.anilist_id for t in schedule.targets(NOW)} >= {5001}

    # Episode 5 slips by a week: logged as a change and flagged as delayed.
    summary = schedule.apply_anilist([ongoing_media()], slots((4, NOW - DAY), (5, NOW + 13 * DAY)), WINDOW_END, NOW)
    assert summary.changed == 1
    (entry,) = schedule.calendar_entries(NOW, NOW + 30 * DAY)
    assert entry.number == 5 and entry.delayed and entry.airs_at == NOW + 13 * DAY


def test_small_time_corrections_are_not_delays(schedule, imported):
    schedule.apply_anilist([ongoing_media()], slots((5, NOW + DAY)), WINDOW_END, NOW)
    assert schedule.apply_anilist([ongoing_media()], slots((5, NOW + DAY + 300)), WINDOW_END, NOW).changed == 0


def test_withdrawn_future_episodes_are_removed_but_past_ones_kept(schedule, imported):
    schedule.apply_anilist([ongoing_media()], slots((3, NOW - 7 * DAY), (4, NOW + DAY)), WINDOW_END, NOW)
    summary = schedule.apply_anilist([ongoing_media()], [], WINDOW_END, NOW)
    assert summary.removed == 1
    remaining = schedule.calendar_entries(0, WINDOW_END)
    assert [e.number for e in remaining] == [3]
    assert [s.title for s in schedule.undated_shows(NOW)] == ["Ongoing Show", "Special Thing"]


def test_unknown_mal_ids_and_conflicting_links_are_ignored(schedule, imported, conn):
    stranger = AniListMedia(7777, 123456, "RELEASING", None)
    assert schedule.apply_anilist([stranger], [AiringSlot(7777, 1, NOW)], WINDOW_END, NOW).shows == 0
    schedule.apply_anilist([AniListMedia(5001, 99002, "RELEASING", 1)], [], WINDOW_END, NOW)
    # 5001 now belongs to 99002; claiming it for 99001 must not steal it.
    assert schedule.apply_anilist([ongoing_media()], slots((1, NOW)), WINDOW_END, NOW).shows == 0


def test_broadcast_projection_only_without_better_data(schedule, imported, conn, repo):
    # Give the ongoing show an exact start date so it can be projected.
    conn.execute(
        "UPDATE anime SET start_date = '2026-10-04', broadcast_time = '23:30' WHERE id = ?", (imported[99001],)
    )
    assert schedule.project_broadcasts(NOW) == 1
    projected = schedule.calendar_entries(0, WINDOW_END)
    assert projected and all(e.precision is Precision.ESTIMATED for e in projected)
    assert projected[0].number == 1

    schedule.apply_anilist([ongoing_media()], slots((5, NOW + DAY)), WINDOW_END, NOW)
    assert schedule.project_broadcasts(NOW) == 0
    entries = schedule.calendar_entries(0, WINDOW_END)
    assert [(e.number, e.source) for e in entries] == [(5, AiringSource.ANILIST)]


def test_user_airings_win_and_can_be_cleared(schedule, imported, repo):
    anime_id = imported[99001]
    schedule.apply_anilist([ongoing_media()], slots((5, NOW + DAY)), WINDOW_END, NOW)
    schedule.set_user_airings(anime_id, [(5, NOW + 2 * DAY), (6, NOW + 9 * DAY)])
    entries = schedule.calendar_entries(NOW, WINDOW_END)
    assert [(e.number, e.source, e.airs_at) for e in entries] == [
        (5, AiringSource.USER, NOW + 2 * DAY),
        (6, AiringSource.USER, NOW + 9 * DAY),
    ]
    # A refresh does not override the user's dates.
    schedule.apply_anilist([ongoing_media()], slots((5, NOW + 3 * DAY)), WINDOW_END, NOW)
    assert schedule.calendar_entries(NOW, WINDOW_END)[0].source is AiringSource.USER

    assert schedule.clear_user_airings(anime_id) == 2
    assert [(e.number, e.source) for e in schedule.calendar_entries(NOW, WINDOW_END)] == [(5, AiringSource.ANILIST)]


def test_manual_show_schedule_without_any_source(schedule, repo):
    anime_id = repo.create_anime(AnimeDraft(title="Hand-made", airing_status=AiringStatus.AIRING))
    assert [s.title for s in schedule.undated_shows(NOW)] == ["Hand-made"]
    schedule.set_user_airings(anime_id, [(1, NOW - DAY), (2, NOW + 6 * DAY)])
    assert schedule.undated_shows(NOW) == []
    assert repo.episodes(anime_id).regular_count == 2


def test_aired_unwatched_excludes_future_and_watched(schedule, imported, repo):
    anime_id = imported[99001]
    schedule.apply_anilist(
        [ongoing_media()], slots((3, NOW - 8 * DAY), (4, NOW - DAY), (5, NOW + DAY)), WINDOW_END, NOW
    )
    # Episode 3 is watched via the MAL import; 4 aired but is unwatched; 5 is in the future.
    assert [e.number for e in schedule.aired_unwatched(NOW)] == [4]
    episode_4 = next(e for e in repo.episodes(anime_id).episodes if e.number == 4)
    repo.set_watch_state([episode_4.id], WatchState.WATCHED)
    assert schedule.aired_unwatched(NOW) == []

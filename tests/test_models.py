import pytest

from anical.domain.models import (
    AnimeDraft,
    AnimeSummary,
    Episode,
    EpisodeKind,
    EpisodeList,
    ValidationError,
    WatchProgress,
    WatchState,
)


def errors_for(**fields):
    with pytest.raises(ValidationError) as info:
        AnimeDraft(**fields).validated()
    return info.value.errors


def test_validation_normalizes_whitespace_and_duplicates():
    draft = AnimeDraft(
        title="  Frieren  ",
        title_en="",
        synonyms=(" Sousou no Frieren ", "Sousou no Frieren", ""),
        tags=("Fantasy", "fantasy ", "  "),
        notes="   ",
    ).validated()
    assert draft.title == "Frieren"
    assert draft.title_en is None
    assert draft.synonyms == ("Sousou no Frieren",)
    assert draft.tags == ("Fantasy",)
    assert draft.notes is None


@pytest.mark.parametrize("title", ["", "   "])
def test_title_is_required(title):
    assert "title" in errors_for(title=title)


@pytest.mark.parametrize("value", ["2026", "2026-10", "2026-10-04"])
def test_partial_dates_are_accepted(value):
    assert AnimeDraft(title="x", start_date=value).validated().start_date == value


@pytest.mark.parametrize("value", ["26-10-04", "2026/10/04", "2026-13", "2026-02-30"])
def test_invalid_dates_are_rejected(value):
    assert "start_date" in errors_for(title="x", start_date=value)


def test_end_before_start_is_rejected_but_coarser_end_is_not():
    assert "end_date" in errors_for(title="x", start_date="2026-10-04", end_date="2026-09-30")
    assert AnimeDraft(title="x", start_date="2026-10-04", end_date="2026-10").validated()


def test_numeric_bounds():
    assert "total_episodes" in errors_for(title="x", total_episodes=-1)
    assert "mal_id" in errors_for(title="x", mal_id=0)


def test_progress_classification():
    base = {"id": 1, "title": "x", "episode_count": 12}
    assert AnimeSummary(**base, watched_count=0).progress is WatchProgress.NOT_STARTED
    assert AnimeSummary(**base, watched_count=3).progress is WatchProgress.IN_PROGRESS
    assert AnimeSummary(**base, total_episodes=12, watched_count=12).progress is WatchProgress.FINISHED
    # Unknown length can never be "finished".
    assert AnimeSummary(**base, watched_count=12).progress is WatchProgress.IN_PROGRESS


def test_next_to_play_is_the_first_aired_unwatched_numbered_episode():
    episodes = EpisodeList(
        [
            Episode(1, 9, 1, state=WatchState.WATCHED),
            Episode(2, 9, 1, kind=EpisodeKind.SPECIAL),
            Episode(3, 9, 2),
            Episode(4, 9, 3),
        ]
    )
    assert episodes.next_to_play({}, now=100).id == 3  # no airing times known: playable
    assert episodes.next_to_play({3: 50}, now=100).id == 3
    assert episodes.next_to_play({3: 150, 4: 160}, now=100) is None  # not aired yet
    assert EpisodeList([Episode(1, 9, 1, state=WatchState.WATCHED)]).next_to_play({}, now=100) is None

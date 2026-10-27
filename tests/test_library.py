from anical.domain.library import LibraryQuery, SortKey, WatchFilter, apply, normalize
from anical.domain.models import AiringStatus, AnimeSummary


def summary(id, title, **kw):
    return AnimeSummary(id=id, title=title, **kw)


LIBRARY = [
    summary(
        1,
        "Sousou no Frieren",
        alt_titles=("Frieren: Beyond Journey’s End",),
        tags=("fantasy",),
        airing_status=AiringStatus.COMPLETED,
        total_episodes=28,
        episode_count=28,
        watched_count=28,
        created_at=10,
        updated_at=50,
    ),
    summary(
        2,
        "Pokémon Horizons",
        airing_status=AiringStatus.AIRING,
        episode_count=40,
        watched_count=12,
        created_at=30,
        updated_at=20,
    ),
    summary(
        3,
        "ＡＫＩＲＡ",
        airing_status=AiringStatus.COMPLETED,
        total_episodes=1,
        episode_count=1,
        created_at=20,
        updated_at=10,
    ),
    summary(4, "Upcoming Thing", airing_status=AiringStatus.UPCOMING, created_at=40, updated_at=40),
]


def titles(query):
    return [s.id for s in apply(LIBRARY, query)]


def test_normalize_ignores_case_accents_and_width():
    assert normalize("  Pokémon   HORIZONS ") == "pokemon horizons"
    assert normalize("ＡＫＩＲＡ") == "akira"


def test_search_matches_alternative_titles_tags_and_accents():
    assert titles(LibraryQuery(text="beyond journey")) == [1]
    assert titles(LibraryQuery(text="FANTASY")) == [1]
    assert titles(LibraryQuery(text="pokemon")) == [2]
    assert titles(LibraryQuery(text="akira")) == [3]
    assert titles(LibraryQuery(text="nothing like this")) == []


def test_status_filter():
    assert titles(LibraryQuery(airing_status=AiringStatus.COMPLETED)) == [3, 1]


def test_watch_filters():
    assert titles(LibraryQuery(watch_filter=WatchFilter.FINISHED)) == [1]
    assert titles(LibraryQuery(watch_filter=WatchFilter.IN_PROGRESS)) == [2]
    assert titles(LibraryQuery(watch_filter=WatchFilter.NOT_STARTED)) == [3, 4]
    assert titles(LibraryQuery(watch_filter=WatchFilter.HAS_UNWATCHED)) == [3, 2]


def test_sorting():
    assert titles(LibraryQuery()) == [3, 2, 1, 4]
    assert titles(LibraryQuery(sort=SortKey.RECENTLY_ADDED)) == [4, 2, 3, 1]
    assert titles(LibraryQuery(sort=SortKey.RECENTLY_UPDATED)) == [1, 4, 2, 3]
    assert titles(LibraryQuery(sort=SortKey.AIRING_STATUS)) == [2, 4, 3, 1]
    assert titles(LibraryQuery(sort=SortKey.UNWATCHED)) == [2, 3, 1, 4]


def test_mal_list_status_filter():
    library = [
        summary(1, "A", mal_list_status="watching"),
        summary(2, "B", mal_list_status="completed"),
        summary(3, "C"),
    ]
    assert [s.id for s in apply(library, LibraryQuery(mal_list_status="watching"))] == [1]
    assert [s.id for s in apply(library, LibraryQuery())] == [1, 2, 3]

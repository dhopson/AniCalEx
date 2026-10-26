import datetime
from zoneinfo import ZoneInfo

import pytest

from anical.domain.calendar import (
    CalendarEntry,
    CalendarMode,
    EntryStatus,
    format_time,
    group_by_day,
    range_title,
    shift,
    utc_bounds,
    visible_range,
    week_start,
)
from anical.domain.models import ValidationError, WatchState
from anical.domain.schedule import (
    AiringSource,
    Precision,
    first_broadcast,
    manual_series,
    project_broadcast,
    weekly_series,
)
from anical.net.http import Response
from anical.providers.anilist import API_URL, AniListClient, AniListError
from helpers import FakeHttp, fixture_json, json_response

NOW = 1_793_040_000  # 2026-10-26
JST = ZoneInfo("Asia/Tokyo")
NEW_YORK = ZoneInfo("America/New_York")
D = datetime.date


def client(http, sleeps=None):
    return AniListClient(http, sleep=sleeps.append if sleeps is not None else (lambda s: None), clock=lambda: NOW)


def page(key, items, has_next=False):
    return json_response({"data": {"Page": {"pageInfo": {"hasNextPage": has_next}, key: items}}})


# AniList client


def test_media_lookup_batches_by_fifty_and_validates():
    mal_ids = list(range(1, 76))
    http = FakeHttp(
        page("media", [{"id": 500, "idMal": 1, "status": "RELEASING", "episodes": None}, {"id": None, "idMal": 2}]),
        page("media", [{"id": 575, "idMal": 75, "status": "FINISHED", "episodes": 12}]),
    )
    media = client(http).media_by_mal_ids(mal_ids)
    assert [(m.anilist_id, m.mal_id, m.episodes) for m in media] == [(500, 1, None), (575, 75, 12)]
    assert [len(r["json"]["variables"]["ids"]) for r in http.requests] == [50, 25]
    assert all(r["url"] == API_URL and r["method"] == "POST" for r in http.requests)


def test_schedule_window_follows_pages_and_rejects_bad_slots():
    recorded = fixture_json("anilist_schedules_page1.json")["data"]["Page"]["airingSchedules"]
    junk = [
        {"mediaId": 1001, "episode": 0, "airingAt": NOW},  # episode 0
        {"mediaId": 9999, "episode": 1, "airingAt": NOW},  # not requested
        {"mediaId": 1001, "episode": 2, "airingAt": 12},  # implausible time
        {"mediaId": 1002, "episode": 9, "airingAt": NOW + 10 * 365 * 86400},
    ]
    http = FakeHttp(page("airingSchedules", recorded[:8], True), page("airingSchedules", recorded[8:] + junk))
    slots = client(http).airing_schedules([1001, 1002, 1003], NOW - 86400 * 60, NOW + 86400 * 365)
    assert len(slots) == len(recorded) == 15
    assert slots == sorted(slots, key=lambda s: s.airs_at)
    assert http.requests[1]["json"]["variables"]["page"] == 2


def test_graphql_errors_and_rate_limits():
    sleeps = []
    http = FakeHttp(Response(429, b"", {"Retry-After": "30"}), page("media", []))
    assert client(http, sleeps).media_by_mal_ids([1]) == []
    assert 30.0 in sleeps
    with pytest.raises(AniListError, match="Too bad"):
        client(FakeHttp(json_response({"errors": [{"message": "Too bad"}], "data": None}))).media_by_mal_ids([1])
    with pytest.raises(AniListError, match="unexpected"):
        client(FakeHttp(json_response({"data": {}}))).media_by_mal_ids([1])


# Projection


def test_first_broadcast_lands_on_the_weekday():
    # 2026-10-02 is a Friday; a Sunday 23:30 slot first airs on the 4th.
    assert first_broadcast(D(2026, 10, 2), 6, "23:30", JST) == datetime.datetime(2026, 10, 4, 23, 30, tzinfo=JST)
    assert first_broadcast(D(2026, 10, 2), 4, "01:00", JST).date() == D(2026, 10, 2)
    assert first_broadcast(D(2026, 10, 2), 4, "25:00", JST) is None


def test_weekly_series_keeps_local_time_across_dst():
    first = datetime.datetime(2026, 10, 25, 20, 0, tzinfo=NEW_YORK)  # DST ends 2026-11-01
    a, b = weekly_series(first, 2)
    assert b - a == 7 * 86400 + 3600
    assert datetime.datetime.fromtimestamp(b, NEW_YORK).hour == 20


def test_projection_with_known_and_unknown_length():
    known = project_broadcast("2026-10-02", 4, "23:00", "Asia/Tokyo", 3, NOW)
    assert [n for n, _ in known] == [1, 2, 3]
    assert known[1][1] - known[0][1] == 7 * 86400
    # Unknown length: projects through ~4 weeks past "now" instead of inventing an end.
    unknown = project_broadcast("2026-10-02", 4, "23:00", "Asia/Tokyo", None, NOW)
    assert NOW + 3 * 7 * 86400 < unknown[-1][1] <= NOW + 5 * 7 * 86400


@pytest.mark.parametrize(
    "args",
    [
        ("2026-10", 4, "23:00", "Asia/Tokyo"),  # partial date: no anchor
        (None, 4, "23:00", "Asia/Tokyo"),
        ("2026-10-02", None, "23:00", "Asia/Tokyo"),
        ("2026-10-02", 4, None, "Asia/Tokyo"),
        ("2026-10-02", 4, "23:00", "Not/AZone"),
    ],
)
def test_projection_refuses_to_guess(args):
    assert project_broadcast(*args, 12, NOW) == []


# Calendar rules


def entry(airs_at, state=WatchState.UNWATCHED, **kw):
    return CalendarEntry(1, 1, kw.pop("title", "Show"), kw.pop("number", 1), airs_at, Precision.EXACT,
                         AiringSource.ANILIST, state, **kw)


def test_entry_status_distinguishes_airing_from_availability():
    lag = 7200
    assert entry(NOW + 1).status(NOW, lag) is EntryStatus.UPCOMING
    assert entry(NOW - 60).status(NOW, lag) is EntryStatus.JUST_AIRED
    assert entry(NOW - lag).status(NOW, lag) is EntryStatus.AVAILABLE
    assert entry(NOW + 999, WatchState.WATCHED).status(NOW, lag) is EntryStatus.WATCHED


@pytest.mark.parametrize(("first_weekday", "expected"), [(0, D(2026, 10, 26)), (6, D(2026, 10, 25))])
def test_week_start(first_weekday, expected):
    assert week_start(D(2026, 10, 28), first_weekday) == expected


def test_visible_ranges():
    anchor = D(2026, 10, 28)
    assert visible_range(CalendarMode.DAY, anchor, 0) == (anchor, D(2026, 10, 29))
    assert visible_range(CalendarMode.WEEK, anchor, 0) == (D(2026, 10, 26), D(2026, 11, 2))
    start, end = visible_range(CalendarMode.MONTH, anchor, 0)
    assert (start, end) == (D(2026, 9, 28), D(2026, 11, 2))
    assert (end - start).days % 7 == 0


def test_shift_clamps_month_ends():
    assert shift(CalendarMode.MONTH, D(2026, 1, 31), 1) == D(2026, 2, 28)
    assert shift(CalendarMode.MONTH, D(2026, 12, 15), 1) == D(2027, 1, 15)
    assert shift(CalendarMode.MONTH, D(2026, 1, 15), -1) == D(2025, 12, 15)
    assert shift(CalendarMode.WEEK, D(2026, 10, 28), -1) == D(2026, 10, 21)


def test_utc_bounds_follow_dst():
    start, end = utc_bounds(D(2026, 10, 31), D(2026, 11, 2), NEW_YORK)
    assert end - start == 2 * 86400 + 3600  # the night DST ends is 25 hours long


def test_group_by_local_day_uses_display_timezone():
    # 2026-11-01 03:30 UTC is Oct 31 in New York but Nov 1 in Tokyo.
    late = int(datetime.datetime(2026, 11, 1, 3, 30, tzinfo=datetime.UTC).timestamp())
    early = late - 3600
    entries = [entry(late, title="B"), entry(early, title="A")]
    assert list(group_by_day(entries, NEW_YORK)) == [D(2026, 10, 31)]
    assert [e.title for e in group_by_day(entries, NEW_YORK)[D(2026, 10, 31)]] == ["A", "B"]
    assert list(group_by_day(entries, JST)) == [D(2026, 11, 1)]


def test_labels():
    moment = datetime.datetime(2026, 10, 26, 9, 5)
    assert format_time(moment, True) == "09:05"
    assert format_time(moment, False) == "9:05 AM"
    assert range_title(CalendarMode.WEEK, D(2026, 10, 28), 0) == "26 Oct – 1 Nov 2026"
    assert range_title(CalendarMode.WEEK, D(2026, 12, 30), 0) == "28 Dec 2026 – 3 Jan 2027"
    assert range_title(CalendarMode.WEEK, D(2026, 10, 14), 0) == "12 – 18 Oct 2026"
    assert range_title(CalendarMode.MONTH, D(2026, 10, 14), 0) == "October 2026"


def test_manual_series():
    single = manual_series(5, None, "2026-10-31", "23:30", NEW_YORK)
    assert single == [(5, int(datetime.datetime(2026, 10, 31, 23, 30, tzinfo=NEW_YORK).timestamp()))]
    weekly = manual_series(5, 7, "2026-10-31", "23:30", NEW_YORK)
    assert [n for n, _ in weekly] == [5, 6, 7]
    assert datetime.datetime.fromtimestamp(weekly[1][1], NEW_YORK).hour == 23  # across the DST change


@pytest.mark.parametrize(
    ("args", "field"),
    [
        ((1, None, "31/10/2026", "23:30"), "date"),
        ((1, None, "2026-10-31", "11:30pm"), "time"),
        ((0, None, "2026-10-31", "23:30"), "first_episode"),
        ((5, 4, "2026-10-31", "23:30"), "last_episode"),
        ((1, 500, "2026-10-31", "23:30"), "last_episode"),
    ],
)
def test_manual_series_validation(args, field):
    with pytest.raises(ValidationError) as info:
        manual_series(*args, NEW_YORK)
    assert field in info.value.errors

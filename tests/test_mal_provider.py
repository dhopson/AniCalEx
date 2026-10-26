import pytest

from anical.domain.models import AiringStatus, MediaType
from anical.net.http import NetworkError, Response
from anical.providers.mal import API_BASE, MalAuthError, MalClient, MalError, parse_anime
from helpers import FakeHttp, fixture_json, json_response


def client(http, token=None, sleeps=None):
    return MalClient(http, "client-id", token, sleep=(sleeps.append if sleeps is not None else lambda s: None))


def test_list_parsing_and_paging(mal_list_result):
    entries = {e.anime.mal_id: e for e in mal_list_result.entries}
    assert set(entries) == {52991, 99001, 99002}
    assert mal_list_result.skipped == 1  # the entry with id 0

    frieren = entries[52991]
    assert frieren.anime.title_en == "Frieren: Beyond Journey's End"
    assert frieren.anime.title_ja == "葬送のフリーレン"
    assert frieren.anime.airing_status is AiringStatus.COMPLETED
    assert frieren.anime.total_episodes == 28
    assert (frieren.anime.broadcast_day, frieren.anime.broadcast_time) == (4, "23:00")
    assert frieren.anime.picture_url.endswith("138006l.jpg")  # large preferred
    assert frieren.list_status == "completed" and frieren.episodes_watched == 28
    assert frieren.updated_at == 1711188000

    ongoing = entries[99001]
    assert ongoing.anime.total_episodes is None  # MAL's 0 means unknown
    assert ongoing.anime.title_en is None  # empty strings are absent
    assert ongoing.anime.broadcast_time is None  # "24:30" is not a valid time
    assert ongoing.anime.start_date == "2026-10"

    special = entries[99002]
    assert special.anime.media_type is MediaType.SPECIAL
    assert special.anime.airing_status is AiringStatus.UPCOMING
    assert special.anime.end_date is None


def test_fetch_list_requests():
    http = FakeHttp(
        json_response(fixture_json("mal_animelist_page1.json")),
        json_response(fixture_json("mal_animelist_page2.json")),
    )
    client(http, token="tok").fetch_list()
    first, second = http.requests
    assert first["url"] == f"{API_BASE}/users/@me/animelist"
    assert first["params"]["limit"] == "1000" and "list_status" in first["params"]["fields"]
    assert first["headers"] == {"Authorization": "Bearer tok"}
    assert second["url"].startswith(f"{API_BASE}/users/@me/animelist?offset=3")
    assert second["params"] is None


def test_public_list_uses_client_id_and_escapes_username():
    http = FakeHttp(json_response({"data": [], "paging": {}}))
    client(http).fetch_list("some user/../x")
    (request,) = http.requests
    assert request["headers"] == {"X-MAL-CLIENT-ID": "client-id"}
    assert request["url"] == f"{API_BASE}/users/some%20user%2F..%2Fx/animelist"


def test_paging_to_a_foreign_host_is_ignored():
    http = FakeHttp(json_response({"data": [], "paging": {"next": "https://evil.example/steal"}}))
    assert client(http).fetch_list().entries == ()
    assert len(http.requests) == 1


def test_rate_limit_is_retried_with_retry_after():
    sleeps = []
    http = FakeHttp(
        Response(429, b"", {"Retry-After": "7"}),
        json_response({"data": [], "paging": {}}),
    )
    client(http, sleeps=sleeps).fetch_list()
    assert 7.0 in sleeps


def test_persistent_403_gives_up_with_a_clear_message():
    http = FakeHttp(*[Response(403, b"{}") for _ in range(4)])
    with pytest.raises(MalError, match="private"):
        client(http).fetch_list("someone")
    assert len(http.requests) == 4


def test_unauthorized_raises_auth_error():
    with pytest.raises(MalAuthError):
        client(FakeHttp(Response(401, b"{}")), token="old").fetch_list()


@pytest.mark.parametrize("reply", [Response(200, b"<html>"), json_response({"unexpected": True})])
def test_malformed_responses_raise(reply):
    with pytest.raises(MalError):
        client(FakeHttp(reply)).fetch_list()


def test_network_errors_propagate():
    with pytest.raises(NetworkError):
        client(FakeHttp(NetworkError("offline"))).fetch_list()


def test_requests_are_throttled():
    sleeps = []
    times = iter([0.0, 0.2])
    http = FakeHttp(
        json_response({"data": [], "paging": {"next": f"{API_BASE}/users/@me/animelist?offset=1"}}),
        json_response({"data": [], "paging": {}}),
    )
    MalClient(http, "c", sleep=sleeps.append, monotonic=lambda: next(times)).fetch_list()
    assert sleeps == [pytest.approx(0.8)]


@pytest.mark.parametrize("node", [None, {}, {"id": "5", "title": "x"}, {"id": 5, "title": "  "}, {"id": -1, "title": "x"}])
def test_invalid_nodes_are_rejected(node):
    assert parse_anime(node) is None


def test_non_https_pictures_are_ignored():
    anime = parse_anime({"id": 1, "title": "x", "main_picture": {"large": "http://insecure/x.jpg"}})
    assert anime.picture_url is None

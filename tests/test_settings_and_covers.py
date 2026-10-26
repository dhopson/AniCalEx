import pytest

from anical.db.settings import LAST_REFRESH_RUN, SettingsRepository
from anical.domain.refresh import REFRESH_INTERVAL_S, refresh_due
from anical.net.http import NetworkError, Response
from anical.sync.covers import download_covers
from helpers import FakeHttp

NOW = 1_790_000_000


def test_settings_round_trip(conn, clock):
    settings = SettingsRepository(conn, clock)
    assert settings.get("mal.client_id", "default") == "default"
    settings.set("mal.client_id", "abc")
    settings.set("nested", {"a": [1, 2]})
    assert settings.get("mal.client_id") == "abc"
    assert settings.get("nested") == {"a": [1, 2]}
    settings.set("mal.client_id", None)
    assert settings.get("mal.client_id") is None


def test_last_refresh_run_is_persisted(conn, clock):
    settings = SettingsRepository(conn, clock)
    assert settings.last_refresh_run() is None
    settings.set_state(LAST_REFRESH_RUN, str(NOW))
    assert SettingsRepository(conn, clock).last_refresh_run() == NOW


@pytest.mark.parametrize(
    ("last_run", "now", "due"),
    [
        (None, NOW, True),
        (NOW - 60, NOW, False),  # restarted within the window: skip
        (NOW - REFRESH_INTERVAL_S + 1, NOW, False),
        (NOW - REFRESH_INTERVAL_S, NOW, True),
        (NOW + 3600, NOW, True),  # clock went backwards
    ],
)
def test_refresh_due(last_run, now, due):
    assert refresh_due(last_run, now) is due


def image(body=b"\xff\xd8jpeg", content_type="image/jpeg"):
    return Response(200, body, {"Content-Type": content_type})


def test_download_covers(tmp_path):
    http = FakeHttp(
        image(),
        Response(200, b"<html>", {"Content-Type": "text/html"}),  # not an image: skipped
        Response(404, b""),
        image(b"RIFFwebp", "image/webp"),
    )
    items = [(1, "https://cdn/a.jpg"), (2, "https://cdn/b"), (3, "https://cdn/c.jpg"), (4, "https://cdn/d.webp")]
    saved = download_covers(http, items, tmp_path / "covers", sleep=lambda s: None)
    assert [anime_id for anime_id, _ in saved] == [1, 4]
    assert saved[1][1].endswith(".webp")
    assert open(saved[0][1], "rb").read() == b"\xff\xd8jpeg"
    assert not list((tmp_path / "covers").glob("*.part"))


def test_download_covers_stops_when_offline(tmp_path):
    http = FakeHttp(image(), NetworkError("offline"))
    saved = download_covers(http, [(1, "https://a"), (2, "https://b"), (3, "https://c")], tmp_path, sleep=lambda s: None)
    assert [anime_id for anime_id, _ in saved] == [1]

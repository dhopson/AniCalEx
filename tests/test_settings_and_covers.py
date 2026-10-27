import io
from pathlib import Path

import pytest
from helpers import FakeHttp

from anical.db.settings import LAST_REFRESH_RUN, SettingsRepository
from anical.domain.refresh import REFRESH_INTERVAL_S, refresh_due
from anical.net import http as http_module
from anical.net.http import NetworkError, Response, UrllibClient, allowed_url
from anical.sync.covers import download_covers

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
    assert Path(saved[0][1]).read_bytes() == b"\xff\xd8jpeg"
    assert not list((tmp_path / "covers").glob("*.part"))


def test_download_covers_skips_urls_that_are_not_web_addresses(tmp_path):
    http = FakeHttp(image())
    items = [(1, "file:///etc/passwd"), (2, "ftp://host/x.jpg"), (3, "https://cdn/c.jpg")]
    saved = download_covers(http, items, tmp_path / "covers", sleep=lambda s: None)
    assert [anime_id for anime_id, _ in saved] == [3]
    assert [r["url"] for r in http.requests] == ["https://cdn/c.jpg"]  # the others were never requested


def test_http_client_only_fetches_web_addresses():
    assert allowed_url("https://myanimelist.net/") and allowed_url("HTTP://example.org/")
    assert not allowed_url("file:///etc/passwd") and not allowed_url("ftp://x/y") and not allowed_url("/etc/passwd")
    with pytest.raises(NetworkError, match="Not a web address"):
        UrllibClient().request("GET", "file:///etc/passwd")


def test_http_client_refuses_oversized_replies(monkeypatch):
    monkeypatch.setattr(http_module, "MAX_RESPONSE_BYTES", 8)
    assert http_module._read_limited(io.BytesIO(b"12345678")) == b"12345678"
    with pytest.raises(NetworkError, match="larger than"):
        http_module._read_limited(io.BytesIO(b"123456789"))


def test_download_covers_stops_when_offline(tmp_path):
    http = FakeHttp(image(), NetworkError("offline"))
    saved = download_covers(
        http, [(1, "https://a"), (2, "https://b"), (3, "https://c")], tmp_path, sleep=lambda s: None
    )
    assert [anime_id for anime_id, _ in saved] == [1]

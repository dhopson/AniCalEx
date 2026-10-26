"""Builds the real widgets against a temporary library. Skipped without a display."""

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

if not Gtk.init_check():
    pytest.skip("no display available", allow_module_level=True)
Adw.init()

from anical.db.settings import MAL_CLIENT_ID, MAL_MODE, MAL_USERNAME, SettingsRepository  # noqa: E402
from anical.domain.models import AiringStatus, AnimeDraft, WatchState  # noqa: E402
from anical.net.http import NetworkError, Response  # noqa: E402
from anical.sync.mal_sync import MalSyncService  # noqa: E402
from anical.ui.editor import AnimeEditorDialog  # noqa: E402
from anical.ui.preferences import PreferencesDialog  # noqa: E402
from anical.ui.show_page import ShowPage  # noqa: E402
from anical.ui.sync_controller import SyncController  # noqa: E402
from anical.ui.window import MainWindow  # noqa: E402
from helpers import FakeHttp, MemoryTokenStore, fixture_json, json_response  # noqa: E402


def drain():
    context = GLib.MainContext.default()
    while context.pending():
        context.iteration(False)


def wait_until(condition, timeout=5.0):
    deadline = GLib.get_monotonic_time() + int(timeout * 1e6)
    while not condition():
        assert GLib.get_monotonic_time() < deadline, "timed out"
        GLib.MainContext.default().iteration(False)


@pytest.fixture
def http():
    return FakeHttp()


@pytest.fixture
def settings(conn, clock):
    return SettingsRepository(conn, clock)


@pytest.fixture
def controller(repo, settings, http, clock, tmp_path):
    service = MalSyncService(http, MemoryTokenStore(), clock=clock)
    return SyncController(repo, settings, service, http, tmp_path / "covers", clock=clock)


@pytest.fixture
def window(repo, controller):
    win = MainWindow(application=None, repository=repo, sync_controller=controller)
    yield win
    win.destroy()
    drain()


def visible_ids(page):
    """Anime ids of the rows the list box currently shows, in display order."""
    ids, index = [], 0
    while row := page._list.get_row_at_index(index):
        if row.get_child_visible():
            ids.append(row.anime_id)
        index += 1
    return ids


def use_public_list(settings):
    settings.set(MAL_CLIENT_ID, "cid")
    settings.set(MAL_MODE, "username")
    settings.set(MAL_USERNAME, "someone")


def test_empty_library_shows_empty_state(window):
    assert window.library_page._stack.get_visible_child_name() == "empty"


def test_library_lists_and_filters(window, repo):
    repo.create_anime(AnimeDraft(title="Frieren", airing_status=AiringStatus.COMPLETED, total_episodes=2))
    repo.create_anime(AnimeDraft(title="Ongoing Show", airing_status=AiringStatus.AIRING))
    window.library_page.reload()
    page = window.library_page
    assert page._stack.get_visible_child_name() == "list"
    assert not page._list_filter.get_parent().get_visible()  # no MAL data, no list filter
    assert visible_ids(page) == [1, 2]
    page._search_entry.set_text("frier")
    page._on_query_changed()
    assert visible_ids(page) == [1]
    page._search_entry.set_text("zzz")
    page._on_query_changed()
    assert page._stack.get_visible_child_name() == "no-results"


def test_show_page_toggles_and_marks_all(window, repo):
    anime_id = repo.create_anime(AnimeDraft(title="Show", total_episodes=3))
    page = ShowPage(window, anime_id)
    page._mark_all(WatchState.WATCHED)
    assert repo.episodes(anime_id).watched_count == 3
    page._mark_all(WatchState.UNWATCHED)
    assert repo.episodes(anime_id).watched_count == 0


def test_editor_creates_entry_and_reports_errors(repo):
    saved = []
    dialog = AnimeEditorDialog(repo, anime=None, on_saved=saved.append)
    dialog._on_save()
    assert saved == [] and dialog._title.has_css_class("error")

    dialog._title.set_text("Новый — 新しい")
    dialog._episodes.set_text("12x")
    dialog._on_save()
    assert saved == [] and dialog._episodes.has_css_class("error")

    dialog._episodes.set_text("12")
    dialog._on_save()
    assert len(saved) == 1
    assert repo.get_anime(saved[0]).title == "Новый — 新しい"
    assert repo.episodes(saved[0]).regular_count == 12


def test_sync_end_to_end(window, controller, settings, http, repo, clock):
    use_public_list(settings)
    http.replies += [
        json_response(fixture_json("mal_animelist_page1.json")),
        json_response(fixture_json("mal_animelist_page2.json")),
        Response(200, b"\xff\xd8jpeg", {"Content-Type": "image/jpeg"}),
        NetworkError("offline"),  # second cover: stop quietly
    ]
    controller.start()
    assert controller.busy
    wait_until(lambda: not controller.busy)
    controller.stop()

    assert settings.last_refresh_run() == clock()
    assert controller.error is None
    assert len(repo.list_summaries()) == 3
    assert sum(1 for s in repo.list_summaries() if s.cover_path) == 1
    assert window.library_page._list_filter.get_parent().get_visible()

    # Restarting inside the 24h window does not refresh again (no requests queued).
    clock.advance(3600)
    controller.sync_if_due()
    assert not controller.busy


def test_offline_sync_keeps_data_and_shows_banner(window, controller, settings, http, repo):
    use_public_list(settings)
    repo.create_anime(AnimeDraft(title="Kept"))
    http.replies.append(NetworkError("offline"))
    controller.sync()
    wait_until(lambda: not controller.busy)
    assert "Couldn’t reach MyAnimeList" in controller.error
    assert window.library_page._banner.get_revealed()
    assert settings.last_refresh_run() is None  # failed runs don't count
    assert [s.title for s in repo.list_summaries()] == ["Kept"]


@pytest.mark.parametrize("mode", [None, "username", "account"])
def test_preferences_build_in_every_mode(controller, settings, mode):
    settings.set(MAL_CLIENT_ID, "cid")
    settings.set(MAL_MODE, mode)
    settings.set(MAL_USERNAME, "someone" if mode else None)
    dialog = PreferencesDialog(controller)
    controller.emit("changed")  # rebuild path
    dialog.force_close()

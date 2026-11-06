"""Builds the real widgets against a temporary library. Skipped without a display."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

if not Gtk.init_check():
    pytest.skip("no display available", allow_module_level=True)
Adw.init()

from helpers import FakeHttp, MemoryTokenStore, fixture_json, json_response

from anical.auth.mal_oauth import RENEW_AFTER_S, OAuthError, Token
from anical.db.playback import PlaybackRepository
from anical.db.schedule import ScheduleRepository
from anical.db.settings import (
    DISPLAY_ENGLISH_TITLES,
    DISPLAY_PLAYING_SIZE,
    DISPLAY_TIMEZONE,
    MAL_CLIENT_ID,
    MAL_MODE,
    MAL_RENEWAL_PROBLEM,
    MAL_USERNAME,
    PLAYBACK_AUTO_MARK,
    PLAYBACK_BACKEND,
    PLAYBACK_CATCH_UP,
    PLAYBACK_EXECUTABLE,
    PLAYBACK_PLAYER_CHOICE,
    PLAYBACK_PLAYER_PATHS,
    SettingsRepository,
)
from anical.domain.models import AiringStatus, AnimeDraft, WatchState
from anical.domain.playback import PlaybackMapping
from anical.net.http import NetworkError, Response
from anical.providers.anilist import AniListClient
from anical.sync.mal_sync import MalSyncService
from anical.sync.schedule_sync import fetch_schedules
from anical.ui.air_dates import AirDatesDialog
from anical.ui.display import DisplaySettings
from anical.ui.editor import AnimeEditorDialog
from anical.ui.playback_controller import PlaybackController
from anical.ui.preferences import PreferencesDialog
from anical.ui.show_page import ShowPage
from anical.ui.sync_controller import SyncController
from anical.ui.window import MainWindow


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

    def fetch_without_pacing(http, targets, now):
        return fetch_schedules(http, targets, now, client=AniListClient(http, sleep=lambda s: None))

    return SyncController(
        repo, settings, service, http, tmp_path / "covers", clock=clock, schedule_fetcher=fetch_without_pacing
    )


@pytest.fixture
def display(settings):
    settings.set(DISPLAY_TIMEZONE, "America/New_York")
    return DisplaySettings(settings)


@pytest.fixture
def playback(repo, settings, display, conn, tmp_path):
    settings.set(PLAYBACK_EXECUTABLE, str(Path(__file__).parent / "fake_ani_cli.py"))
    settings.set(PLAYBACK_PLAYER_PATHS["mpv"], str(Path(__file__).parent / "fake_mpv.py"))
    settings.set(PLAYBACK_PLAYER_CHOICE, "mpv")
    return PlaybackController(repo, PlaybackRepository(conn), settings, display, tmp_path / "run")


@pytest.fixture
def window(repo, controller, display, playback):
    win = MainWindow(application=None, repository=repo, sync_controller=controller, display=display, playback=playback)
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


def anilist_page(key, items):
    return json_response({"data": {"Page": {"pageInfo": {"hasNextPage": False}, key: items}}})


def use_public_list(settings):
    settings.set(MAL_CLIENT_ID, "cid")
    settings.set(MAL_MODE, "username")
    settings.set(MAL_USERNAME, "someone")


def test_empty_library_shows_empty_state(window):
    assert window.library_view._stack.get_visible_child_name() == "empty"


def test_library_lists_and_filters(window, repo):
    repo.create_anime(AnimeDraft(title="Frieren", airing_status=AiringStatus.COMPLETED, total_episodes=2))
    repo.create_anime(AnimeDraft(title="Ongoing Show", airing_status=AiringStatus.AIRING))
    window.library_view.reload()
    page = window.library_view
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


def test_show_page_links_to_myanimelist_only_with_an_id(window, repo, monkeypatch):
    def link_buttons(page):
        return [w for w in all_widgets(page) if isinstance(w, Gtk.Button) and w.has_css_class("cover-link")]

    assert link_buttons(ShowPage(window, repo.create_anime(AnimeDraft(title="Manual")))) == []
    [button] = link_buttons(ShowPage(window, repo.create_anime(AnimeDraft(title="Imported", mal_id=64084))))
    assert "Open MAL page" in widget_texts(button)
    opened = []
    monkeypatch.setattr(Gtk.UriLauncher, "launch", lambda launcher, *_: opened.append(launcher.get_uri()))
    button.emit("clicked")
    assert opened == ["https://myanimelist.net/anime/64084/"]


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
    now = clock()
    http.replies += [
        json_response(fixture_json("mal_animelist_page1.json")),
        json_response(fixture_json("mal_animelist_page2.json")),
        # AniList: link the two still-airing shows, then their schedule window.
        anilist_page(
            "media",
            [
                {"id": 5001, "idMal": 99001, "status": "RELEASING", "episodes": None},
                {"id": 5002, "idMal": 99002, "status": "NOT_YET_RELEASED", "episodes": 1},
            ],
        ),
        anilist_page("airingSchedules", [{"mediaId": 5001, "episode": 4, "airingAt": now + 3600}]),
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
    assert window.library_view._list_filter.get_parent().get_visible()
    (entry,) = ScheduleRepository(repo.connection, clock).calendar_entries(now, now + 86400)
    assert (entry.title, entry.number) == ("Ongoing Show", 4)

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
    assert controller.error == "MyAnimeList: couldn’t connect. Showing saved data."
    assert window._banner.get_revealed()
    assert settings.last_refresh_run() is None  # failed runs don't count
    assert [s.title for s in repo.list_summaries()] == ["Kept"]


def use_expired_account(settings):
    settings.set(MAL_CLIENT_ID, "cid")
    settings.set(MAL_MODE, "account")
    settings.set(MAL_USERNAME, "example_user")


def test_expired_login_asks_with_a_popup_the_size_of_the_wizard(window, controller, settings):
    from anical.ui.setup_wizard import WIZARD_HEIGHT, WIZARD_WIDTH

    use_expired_account(settings)  # MemoryTokenStore starts empty, so there's no token
    controller.sync()
    wait_until(lambda: not controller.busy)
    drain()
    dialog = window.get_visible_dialog()
    assert dialog is window._login_dialog and dialog is not None
    assert (dialog.get_content_width(), dialog.get_content_height()) == (WIZARD_WIDTH, WIZARD_HEIGHT)
    assert not window._banner.get_revealed()  # the popup replaces the banner
    assert controller.login_expired and controller.error is None
    assert settings.last_refresh_run() is None  # still not a successful refresh
    dialog.force_close()
    drain()


def test_not_now_leaves_a_banner_and_doesnt_ask_again(window, controller, settings):
    use_expired_account(settings)
    controller.sync()
    wait_until(lambda: not controller.busy)
    drain()
    window._login_dialog.force_close()
    drain()
    assert window._login_dialog is None
    assert window._banner.get_revealed() and "expired" in window._banner.get_title()
    assert window._banner.get_button_label() == "Log In…"

    controller.sync()
    wait_until(lambda: not controller.busy)
    drain()
    assert window._login_dialog is None  # the next refresh stays quiet

    window._on_banner_button()  # but the banner's button asks again
    assert window._login_dialog is not None
    window._login_dialog.force_close()
    drain()


def test_working_login_closes_the_popup_and_clears_the_reminder(window, controller, settings, http, clock):
    use_expired_account(settings)
    controller.sync()
    wait_until(lambda: not controller.busy)
    drain()
    assert window._login_dialog is not None

    controller._service._tokens.save(Token("access", "refresh", clock() + 99999, clock()))
    http.replies.append(json_response({"data": [], "paging": {}}))
    controller.sync()
    wait_until(lambda: not controller.busy)
    drain()
    assert not controller.login_expired
    assert window._login_dialog is None and not window._banner.get_revealed()


def login_dialog(controller):
    from anical.ui.login_expired import LoginExpiredDialog

    return LoginExpiredDialog(controller)


def test_login_popup_shows_what_it_will_log_in_with(controller, settings):
    use_expired_account(settings)
    dialog = login_dialog(controller)
    assert dialog._client_entry.get_text() == "cid"
    assert not dialog._register.get_visible()
    assert dialog._log_in.get_sensitive()
    account = dialog._client_entry.get_parent().get_row_at_index(2)
    assert (account.get_title(), account.get_subtitle()) == ("Account", "example_user")
    dialog.force_close()


def test_login_popup_collects_a_missing_client_id(controller, settings, monkeypatch):
    dialog = login_dialog(controller)  # a library with nothing set up, as in the self-test
    assert not dialog._log_in.get_sensitive() and dialog._register.get_visible()

    dialog._client_entry.set_text("  " + "a" * 32 + "  ")
    assert dialog._log_in.get_sensitive() and not dialog._register.get_visible()
    assert not dialog._status.get_visible()  # it looks right, so no warning

    started = []

    def refuse(on_finished):
        started.append(controller.config.client_id)
        raise OAuthError("port busy")

    monkeypatch.setattr(controller, "begin_login", refuse)
    dialog._on_log_in()
    assert started == ["a" * 32]  # saved before the login starts
    assert dialog._status.get_label() == "port busy" and dialog._log_in.get_sensitive()
    dialog.force_close()


def test_login_popup_only_focuses_the_client_id_when_it_needs_one(window, controller, settings):
    window.show_login_expired()
    drain()
    dialog = window._login_dialog
    assert dialog.get_focus() is dialog._client_entry
    dialog.force_close()
    drain()

    use_expired_account(settings)
    window.show_login_expired()
    drain()
    dialog = window._login_dialog
    assert dialog.get_focus() is dialog._log_in  # a filled-in field opens selected, one key from being replaced
    dialog.force_close()
    drain()


def test_login_popup_warns_about_an_unusual_client_id(controller, settings):
    dialog = login_dialog(controller)
    dialog._client_entry.set_text("my password")
    assert dialog._status.get_visible() and "32 letters" in dialog._status.get_label()
    dialog._client_entry.set_text("")
    assert not dialog._status.get_visible() and not dialog._log_in.get_sensitive()
    dialog.force_close()


def test_saving_a_client_id_doesnt_close_the_popup(window, controller):
    window.show_login_expired()
    drain()
    dialog = window._login_dialog
    controller.set_client_id("a" * 32)
    drain()
    assert window._login_dialog is dialog and window.get_visible_dialog() is dialog
    dialog.force_close()
    drain()


def test_not_now_keeps_a_typed_client_id_but_not_a_blank_one(window, controller, settings):
    use_expired_account(settings)
    window.show_login_expired()
    drain()
    window._login_dialog._client_entry.set_text("")
    window._login_dialog.force_close()
    drain()
    assert controller.config.client_id == "cid"

    window.show_login_expired()
    drain()
    window._login_dialog._client_entry.set_text("new-id")
    window._login_dialog.force_close()
    drain()
    assert controller.config.client_id == "new-id"


def test_account_without_a_client_id_asks_to_log_in(window, controller, settings, clock):
    settings.set(MAL_MODE, "account")
    settings.set(MAL_USERNAME, "example_user")
    controller.sync_if_due()  # there's nothing else to refresh, but the account still needs saying
    wait_until(lambda: not controller.busy)
    drain()
    assert controller.login_expired and window._login_dialog is not None
    assert not window._login_dialog._log_in.get_sensitive()
    window._login_dialog.force_close()
    drain()


def test_failed_renewal_is_recorded_and_doesnt_interrupt(window, controller, settings, http, clock):
    use_expired_account(settings)
    day_old = Token("access", "refresh", clock() + 99999, clock() - RENEW_AFTER_S)
    controller._service._tokens.save(day_old)
    http.replies += [json_response({"error": "invalid_grant"}, 400), json_response({"data": [], "paging": {}})]
    controller.sync()
    wait_until(lambda: not controller.busy)
    drain()
    assert "400 invalid_grant" in settings.get_state(MAL_RENEWAL_PROBLEM)
    assert not controller.login_expired and window._login_dialog is None
    assert controller.error is None and settings.last_refresh_run() is not None


def test_renewal_problem_is_kept_when_the_login_expires(window, controller, settings, http, clock):
    use_expired_account(settings)
    controller._service._tokens.save(Token("access", "refresh", clock() - 10, clock() - 99999))
    http.replies.append(json_response({"error": "invalid_grant"}, 400))
    controller.sync()
    wait_until(lambda: not controller.busy)
    drain()
    assert "400 invalid_grant" in settings.get_state(MAL_RENEWAL_PROBLEM)
    assert window._login_dialog is not None
    window._login_dialog.force_close()
    drain()


def test_expired_login_is_remembered_after_a_restart(controller, settings):
    from anical.db.settings import MAL_LOGIN_EXPIRED

    use_expired_account(settings)
    settings.set_state(MAL_LOGIN_EXPIRED, "1")
    assert controller.login_expired
    settings.set(MAL_MODE, "username")  # a public list has no login to expire
    assert not controller.login_expired


def test_preferences_offer_logging_in_again(controller, settings, display, playback, repo):
    from anical.db.settings import MAL_LOGIN_EXPIRED

    use_expired_account(settings)
    settings.set_state(MAL_LOGIN_EXPIRED, "1")
    dialog = PreferencesDialog(controller, display, playback, repo.connection, Path("/nonexistent"), lambda: None)
    drain()
    dialog.force_close()


@pytest.mark.parametrize("mode", [None, "username", "account"])
def test_preferences_build_in_every_mode(controller, settings, display, playback, repo, mode):
    settings.set(MAL_CLIENT_ID, "cid")
    settings.set(MAL_MODE, mode)
    settings.set(MAL_USERNAME, "someone" if mode else None)
    dialog = PreferencesDialog(controller, display, playback, repo.connection, Path("/tmp"), lambda: None)
    controller.emit("changed")  # rebuild path
    dialog.force_close()


def test_mal_page_explains_a_missing_client_id(controller, settings, display, playback, repo):
    dialog = PreferencesDialog(controller, display, playback, repo.connection, Path("/tmp"), lambda: None)
    texts = widget_texts(dialog._page)
    assert "Client ID Needed" in texts and "Available once a client ID is set" in texts
    assert [g.get_title() for g in dialog._groups] == ["Account", "API Client", "Sync"]  # the fix comes first

    controller.set_client_id("cid")  # emits changed, which rebuilds the page
    texts = widget_texts(dialog._page)
    assert "Client ID Needed" not in texts
    assert [g.get_title() for g in dialog._groups] == ["Account", "Sync", "API Client"]
    dialog.force_close()


def schedule_show(repo, window, *offsets_hours):
    """An airing show with one user-set airing per offset (hours from now)."""
    import time

    anime_id = repo.create_anime(AnimeDraft(title="Weekly Show", airing_status=AiringStatus.AIRING))
    now = int(time.time())
    window.schedule.set_user_airings(anime_id, [(i + 1, now + h * 3600) for i, h in enumerate(offsets_hours)])
    window.library_changed()
    return anime_id


def widget_texts(widget):
    """All label texts and row titles under `widget`, for asserting what is shown."""
    texts = []
    stack = [widget]
    while stack:
        current = stack.pop()
        if isinstance(current, Gtk.Label):
            texts.append(current.get_label())
        if isinstance(current, Adw.PreferencesRow):
            texts.append(current.get_title())
        child = current.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return texts


@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize("mode", ["day", "week", "month", "unwatched"])
def test_calendar_modes_render(window, repo, mode, compact):
    schedule_show(repo, window, -30, -1, 2)
    calendar = window.calendar_view
    calendar.set_property("compact", compact)
    calendar._modes.set_active_name(mode)
    texts = widget_texts(calendar)
    if mode == "unwatched":
        assert "Weekly Show" in texts and "Aired, Not Watched" in texts
        assert "Episode 1" in texts and "Episode 2" in texts
    elif mode != "month":
        assert any("Weekly Show" in t for t in texts if t)


def test_calendar_navigation_and_today(window):
    calendar = window.calendar_view
    first = calendar._title.get_label()
    calendar.navigate(1)
    assert calendar._title.get_label() != first
    calendar.navigate(0)
    assert calendar._title.get_label() == first


def test_marking_watched_from_calendar_updates_everything(window, repo):
    anime_id = schedule_show(repo, window, -3)
    (episode,) = repo.episodes(anime_id).episodes
    window.calendar_view._toggle_watched(episode.id)
    drain()
    assert repo.watch_state(episode.id) is WatchState.WATCHED
    window.calendar_view._modes.set_active_name("unwatched")
    assert "All Caught Up" in widget_texts(window.calendar_view)


def test_undated_airing_shows_are_listed(window, repo):
    repo.create_anime(AnimeDraft(title="No Dates Yet", airing_status=AiringStatus.AIRING))
    window.library_changed()
    texts = widget_texts(window.calendar_view)
    assert "Airing, Date Unknown" in texts and "No Dates Yet" in texts


def test_air_dates_dialog_validates_and_saves(window, repo):
    anime_id = repo.create_anime(AnimeDraft(title="Manual", airing_status=AiringStatus.AIRING, total_episodes=4))
    saved = []
    dialog = AirDatesDialog(window.schedule, window.display, repo.get_anime(anime_id), 1, lambda: saved.append(1))
    dialog._on_save()  # no time entered
    assert not saved and dialog._time.has_css_class("error")
    dialog._date.set_text("2026-11-01")
    dialog._time.set_text("20:00")
    dialog._repeat.set_active(True)
    dialog._on_save()
    assert saved
    airings = window.schedule.episode_airings(anime_id)
    assert len(airings) == 4 and window.schedule.has_user_airings(anime_id)

    page = ShowPage(window, anime_id)
    assert page._actions.lookup_action("clear-air-dates").get_enabled()
    page._on_clear_air_dates()
    assert not window.schedule.has_user_airings(anime_id)


def test_unwatched_mark_all_and_markup_safe_titles(window, repo):
    import time

    anime_id = repo.create_anime(AnimeDraft(title="Tom & Jerry <Special>", airing_status=AiringStatus.AIRING))
    now = int(time.time())
    window.schedule.set_user_airings(anime_id, [(1, now - 7200), (2, now - 3600)])
    window.library_changed()
    window.calendar_view._modes.set_active_name("unwatched")
    assert "Tom &amp; Jerry &lt;Special&gt;" in widget_texts(window.calendar_view)
    window.calendar_view._mark_watched([e.id for e in repo.episodes(anime_id).episodes])
    drain()
    assert repo.episodes(anime_id).watched_count == 2


def test_show_page_count_does_not_invent_a_total(window, repo):
    unknown = repo.create_anime(AnimeDraft(title="Open-ended"))
    for _ in range(3):
        repo.add_episode(unknown)
    assert ShowPage(window, unknown)._count_label.get_label() == "0 watched"
    known = repo.create_anime(AnimeDraft(title="Finite", total_episodes=12))
    assert ShowPage(window, known)._count_label.get_label() == "0 of 12 watched"


def test_calendar_can_use_english_titles(window, repo, display):
    import time

    anime_id = repo.create_anime(
        AnimeDraft(title="Kusuriya no Hitorigoto", title_en="The Apothecary Diaries", airing_status=AiringStatus.AIRING)
    )
    plain = repo.create_anime(AnimeDraft(title="Only Romaji", airing_status=AiringStatus.AIRING))
    now = int(time.time())  # not an hour ahead: after 23:00 in the display zone that is tomorrow
    for show in (anime_id, plain):
        window.schedule.set_user_airings(show, [(1, now)])
    window.calendar_view._modes.set_active_name("day")
    window.library_changed()
    assert "Kusuriya no Hitorigoto" in widget_texts(window.calendar_view)

    display.set(DISPLAY_ENGLISH_TITLES, True)  # re-renders via the display's changed signal
    texts = widget_texts(window.calendar_view)
    assert "The Apothecary Diaries" in texts and "Kusuriya no Hitorigoto" not in texts
    assert "Only Romaji" in texts  # falls back when there's no English title


# Playback (fake ani-cli, real Gio.Subprocess)


# Unpackaged, AniCal's playback helpers are scripts, which Windows can't run;
# the Windows build's self-test and ani-cli harness cover playback there.
launches_ani_cli = pytest.mark.skipif(os.name == "nt", reason="needs the packaged helpers on Windows")


def play_and_wait(playback, episode_id):
    reports = []
    handler = playback.connect("finished", lambda _p, report: reports.append(report))
    assert playback.play(episode_id) is None
    wait_until(lambda: reports, timeout=15)
    playback.disconnect(handler)
    return reports[0]


def first_episode(repo, title="Show", **draft):
    anime_id = repo.create_anime(AnimeDraft(title=title, total_episodes=3, **draft))
    return anime_id, repo.episodes(anime_id).episodes[0]


@launches_ani_cli
def test_playing_to_the_end_marks_watched(playback, repo, conn, monkeypatch):
    monkeypatch.setenv("FAKE_PROGRESS", '{"reason":"eof","percent":96.5,"position":1380,"duration":1420}')
    _, episode = first_episode(repo)
    report = play_and_wait(playback, episode.id)
    assert report.result.outcome.value == "watched" and report.marked
    assert repo.watch_state(episode.id) is WatchState.WATCHED
    (source,) = conn.execute("SELECT source FROM watch_event WHERE episode_id = ?", (episode.id,)).fetchone()
    assert source == "playback"
    assert conn.execute("SELECT end_reason FROM playback_session").fetchone()[0] == "eof"
    assert not list((Path(playback._runtime_dir)).glob("session-*"))  # cleaned up


@pytest.mark.skipif(not hasattr(os, "openpty"), reason="POSIX only")
def test_ani_cli_gets_a_terminal_so_it_keeps_our_picker(playback, repo, monkeypatch, tmp_path):
    # Without a terminal on stdin, ani-cli swaps ANI_CLI_MENU for rofi or dmenu.
    log = tmp_path / "fake.json"
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.setenv("FAKE_PROGRESS", '{"reason":"eof"}')
    _, episode = first_episode(repo)
    play_and_wait(playback, episode.id)
    assert json.loads(log.read_text())["stdin_is_terminal"] is True


@launches_ani_cli
def test_stopping_early_does_not_mark_watched(playback, repo, monkeypatch):
    monkeypatch.setenv("FAKE_PROGRESS", '{"reason":"quit","percent":42,"position":600,"duration":1420}')
    _, episode = first_episode(repo)
    report = play_and_wait(playback, episode.id)
    assert report.result.outcome.value == "partial" and round(report.result.percent) == 42
    assert repo.watch_state(episode.id) is WatchState.UNWATCHED


@launches_ani_cli
def test_auto_mark_can_be_turned_off(playback, repo, settings, monkeypatch):
    settings.set(PLAYBACK_AUTO_MARK, False)
    monkeypatch.setenv("FAKE_PROGRESS", '{"reason":"eof","percent":99}')
    _, episode = first_episode(repo)
    report = play_and_wait(playback, episode.id)
    assert report.result.outcome.value == "watched" and not report.marked
    assert repo.watch_state(episode.id) is WatchState.UNWATCHED


@launches_ani_cli
def test_ambiguous_search_asks_once_then_remembers(playback, repo, monkeypatch):
    monkeypatch.setenv("FAKE_RESULTS", "Show|Show Season 2")
    monkeypatch.setenv("FAKE_PROGRESS", '{"reason":"quit","percent":5}')
    anime_id, episode = first_episode(repo, title="Show S2")
    requests = []

    def answer(_p, request):
        requests.append(request)
        request.respond(next(i for i, t in request.choices if t == "Show Season 2"))

    handler = playback.connect("pick-requested", answer)
    play_and_wait(playback, episode.id)
    assert len(requests) == 1 and requests[0].query == "Show S2"
    assert PlaybackRepository(repo.connection).mapping(anime_id).result_title == "Show Season 2"

    play_and_wait(playback, episode.id)
    assert len(requests) == 1  # second time the picker chose by itself
    playback.disconnect(handler)


@launches_ani_cli
def test_cancelled_pick_and_unreleased_episode(playback, repo, monkeypatch):
    monkeypatch.setenv("FAKE_RESULTS", "A|B")
    handler = playback.connect("pick-requested", lambda _p, request: request.respond(None))
    _, episode = first_episode(repo)
    assert play_and_wait(playback, episode.id).result.outcome.value == "cancelled"
    playback.disconnect(handler)

    monkeypatch.delenv("FAKE_RESULTS")
    monkeypatch.setenv("FAKE_EPISODES", "0")
    assert play_and_wait(playback, episode.id).result.outcome.value == "episode_not_found"


@launches_ani_cli
def test_one_playback_at_a_time_and_missing_ani_cli(playback, repo, settings, monkeypatch):
    monkeypatch.setenv("FAKE_PROGRESS", '{"reason":"eof"}')
    _, episode = first_episode(repo)
    assert playback.play(episode.id) is None
    assert playback.play(episode.id).startswith("Already playing")
    wait_until(lambda: playback.playing is None, timeout=15)

    settings.set(PLAYBACK_EXECUTABLE, "/nonexistent/ani-cli")
    assert "wasn’t found" in playback.play(episode.id)


@launches_ani_cli
def test_play_buttons_on_show_page_and_calendar(window, repo, monkeypatch):
    import time

    monkeypatch.setenv("FAKE_PROGRESS", '{"reason":"eof"}')
    anime_id = repo.create_anime(AnimeDraft(title="Airing", airing_status=AiringStatus.AIRING, total_episodes=2))
    now = int(time.time())
    window.schedule.set_user_airings(anime_id, [(1, now - 3600), (2, now + 86400)])
    page = ShowPage(window, anime_id)
    texts = widget_texts(page)
    assert "Play Episode 1" in texts  # the next aired, unwatched episode
    window.play_episode(repo.episodes(anime_id).episodes[0].id)
    wait_until(lambda: window.playback.playing is None, timeout=15)
    drain()
    assert repo.episodes(anime_id).watched_count == 1
    assert "Play Episode 1" not in widget_texts(ShowPage(window, anime_id))  # episode 2 hasn't aired


def test_ani_cli_history_hint(window, repo, private_ani_cli_history):
    anime_id = repo.create_anime(AnimeDraft(title="Overgeared", total_episodes=12))
    assert "ani-cli" not in widget_texts(ShowPage(window, anime_id))  # no chosen result, so no match is claimed
    PlaybackRepository(repo.connection).save_mapping(PlaybackMapping(anime_id, "Overgeared", "Overgeared"))
    private_ani_cli_history.write_text("5\tovergeared-10469\tOvergeared\n")
    page = ShowPage(window, anime_id)
    texts = widget_texts(page)
    assert "Last launched in ani-cli: Episode 5" in texts and "Mark 1–4 Watched" in texts
    assert repo.episodes(anime_id).watched_count == 0  # only a hint until the user clicks


def test_long_shows_show_a_window_around_progress(window, repo):
    anime_id = repo.create_anime(AnimeDraft(title="Very Long", total_episodes=300))
    repo.set_watch_state([e.id for e in repo.episodes(anime_id).episodes[:200]], WatchState.WATCHED)
    page = ShowPage(window, anime_id)
    texts = widget_texts(page)
    assert "Episode 201" in texts and "Episode 191" in texts and "Episode 1" not in texts
    assert "Show 190 Earlier Episodes" in texts and "Show 50 Later Episodes" in texts
    assert "200 of 300 watched" in texts  # counts still cover everything
    page._expand_episodes()
    texts = widget_texts(page)
    assert "Episode 1" in texts and "Episode 300" in texts


def test_keyboard_actions_and_shortcuts_dialog(window):
    from anical.app import shortcuts_dialog

    first = window.calendar_view._title.get_label()
    window.activate_action("win.show-library", None)
    assert window._stack.get_visible_child_name() == "library"
    window.activate_action("win.calendar-next", None)
    assert window._stack.get_visible_child_name() == "calendar"
    assert window.calendar_view._title.get_label() != first
    window.activate_action("win.calendar-today", None)
    assert window.calendar_view._title.get_label() == first
    shortcuts_dialog()  # builds without errors


# First-run setup wizard


@pytest.fixture
def wizard_parts(controller, playback, settings, tmp_path, window):
    from anical.ui.setup_wizard import SetupWizard

    finished = []
    made = []

    def make():
        wizard = SetupWizard(
            controller, playback, settings, tmp_path / "run", on_finished=lambda: finished.append(True)
        )
        wizard.present(window)  # as the app does
        made.append(wizard)
        return wizard

    yield make, finished
    for wizard in made:
        wizard.force_close()
    drain()


def test_wizard_counts_steps_and_goes_back(wizard_parts, controller, settings):
    from anical.db.settings import SETUP_SEEN

    make, finished = wizard_parts
    wizard = make()
    assert wizard._title.get_subtitle() == "Step 1 of 4"
    assert not wizard._back.get_visible() and wizard._next.get_label() == "Let’s Go"
    wizard._on_next()
    assert wizard._title.get_subtitle() == "Step 2 of 4" and wizard._back.get_visible()
    assert wizard._next.get_label() == "Skip"  # nothing entered yet
    wizard._client_entry.set_text("0123456789abcdef0123456789abcdef")
    assert wizard._next.get_label() == "Next" and "looks like a client ID" in wizard._client_hint.get_label()
    wizard._on_next()  # leaving the step saves the client ID
    assert controller.config.client_id == "0123456789abcdef0123456789abcdef"
    assert wizard._title.get_subtitle() == "Step 3 of 4"
    wizard._back.emit("clicked")
    assert wizard._title.get_subtitle() == "Step 2 of 4"  # back works until Finish
    wizard.go_to(4)
    assert wizard._next.get_label() == "Finish"
    wizard._on_next()
    assert finished == [True] and settings.get(SETUP_SEEN) is True
    wizard._go(0)
    assert wizard._title.get_subtitle() == "Step 4 of 4"  # finished: no more navigation


def test_client_id_is_trimmed_wherever_it_comes_from(wizard_parts, controller, settings):
    client_id = "0123456789abcdef0123456789abcdef"
    make, _ = wizard_parts
    wizard = make()
    wizard.go_to(2)
    wizard._client_entry.set_text(f" \t{client_id}\n ")  # pasted with whitespace around it
    assert "looks like a client ID" in wizard._client_hint.get_label()
    wizard._on_next()
    assert settings.get(MAL_CLIENT_ID) == client_id

    controller.set_client_id(f"  {client_id}  ")  # Preferences saves through this
    assert settings.get(MAL_CLIENT_ID) == client_id
    controller.set_client_id("   ")
    assert settings.get(MAL_CLIENT_ID) is None

    settings.set(MAL_CLIENT_ID, f"{client_id} \n")  # stored with whitespace by something else
    assert controller.config.client_id == client_id


def test_wizard_rerun_shows_what_is_already_set_up(wizard_parts, controller, settings):
    settings.set(MAL_CLIENT_ID, "fedcba9876543210fedcba9876543210")
    settings.set(MAL_MODE, "username")
    settings.set(MAL_USERNAME, "someone")
    make, _ = wizard_parts
    wizard = make()
    assert wizard._client_entry.get_text() == "fedcba9876543210fedcba9876543210"  # pre-filled
    wizard.go_to(2)
    assert wizard._next.get_label() == "Next"
    wizard.go_to(3)
    assert "Importing someone’s public list" in widget_texts(wizard._sign_in_box)
    assert wizard._next.get_label() == "Next"
    assert "Got it" in wizard._bubbles["sign-in"].get_label()


def test_wizard_sign_in_step_without_client_id_points_back(wizard_parts):
    make, _ = wizard_parts
    wizard = make()
    wizard.go_to(3)
    texts = widget_texts(wizard._sign_in_box)
    assert "Add a Client ID" in texts and wizard._next.get_label() == "Skip"


def test_wizard_playback_step_checks_and_installs(wizard_parts, playback, settings, monkeypatch):
    import anical.ui.setup_wizard as setup_wizard

    opened = []
    monkeypatch.setattr(setup_wizard, "open_install_terminal", lambda folder, programs: opened.append(programs))
    make, _ = wizard_parts
    wizard = make()
    wizard.go_to(4)
    assert wizard._all_found  # the fixtures' fake ani-cli and mpv
    assert "All set" in wizard._bubbles["playback"].get_label()

    settings.set(PLAYBACK_PLAYER_PATHS["mpv"], "/nonexistent/mpv")
    wizard._recheck()
    mpv = next(p for p in wizard._players if p.install_key == "mpv")
    assert not mpv.found and mpv.install.get_visible()
    assert "Last one" in wizard._bubbles["playback"].get_label()
    mpv.install.emit("clicked")
    assert opened == [["mpv"]]


def test_wizard_suggests_the_systems_player_and_saves_a_choice(wizard_parts, playback, settings, monkeypatch):
    import anical.platform.anicli as anicli
    from anical.domain.playback import Player
    from anical.platform import System

    monkeypatch.setattr(anicli, "current", lambda: System.MACOS)
    settings.set(PLAYBACK_PLAYER_CHOICE, None)
    for key in PLAYBACK_PLAYER_PATHS.values():
        settings.set(key, "/nonexistent/player")
    make, _ = wizard_parts
    wizard = make()
    wizard.go_to(4)

    assert [p.name for p in wizard._players] == ["IINA", "mpv", "VLC"]
    assert wizard._players[0].row.get_title() == "IINA (Recommended)"
    assert playback.chosen_player is Player.IINA  # none is installed: the suggestion
    assert wizard._players[0].choice.get_active()
    assert settings.get(PLAYBACK_PLAYER_CHOICE) is None  # showing it isn't choosing it
    assert "IINA" in wizard._bubbles["playback"].get_label()

    wizard._players[2].choice.set_active(True)
    assert settings.get(PLAYBACK_PLAYER_CHOICE) == "vlc"
    assert playback.chosen_player is Player.VLC
    assert wizard._players[2].choice.get_active()


def test_without_a_choice_the_first_installed_player_is_used(playback, settings, tmp_path, monkeypatch):
    import anical.platform.anicli as anicli
    from anical.domain.playback import Player
    from anical.platform import System

    monkeypatch.setattr(anicli, "current", lambda: System.WINDOWS)
    settings.set(PLAYBACK_PLAYER_CHOICE, None)
    settings.set(PLAYBACK_PLAYER_PATHS["vlc"], "/nonexistent/vlc.exe")
    assert playback.chosen_player is Player.MPV  # VLC is suggested here, but only mpv is installed
    vlc = tmp_path / "vlc.exe"
    vlc.touch()
    settings.set(PLAYBACK_PLAYER_PATHS["vlc"], str(vlc))
    assert playback.chosen_player is Player.VLC
    settings.set(PLAYBACK_PLAYER_CHOICE, "iina")  # from a Mac's backup: Windows doesn't offer it
    assert playback.chosen_player is Player.VLC
    settings.set(PLAYBACK_PLAYER_CHOICE, "mpv")
    assert playback.chosen_player is Player.MPV


def test_preferences_shows_the_location_of_the_chosen_player(
    controller, display, playback, repo, settings, monkeypatch
):
    import anical.platform.anicli as anicli
    from anical.platform import System

    monkeypatch.setattr(anicli, "current", lambda: System.LINUX)
    dialog = PreferencesDialog(controller, display, playback, repo.connection, Path("/tmp"), lambda: None)
    rows = []
    group = SimpleNamespace(add=rows.append)
    dialog._player_rows(group, {"mpv": "", "vlc": ""})
    choice, mpv_entry, mpv_status, vlc_entry, vlc_status = rows
    assert choice.get_selected() == 0
    assert mpv_entry.get_visible() and mpv_status.get_visible()
    assert not vlc_entry.get_visible() and not vlc_status.get_visible()
    assert "mpv is recommended" in choice.get_subtitle()
    choice.set_selected(1)
    assert settings.get(PLAYBACK_PLAYER_CHOICE) == "vlc"
    assert vlc_entry.get_visible() and not mpv_entry.get_visible()


def test_required_programs_add_git_bash_on_windows(playback, monkeypatch):
    import anical.ui.playback_controller as playback_controller
    from anical.platform import System

    monkeypatch.setattr(playback_controller, "current", lambda: System.LINUX)  # whatever system runs the tests
    assert [p.name for p in playback.required_programs()] == ["ani-cli"]
    monkeypatch.setattr(playback_controller, "current", lambda: System.WINDOWS)
    programs = playback.required_programs()
    assert [p.name for p in programs] == ["Git Bash", "ani-cli"]  # Git Bash first: ani-cli runs in it
    assert [p.install_key for p in programs] == ["git", "ani-cli"]  # what the install scripts accept


def test_double_click_plays_and_a_single_click_still_opens(monkeypatch):
    from anical.ui.gestures import on_row_double_click

    monkeypatch.setattr(Gtk.Settings.get_default().props, "gtk_double_click_time", 10)
    box = Gtk.ListBox()
    row = Adw.ActionRow(title="Show", activatable=True)
    box.append(row)
    window = Gtk.Window(child=box)
    opened, played = [], []
    row.connect("activated", lambda *_: opened.append(1))
    gesture = on_row_double_click(row, lambda: played.append(1))

    gesture.emit("pressed", 1, 0.0, 0.0)
    gesture.emit("released", 1, 0.0, 0.0)
    gesture.emit("pressed", 2, 0.0, 0.0)  # within the double-click time: play, don't open
    gesture.emit("released", 2, 0.0, 0.0)
    GLib.usleep(30_000)
    drain()
    assert played == [1] and opened == []

    gesture.emit("pressed", 1, 0.0, 0.0)
    gesture.emit("released", 1, 0.0, 0.0)
    wait_until(lambda: opened)  # a single click opens, once the double-click time has passed
    assert played == [1]
    window.destroy()


def test_typing_sheet_matches_its_layout():
    from gi.repository import Gdk

    from anical.ui.login_expired import TYPING

    sheet = Gdk.Texture.new_from_filename(str(TYPING.path))
    rows = -(-TYPING.frames // TYPING.columns)
    assert (sheet.get_width(), sheet.get_height()) == (TYPING.columns * TYPING.frame_w, rows * TYPING.frame_h)


def test_theatre_sheet_matches_its_layout():
    from gi.repository import Gdk

    from anical.ui import playing_popup

    sheet = Gdk.Texture.new_from_filename(str(playing_popup.theatre_path()))
    rows = -(-playing_popup.FRAMES // playing_popup.COLUMNS)
    assert (sheet.get_width(), sheet.get_height()) == (
        playing_popup.COLUMNS * playing_popup.FRAME_W,
        rows * playing_popup.FRAME_H,
    )


@launches_ani_cli
def test_play_show_plays_the_next_episode_with_the_playing_card(window, repo, monkeypatch):
    monkeypatch.setenv("FAKE_PROGRESS", '{"reason":"eof"}')
    anime_id, episode = first_episode(repo)
    window.play_show(anime_id)
    assert window._playing.showing and "Episode 1" in window._playing._episode.get_label()
    wait_until(lambda: window.playback.playing is None, timeout=15)
    drain()
    assert not window._playing.showing  # hidden once playback ends
    assert repo.watch_state(episode.id) is WatchState.WATCHED


def test_play_show_with_nothing_left_to_play(window, repo):
    anime_id, _ = first_episode(repo)
    repo.set_watch_state([e.id for e in repo.episodes(anime_id).episodes], WatchState.WATCHED)
    window.play_show(anime_id)
    assert not window._playing.showing and window.playback.playing is None


def test_week_card_plays_on_double_click_and_has_a_context_menu(window, repo, monkeypatch):
    import time

    from anical.ui import gestures

    anime_id = repo.create_anime(AnimeDraft(title="Card", airing_status=AiringStatus.AIRING, total_episodes=2))
    window.schedule.set_user_airings(anime_id, [(1, int(time.time()) - 3600)])
    window.library_changed()
    window.calendar_view.show_mode("week")
    drain()
    cards = [w for w in all_widgets(window.calendar_view) if w.has_css_class("cal-card")]
    (card,) = [c for c in cards if "Card" in c.get_tooltip_text()]
    assert "Double-click to play" in card.get_tooltip_text()
    played, opened = [], []
    monkeypatch.setattr(window, "play_episode", played.append)
    monkeypatch.setattr(window, "show_anime", opened.append)

    card.emit("activate")  # Enter or Space
    assert played == [repo.episodes(anime_id).episodes[0].id]

    # Newest first: the context menu's gesture, then the double-click's, then the button's own.
    context, double = [c for c in card.observe_controllers() if isinstance(c, Gtk.GestureClick)][:2]
    double.emit("pressed", 1, 1.0, 1.0)
    double.emit("released", 1, 1.0, 1.0)
    drain()
    assert len(played) == 1 and opened == []  # a single click does nothing
    double.emit("pressed", 2, 1.0, 1.0)
    assert len(played) == 2

    context.emit("pressed", 1, 1.0, 1.0)  # no current event: taken as a secondary click
    menus = [w for w in all_widgets(card) if isinstance(w, Gtk.PopoverMenu)]
    assert len(menus) == 1
    menus[0].popdown()
    drain()

    monkeypatch.setattr(gestures, "current", lambda: gestures.System.MACOS)
    assert gestures.context_click_hint() == "Control-click"


def _unwatched_third_episode(window, repo):
    """A show with episode 1 watched and episode 3 aired, on the calendar."""
    import time

    anime_id = repo.create_anime(AnimeDraft(title="Catch Up", airing_status=AiringStatus.AIRING, total_episodes=3))
    one, two, three = repo.episodes(anime_id).episodes
    repo.set_watch_state([one.id], WatchState.WATCHED)
    window.schedule.set_user_airings(anime_id, [(3, int(time.time()) - 3600)])
    window.library_changed()
    drain()
    return anime_id, two, three


def _calendar_rows_for(window, title):
    window.calendar_view.show_mode("unwatched")
    drain()
    rows = [w for w in all_widgets(window.calendar_view) if isinstance(w, Adw.ActionRow)]
    return [r for r in rows if r.get_title().startswith(title)]


def test_calendar_double_click_catches_up_and_says_so(window, repo, monkeypatch):
    anime_id, two, three = _unwatched_third_episode(window, repo)
    played, toasts = [], []
    monkeypatch.setattr(window.playback, "play", lambda episode_id: played.append(episode_id))
    monkeypatch.setattr(window, "toast", toasts.append)

    window.play_from_calendar(anime_id, three.id)
    assert played == [two.id]
    assert toasts == ["Playing Episode 2, the first one you haven’t watched"]

    window.play_from_calendar(anime_id, two.id)  # already the first unwatched: no message
    assert played == [two.id, two.id] and len(toasts) == 1


def test_calendar_catch_up_stays_within_the_show(window, repo, monkeypatch):
    _unwatched_third_episode(window, repo)  # another season, with an earlier episode unwatched
    later = repo.create_anime(AnimeDraft(title="Next Season", airing_status=AiringStatus.AIRING, total_episodes=1))
    (episode,) = repo.episodes(later).episodes
    played = []
    monkeypatch.setattr(window.playback, "play", lambda episode_id: played.append(episode_id))
    window.play_from_calendar(later, episode.id)
    assert played == [episode.id]


def test_calendar_catch_up_can_be_turned_off(window, repo, settings, monkeypatch):
    anime_id, _two, three = _unwatched_third_episode(window, repo)
    settings.set(PLAYBACK_CATCH_UP, False)
    played, toasts = [], []
    monkeypatch.setattr(window.playback, "play", lambda episode_id: played.append(episode_id))
    monkeypatch.setattr(window, "toast", toasts.append)
    window.play_from_calendar(anime_id, three.id)
    assert played == [three.id] and toasts == []


def test_calendar_catch_up_stays_quiet_when_playback_cannot_start(window, repo, monkeypatch):
    anime_id, _two, three = _unwatched_third_episode(window, repo)
    toasts = []
    monkeypatch.setattr(window.playback, "play", lambda _episode_id: "ani-cli wasn’t found")
    monkeypatch.setattr(window, "toast", toasts.append)
    window.play_from_calendar(anime_id, three.id)
    assert toasts == ["ani-cli wasn’t found"]


def test_calendar_rows_catch_up_on_double_click_but_their_play_button_is_exact(window, repo, monkeypatch):
    _anime_id, two, three = _unwatched_third_episode(window, repo)
    played = []
    monkeypatch.setattr(window.playback, "play", lambda episode_id: played.append(episode_id))
    (row,) = _calendar_rows_for(window, "Episode 3")
    # Newest first: the context menu's gesture, then the double-click's.
    _context, double = [c for c in row.observe_controllers() if isinstance(c, Gtk.GestureClick)][:2]
    double.emit("pressed", 2, 1.0, 1.0)
    assert played == [two.id]

    play = next(w for w in all_widgets(row) if isinstance(w, Gtk.Button) and w.get_tooltip_text() == "Play")
    play.emit("clicked")
    assert played == [two.id, three.id]


def test_catch_up_switch_saves_and_stays_visible_for_link_or_command(controller, display, playback, repo, settings):
    settings.set(PLAYBACK_BACKEND, "external")  # hides the groups that only apply to ani-cli
    dialog = PreferencesDialog(controller, display, playback, repo.connection, Path("/tmp"), lambda: None)
    page = dialog._playback_page()  # held: the rows lose their parents if the page is collected
    switches = [w for w in all_widgets(page) if isinstance(w, Adw.SwitchRow)]
    (switch,) = [w for w in switches if w.get_title() == "Catch Up From the Calendar"]
    assert switch.get_active() and playback.config.catch_up
    assert switch.get_ancestor(Adw.PreferencesGroup.__gtype__).get_visible()
    switch.set_active(False)
    assert settings.get(PLAYBACK_CATCH_UP) is False and not playback.config.catch_up


def test_large_scene_stops_at_whichever_side_reaches_75_percent_first():
    from anical.ui.playing_popup import FRAME_H, FRAME_W, large_scene_size

    # A very wide window: height is the limit, and the sides are left empty.
    width, height = large_scene_size(3000, 800, 16, 100)
    assert height + 100 <= 600 and width + 16 < 3000 * 0.75
    assert abs(height - width * FRAME_H / FRAME_W) <= 1  # never stretched

    # A very tall window: width is the limit.
    width, height = large_scene_size(800, 3000, 16, 100)
    assert width + 16 == 600 and height + 100 < 3000 * 0.75
    assert abs(height - width * FRAME_H / FRAME_W) <= 1


def test_large_scene_is_never_smaller_than_the_small_one():
    from anical.ui.playing_popup import SCENE_HEIGHT, SCENE_WIDTH, large_scene_size

    assert large_scene_size(320, 300, 16, 100) == (SCENE_WIDTH, SCENE_HEIGHT)


def test_playing_card_goes_in_the_corner_or_large_in_the_center(window, display):
    popup = window._playing
    popup.present("A Show, Episode 1")

    x, y, width, height = popup.card_rectangle(1200, 800)
    assert (x + width, y + height) == (1200 - 12, 800 - 12)

    display.set(DISPLAY_PLAYING_SIZE, "large")  # applies straight away, through display's changed signal
    x, y, width, height = popup.card_rectangle(1200, 800)
    assert abs(x - (1200 - width) / 2) <= 1 and abs(y - (800 - height) / 2) <= 1
    assert width <= 900 and height <= 600 and (width > 800 or height > 550)

    wide = popup.card_rectangle(3000, 800)
    assert wide[3] <= 600 and wide[2] < 3000 * 0.75

    display.set(DISPLAY_PLAYING_SIZE, "small")
    assert popup.card_rectangle(1200, 800)[2] < 400


def test_playing_size_setting_rejects_unknown_values_and_preferences_saves_it(
    controller, display, playback, repo, settings
):
    settings.set(DISPLAY_PLAYING_SIZE, "enormous")
    assert display.playing_size == "small"
    dialog = PreferencesDialog(controller, display, playback, repo.connection, Path("/tmp"), lambda: None)
    page = dialog._playback_page()  # held: the rows lose their parents if the page is collected
    (row,) = [w for w in all_widgets(page) if isinstance(w, Adw.ComboRow) and w.get_title() == "Playing Card Size"]
    row.set_selected(1)
    assert settings.get(DISPLAY_PLAYING_SIZE) == "large" and display.playing_size == "large"


def test_only_the_card_takes_clicks_around_the_large_card(window, display):
    display.set(DISPLAY_PLAYING_SIZE, "large")
    popup = window._playing
    popup.present("A Show, Episode 1")
    window.present()
    wait_until(lambda: popup.get_width() > 0 and popup._card.get_mapped())
    overlay = popup.get_parent()
    x, y, width, height = popup.card_rectangle(popup.get_width(), popup.get_height())

    in_card = overlay.pick(x + width / 2, y + height / 2, Gtk.PickFlags.DEFAULT)
    assert in_card.is_ancestor(popup)
    outside = overlay.pick(2.0, popup.get_height() - 2.0, Gtk.PickFlags.DEFAULT)
    assert outside is None or not (outside is popup or outside.is_ancestor(popup))


def all_widgets(widget):
    found, stack = [], [widget]
    while stack:
        current = stack.pop()
        found.append(current)
        child = current.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return found


@launches_ani_cli
def test_playing_card_and_messages_use_english_titles_when_chosen(window, repo, settings, monkeypatch):
    monkeypatch.setenv("FAKE_PROGRESS", '{"reason":"eof"}')
    anime_id, _ = first_episode(repo, title="Sousou no Frieren", title_en="Frieren: Beyond Journey's End")
    settings.set(DISPLAY_ENGLISH_TITLES, True)
    window.play_show(anime_id)
    assert window._playing._episode.get_label() == "Frieren: Beyond Journey's End, Episode 1"
    wait_until(lambda: window.playback.playing is None, timeout=15)
    drain()

    settings.set(DISPLAY_ENGLISH_TITLES, False)
    window.play_show(anime_id)  # episode 1 was marked watched; episode 2 is next
    assert window._playing._episode.get_label() == "Sousou no Frieren, Episode 2"
    wait_until(lambda: window.playback.playing is None, timeout=15)
    drain()

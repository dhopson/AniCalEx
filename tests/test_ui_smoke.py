"""Builds the real widgets against a temporary library. Skipped without a display."""

import json
import os
from pathlib import Path

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

if not Gtk.init_check():
    pytest.skip("no display available", allow_module_level=True)
Adw.init()

from helpers import FakeHttp, MemoryTokenStore, fixture_json, json_response

from anical.db.playback import PlaybackRepository
from anical.db.schedule import ScheduleRepository
from anical.db.settings import (
    DISPLAY_ENGLISH_TITLES,
    DISPLAY_TIMEZONE,
    MAL_CLIENT_ID,
    MAL_MODE,
    MAL_USERNAME,
    PLAYBACK_AUTO_MARK,
    PLAYBACK_EXECUTABLE,
    PLAYBACK_PLAYER,
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
def playback(repo, settings, conn, tmp_path):
    settings.set(PLAYBACK_EXECUTABLE, str(Path(__file__).parent / "fake_ani_cli.py"))
    settings.set(PLAYBACK_PLAYER, str(Path(__file__).parent / "fake_mpv.py"))
    return PlaybackController(repo, PlaybackRepository(conn), settings, tmp_path / "run")


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
    assert all(p.found for p in wizard._programs)  # the fixtures' fake ani-cli and mpv
    assert "All set" in wizard._bubbles["playback"].get_label()

    settings.set(PLAYBACK_PLAYER, "/nonexistent/mpv")
    wizard._recheck()
    mpv = next(p for p in wizard._programs if p.name == "mpv")
    assert not mpv.found and mpv.install.get_visible()
    assert "Let’s make sure" in wizard._bubbles["playback"].get_label()
    mpv.install.emit("clicked")
    assert opened == [["mpv"]]


def test_required_programs_add_git_bash_on_windows(playback, monkeypatch):
    import anical.ui.playback_controller as playback_controller
    from anical.platform import System

    assert [p.name for p in playback.required_programs()] == ["ani-cli", "mpv"]
    monkeypatch.setattr(playback_controller, "current", lambda: System.WINDOWS)
    programs = playback.required_programs()
    assert [p.name for p in programs] == ["Git Bash", "ani-cli", "mpv"]  # Git Bash first: ani-cli runs in it
    assert [p.install_key for p in programs] == ["git", "ani-cli", "mpv"]  # what the install scripts accept

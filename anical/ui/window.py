from __future__ import annotations

from pathlib import Path

from gi.repository import Adw, Gio, GObject, Gtk

from anical.db.playback import PlaybackRepository
from anical.db.repository import LibraryRepository
from anical.domain.models import WatchState
from anical.domain.playback import Outcome
from anical.db.schedule import ScheduleRepository
from anical.ui.calendar_view import CalendarView
from anical.ui.display import DisplaySettings
from anical.ui.editor import AnimeEditorDialog
from anical.ui.library_view import LibraryView
from anical.ui.pick_dialog import PickDialog
from anical.ui.playback_controller import PlaybackController, PlaybackReport
from anical.ui.playback_dialog import PlaybackSettingsDialog
from anical.ui.show_page import ShowPage
from anical.ui.sync_controller import RefreshResult, SyncController


def sync_message(result: RefreshResult) -> str | None:
    parts = []
    if result.mal:
        mal = result.mal
        total = mal.added + mal.updated + mal.unchanged
        text = f"Synced {total} show{'' if total == 1 else 's'} from MyAnimeList"
        if mal.added:
            text += f" ({mal.added} new)"
        parts.append(text)
    if result.schedule and result.schedule.changed:
        count = result.schedule.changed
        parts.append(f"{count} airing time{'' if count == 1 else 's'} changed")
    if result.errors:
        parts.append("some data couldn’t be updated")
    return ", ".join(parts) or None


class MainWindow(Adw.ApplicationWindow):
    def __init__(
        self,
        *,
        application: Adw.Application,
        repository: LibraryRepository,
        sync_controller: SyncController,
        display: DisplaySettings,
        playback: PlaybackController,
    ):
        super().__init__(application=application, title="AniCal", default_width=1100, default_height=760)
        self.set_size_request(360, 294)
        self.repository = repository
        self.schedule = ScheduleRepository(repository.connection)
        self.playback_repository = PlaybackRepository(repository.connection)
        self.sync_controller = sync_controller
        self.display = display
        self.playback = playback

        self._stack = Adw.ViewStack()
        self.calendar_view = CalendarView(self)
        self.library_view = LibraryView(self)
        self._stack.add_titled_with_icon(self.calendar_view, "calendar", "Calendar", "x-office-calendar-symbolic")
        self._stack.add_titled_with_icon(self.library_view, "library", "Library", "view-list-bullet-symbolic")
        self._stack.connect("notify::visible-child-name", lambda *_: self._on_view_changed())

        header = Adw.HeaderBar(title_widget=Adw.ViewSwitcher(stack=self._stack, policy=Adw.ViewSwitcherPolicy.WIDE))
        header.pack_start(Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Add Anime", action_name="win.add-anime"))
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=_main_menu(), tooltip_text="Main Menu", primary=True))
        self._search_button = Gtk.ToggleButton(icon_name="system-search-symbolic", tooltip_text="Search Library")
        self._search_button.bind_property(
            "active", self.library_view.search_bar, "search-mode-enabled",
            GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE,
        )
        header.pack_end(self._search_button)
        self._spinner = Adw.Spinner(tooltip_text="Refreshing", visible=False)
        header.pack_end(self._spinner)

        self._banner = Adw.Banner(button_label="Retry")
        self._banner.connect("button-clicked", lambda *_: sync_controller.sync())
        switcher_bar = Adw.ViewSwitcherBar(stack=self._stack)

        main = Adw.ToolbarView(content=self._stack)
        main.add_top_bar(header)
        main.add_top_bar(self._banner)
        main.add_bottom_bar(switcher_bar)
        self._main_page = Adw.NavigationPage(title="AniCal", tag="main", child=main)

        self._nav = Adw.NavigationView()
        self._nav.add(self._main_page)
        # Watched counts and edits made on a show page show up on return.
        self._nav.connect("popped", lambda *_: self.library_changed())
        self._toasts = Adw.ToastOverlay(child=self._nav)
        self.set_content(self._toasts)

        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 600sp"))
        narrow.add_setter(switcher_bar, "reveal", True)
        narrow.add_setter(header, "title-widget", Adw.WindowTitle(title="AniCal"))
        narrow.add_setter(self.calendar_view, "compact", True)
        self.add_breakpoint(narrow)

        handlers = [
            sync_controller.connect("synced", self._on_synced),
            sync_controller.connect("library-changed", lambda *_: self.library_changed()),
            sync_controller.connect("changed", lambda *_: self._update_sync_state()),
        ]
        playback_handlers = [
            playback.connect("pick-requested", lambda _p, request: PickDialog(request).present(self)),
            playback.connect("finished", self._on_playback_finished),
        ]
        self.connect("destroy", lambda *_: [sync_controller.disconnect(h) for h in handlers])
        self.connect("destroy", lambda *_: [playback.disconnect(h) for h in playback_handlers])

        for name, callback in (
            ("add-anime", self._on_add_anime),
            ("search", self._on_search),
            ("sync", self._on_sync),
            ("calendar-previous", lambda *_: self._calendar_step(-1)),
            ("calendar-next", lambda *_: self._calendar_step(1)),
            ("calendar-today", lambda *_: self._calendar_step(0)),
            ("show-calendar", lambda *_: self._show_view("calendar")),
            ("show-library", lambda *_: self._show_view("library")),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
        self._update_sync_state()
        self._on_view_changed()

    def show_anime(self, anime_id: int) -> None:
        self._nav.push(ShowPage(self, anime_id))

    def show_unwatched(self) -> None:
        self._nav.pop_to_page(self._main_page)
        self._stack.set_visible_child_name("calendar")
        self.calendar_view.show_mode("unwatched")

    def play_episode(self, episode_id: int) -> None:
        if problem := self.playback.play(episode_id):
            self.toast(problem)
        else:
            self.toast(f"Opening {self.playback.playing} in ani-cli…")

    def open_playback_settings(self, anime_id: int) -> None:
        anime = self.repository.get_anime(anime_id)
        mapping = self.playback.mapping_for(anime.id, anime.title)
        PlaybackSettingsDialog(self.playback_repository, anime, mapping, on_saved=self.library_changed).present(self)

    def _on_playback_finished(self, _controller, report: PlaybackReport) -> None:
        result = report.result

        def mark_watched() -> None:
            self.playback.mark_watched(report.episode_id)
            self.library_changed()

        def undo() -> None:
            self.repository.set_watch_state([report.episode_id], WatchState.UNWATCHED)
            self.library_changed()

        match result.outcome:
            case Outcome.WATCHED if report.marked:
                self.toast(f"Marked {report.label} watched", "Undo", undo)
            case Outcome.WATCHED:
                self.toast(f"Finished {report.label}", "Mark Watched", mark_watched)
            case Outcome.PARTIAL:
                self.toast(f"Stopped {report.label} at {result.percent or 0:.0f}%", "Mark Watched", mark_watched)
            case Outcome.UNKNOWN:
                self.toast(f"Did you finish {report.label}?", "Mark Watched", mark_watched)
            case Outcome.EPISODE_NOT_FOUND:
                self.toast(f"{report.label} isn’t available in ani-cli yet")
            case Outcome.SHOW_NOT_FOUND:
                self.toast(
                    f"ani-cli found nothing for “{report.query}”",
                    "Change Search…",
                    lambda: self.open_playback_settings(report.anime_id),
                )
            case Outcome.FAILED:
                self.toast(f"Playback failed: {result.message}")
            case Outcome.CANCELLED:
                pass
        self.library_changed()

    def library_changed(self) -> None:
        """Redraw everything that shows library data."""
        self.library_view.reload()
        self.calendar_view.render()
        page = self._nav.get_visible_page()
        if isinstance(page, ShowPage):
            page.refresh()

    def _on_view_changed(self) -> None:
        library = self._stack.get_visible_child_name() == "library"
        self._search_button.set_visible(library)
        # Typing starts a search only where there is something to search.
        self.library_view.search_bar.set_key_capture_widget(self if library else None)

    def _update_sync_state(self) -> None:
        controller = self.sync_controller
        self._spinner.set_visible(controller.busy)
        error = controller.error
        self._banner.set_title(f"Couldn’t refresh — {error}" if error else "")
        self._banner.set_revealed(bool(error) and not controller.busy)

    def _on_synced(self, _controller, result: RefreshResult) -> None:
        if message := sync_message(result):
            self.toast(message)

    def close_page(self, page: Adw.NavigationPage) -> None:
        if self._nav.get_visible_page() is page:
            self._nav.pop()

    def toast(self, text: str, button_label: str | None = None, on_button=None) -> None:
        toast = Adw.Toast(title=text, use_markup=False)
        if button_label:
            toast.set_button_label(button_label)
            toast.connect("button-clicked", lambda *_: on_button())
        self._toasts.add_toast(toast)

    def _on_add_anime(self, *_):
        def saved(anime_id: int) -> None:
            self._nav.pop_to_page(self._main_page)
            self.library_changed()
            self.show_anime(anime_id)

        AnimeEditorDialog(self.repository, anime=None, on_saved=saved).present(self)

    def _on_sync(self, *_):
        self.sync_controller.sync()

    def _show_view(self, name: str) -> None:
        self._nav.pop_to_page(self._main_page)
        self._stack.set_visible_child_name(name)

    def _calendar_step(self, steps: int) -> None:
        self._show_view("calendar")
        self.calendar_view.navigate(steps)

    def _on_search(self, *_):
        self._nav.pop_to_page(self._main_page)
        self._stack.set_visible_child_name("library")
        self.library_view.toggle_search()


def _main_menu() -> Gio.Menu:
    menu = Gio.Menu()
    actions = Gio.Menu()
    actions.append("Refresh", "win.sync")
    menu.append_section(None, actions)
    app_items = Gio.Menu()
    app_items.append("Preferences", "app.preferences")
    app_items.append("Keyboard Shortcuts", "app.shortcuts")
    app_items.append("About AniCal", "app.about")
    menu.append_section(None, app_items)
    return menu


class DatabaseErrorWindow(Adw.ApplicationWindow):
    """Shown instead of the library when the database can't be opened or upgraded."""

    def __init__(self, *, application: Adw.Application, path: Path, error: Exception):
        super().__init__(application=application, title="AniCal", default_width=640, default_height=560)
        open_folder = Gtk.Button(label="Open Data Folder", halign=Gtk.Align.CENTER, css_classes=["pill"])
        open_folder.connect(
            "clicked", lambda *_: Gtk.FileLauncher.new(Gio.File.new_for_path(str(path))).open_containing_folder(self)
        )
        status = Adw.StatusPage(
            icon_name="dialog-error-symbolic",
            title="Couldn’t Open Library",
            description=f"{error}\n\nThe library file was not modified:\n{path}",
            child=open_folder,
        )
        view = Adw.ToolbarView(content=status)
        view.add_top_bar(Adw.HeaderBar())
        self.set_content(view)

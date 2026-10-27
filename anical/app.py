"""Application entry point."""

from __future__ import annotations

import os
import sqlite3
import sys
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from anical import APP_ID, __version__  # noqa: E402
from anical.db.backup import automatic_backup  # noqa: E402
from anical.db.database import DatabaseError, open_database  # noqa: E402
from anical.db.playback import PlaybackRepository  # noqa: E402
from anical.db.repository import LibraryRepository  # noqa: E402
from anical.db.settings import SETUP_SEEN, SettingsRepository  # noqa: E402
from anical.net.http import UrllibClient  # noqa: E402
from anical.platform.notify import notification_sender  # noqa: E402
from anical.platform.paths import app_paths  # noqa: E402
from anical.platform.secrets import token_store  # noqa: E402
from anical.sync.mal_sync import MalSyncService  # noqa: E402
from anical.ui.style import install_css, install_icons  # noqa: E402
from anical.ui.display import DisplaySettings  # noqa: E402
from anical.db.schedule import ScheduleRepository  # noqa: E402
from anical.ui.notifier import Notifier  # noqa: E402
from anical.ui.playback_controller import PlaybackController  # noqa: E402
from anical.ui.preferences import PreferencesDialog  # noqa: E402
from anical.ui.setup_wizard import SetupWizard  # noqa: E402
from anical.ui.sync_controller import SyncController  # noqa: E402
from anical.ui.window import DatabaseErrorWindow, MainWindow  # noqa: E402


# Shown in the Keyboard Shortcuts window; quit, preferences and the
# original window shortcuts are registered with their actions below.
ACCELERATORS = {
    "app.shortcuts": ["<Primary>question"],
    "win.calendar-previous": ["<Primary>Page_Up"],
    "win.calendar-next": ["<Primary>Page_Down"],
    "win.calendar-today": ["<Primary>Home"],
    "win.show-calendar": ["<Primary>1"],
    "win.show-library": ["<Primary>2"],
}


def shortcuts_dialog() -> Adw.ShortcutsDialog:
    dialog = Adw.ShortcutsDialog()
    for title, items in (
        ("General", [
            ("Add Anime", "<Primary>n"),
            ("Search Library", "<Primary>f"),
            ("Refresh", "<Primary>r"),
            ("Preferences", "<Primary>comma"),
            ("Keyboard Shortcuts", "<Primary>question"),
            ("Quit", "<Primary>q"),
        ]),
        ("Views", [
            ("Calendar", "<Primary>1"),
            ("Library", "<Primary>2"),
            ("Back", "<Alt>Left"),
        ]),
        ("Calendar", [
            ("Previous Day, Week or Month", "<Primary>Page_Up"),
            ("Next Day, Week or Month", "<Primary>Page_Down"),
            ("Today", "<Primary>Home"),
        ]),
    ):
        section = Adw.ShortcutsSection(title=title)
        for label, accelerator in items:
            section.add(Adw.ShortcutsItem(title=label, accelerator=accelerator))
        dialog.add(section)
    return dialog


class AniCalApplication(Adw.Application):
    def __init__(self):
        # A separate library ($ANICAL_DATA_DIR: development, tests, CI) runs as its own
        # instance instead of handing over to an AniCal that is already running.
        separate = bool(os.environ.get("ANICAL_DATA_DIR"))
        flags = Gio.ApplicationFlags.NON_UNIQUE if separate else Gio.ApplicationFlags.DEFAULT_FLAGS
        super().__init__(application_id=APP_ID, flags=flags)
        self._conn = None
        self._sync: SyncController | None = None
        self._display: DisplaySettings | None = None
        self._playback: PlaybackController | None = None
        self._notifier: Notifier | None = None
        self._data_dir = None
        self.selftest_status: int | None = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        for name, callback, accels in (
            ("quit", lambda *_: self.quit(), ["<Primary>q"]),
            ("about", self._on_about, []),
            ("preferences", self._on_preferences, ["<Primary>comma"]),
            ("setup", lambda *_: self._show_setup(), []),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
            self.set_accels_for_action(f"app.{name}", accels)
        # Targets of notification buttons; they act on the main window.
        for name, handler in (
            ("show-anime", lambda window, value: window.show_anime(value.get_int64())),
            ("play-episode", lambda window, value: window.play_episode(value.get_int64())),
        ):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new("x"))
            action.connect("activate", lambda _a, value, h=handler: self._with_window(lambda w: h(w, value)))
            self.add_action(action)
        unwatched = Gio.SimpleAction.new("show-unwatched", None)
        unwatched.connect("activate", lambda *_: self._with_window(lambda w: w.show_unwatched()))
        self.add_action(unwatched)
        shortcuts = Gio.SimpleAction.new("shortcuts", None)
        shortcuts.connect("activate", lambda *_: shortcuts_dialog().present(self.get_active_window()))
        self.add_action(shortcuts)
        for action, accels in ACCELERATORS.items():
            self.set_accels_for_action(action, accels)
        self.set_accels_for_action("win.add-anime", ["<Primary>n"])
        self.set_accels_for_action("win.search", ["<Primary>f"])
        self.set_accels_for_action("win.sync", ["<Primary>r"])
        install_css()
        install_icons()

    def do_activate(self):
        if window := self.get_active_window():
            window.present()
            return
        paths = app_paths()
        self._data_dir = paths.data
        path = paths.data / "anical.db"
        try:
            self._conn = open_database(path)
        except DatabaseError as e:
            DatabaseErrorWindow(application=self, path=path, error=e).present()
            return
        try:
            automatic_backup(self._conn, paths.data / "backups", int(time.time()))
        except (OSError, sqlite3.Error) as e:
            print(f"AniCal: automatic backup failed: {e}", file=sys.stderr)
        repository = LibraryRepository(self._conn)
        settings = SettingsRepository(self._conn)
        http = UrllibClient()
        self._sync = SyncController(
            repository, settings, MalSyncService(http, token_store(paths.data)), http, paths.cache / "covers"
        )
        self._display = DisplaySettings(settings)
        self._playback = PlaybackController(repository, PlaybackRepository(self._conn), settings, paths.runtime)
        window = MainWindow(
            application=self,
            repository=repository,
            sync_controller=self._sync,
            display=self._display,
            playback=self._playback,
        )
        window.present()
        send = notification_sender(self)
        self._notifier = Notifier(ScheduleRepository(self._conn), settings, self._display, send)
        self._settings = settings
        self._runtime_dir = paths.runtime
        if report := os.environ.get("ANICAL_SELFTEST"):
            from anical import selftest

            selftest.schedule(self, window, paths, send, report)
            if step := os.environ.get("ANICAL_SELFTEST_WIZARD"):  # CI screenshots of a wizard step
                # After the window is up, as on a first run; presented earlier, macOS didn't show it.
                GLib.timeout_add(300, lambda: self._show_setup(window, int(step)) and False)
            elif os.environ.get("ANICAL_SELFTEST_DIALOG") == "preferences":  # does any dialog show?
                GLib.timeout_add(300, lambda: self.activate_action("preferences", None) and False)
        elif not settings.get(SETUP_SEEN) and not self._sync.config.client_id:
            GLib.idle_add(lambda: self._show_setup() and False)  # first run: after the window is up
        self._sync.connect("library-changed", lambda *_: self._notifier.check())
        self._sync.start()  # local data is already on screen; refresh in the background if due
        self._notifier.start()

    def do_shutdown(self):
        if self._sync is not None:
            self._sync.stop()
        if self._notifier is not None:
            self._notifier.stop()
        if self._conn is not None:
            self._conn.close()
        Adw.Application.do_shutdown(self)

    def _with_window(self, action) -> None:
        self.activate()
        if isinstance(window := self.get_active_window(), MainWindow):
            action(window)

    def _show_setup(self, window: MainWindow | None = None, step: int = 1) -> None:
        window = window or self.get_active_window()
        if self._sync is None or not isinstance(window, MainWindow):
            return
        wizard = SetupWizard(
            self._sync, self._playback, self._settings, self._runtime_dir,
            on_finished=lambda: window.toast("AniCal is set up. Enjoy your shows!"),
        )
        wizard.present(window)
        wizard.go_to(step)
        self.selftest_wizard = wizard  # for the self-test's dialog diagnostics

    def _on_preferences(self, *_):
        if self._sync is not None:
            window = self.get_active_window()
            PreferencesDialog(
                self._sync, self._display, self._playback, self._conn, self._data_dir,
                on_restored=window.library_changed if isinstance(window, MainWindow) else lambda: None,
            ).present(window)

    def _on_about(self, *_):
        Adw.AboutDialog(
            application_name="AniCal",
            application_icon=APP_ID,
            version=__version__,
            comments="Anime library and airing calendar, with ani-cli playback",
            license_type=Gtk.License.MIT_X11,
        ).present(self.get_active_window())


def main(argv: list[str] | None = None) -> int:
    if report := os.environ.get("ANICAL_SELFTEST"):
        from anical import selftest

        selftest.redirect_stderr(report)
    app = AniCalApplication()
    status = app.run(argv if argv is not None else sys.argv)
    return status if app.selftest_status is None else app.selftest_status

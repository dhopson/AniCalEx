"""Application entry point."""

from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from anical import APP_ID, __version__  # noqa: E402
from anical.auth.secrets import KeyringTokenStore  # noqa: E402
from anical.db.backup import automatic_backup  # noqa: E402
from anical.db.database import DatabaseError, open_database  # noqa: E402
from anical.db.playback import PlaybackRepository  # noqa: E402
from anical.db.repository import LibraryRepository  # noqa: E402
from anical.db.settings import SettingsRepository  # noqa: E402
from anical.net.http import UrllibClient  # noqa: E402
from anical.sync.mal_sync import MalSyncService  # noqa: E402
from anical.ui.style import install_css, install_icons  # noqa: E402
from anical.ui.display import DisplaySettings  # noqa: E402
from anical.db.schedule import ScheduleRepository  # noqa: E402
from anical.ui.notifier import Notifier  # noqa: E402
from anical.ui.playback_controller import PlaybackController  # noqa: E402
from anical.ui.preferences import PreferencesDialog  # noqa: E402
from anical.ui.sync_controller import SyncController  # noqa: E402
from anical.ui.window import DatabaseErrorWindow, MainWindow  # noqa: E402


def data_dir() -> Path:
    """$ANICAL_DATA_DIR (for development and tests), else $XDG_DATA_HOME/anical."""
    override = os.environ.get("ANICAL_DATA_DIR")
    return Path(override) if override else Path(GLib.get_user_data_dir()) / "anical"


def runtime_dir() -> Path:
    """Per-session scratch files (picker, player script, playback sessions)."""
    override = os.environ.get("ANICAL_DATA_DIR")
    return Path(override) / "run" if override else Path(GLib.get_user_runtime_dir()) / "anical"


def cache_dir() -> Path:
    """Re-downloadable data. Lives under the data dir override when one is set."""
    override = os.environ.get("ANICAL_DATA_DIR")
    return Path(override) / "cache" if override else Path(GLib.get_user_cache_dir()) / "anical"


# Shown in the Keyboard Shortcuts window; quit, preferences and the
# original window shortcuts are registered with their actions below.
ACCELERATORS = {
    "app.shortcuts": ["<Control>question"],
    "win.calendar-previous": ["<Control>Page_Up"],
    "win.calendar-next": ["<Control>Page_Down"],
    "win.calendar-today": ["<Control>Home"],
    "win.show-calendar": ["<Control>1"],
    "win.show-library": ["<Control>2"],
}


def shortcuts_dialog() -> Adw.ShortcutsDialog:
    dialog = Adw.ShortcutsDialog()
    for title, items in (
        ("General", [
            ("Add Anime", "<Control>n"),
            ("Search Library", "<Control>f"),
            ("Refresh", "<Control>r"),
            ("Preferences", "<Control>comma"),
            ("Keyboard Shortcuts", "<Control>question"),
            ("Quit", "<Control>q"),
        ]),
        ("Views", [
            ("Calendar", "<Control>1"),
            ("Library", "<Control>2"),
            ("Back", "<Alt>Left"),
        ]),
        ("Calendar", [
            ("Previous Day, Week or Month", "<Control>Page_Up"),
            ("Next Day, Week or Month", "<Control>Page_Down"),
            ("Today", "<Control>Home"),
        ]),
    ):
        section = Adw.ShortcutsSection(title=title)
        for label, accelerator in items:
            section.add(Adw.ShortcutsItem(title=label, accelerator=accelerator))
        dialog.add(section)
    return dialog


class AniCalApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self._conn = None
        self._sync: SyncController | None = None
        self._display: DisplaySettings | None = None
        self._playback: PlaybackController | None = None
        self._notifier: Notifier | None = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        for name, callback, accels in (
            ("quit", lambda *_: self.quit(), ["<Control>q"]),
            ("about", self._on_about, []),
            ("preferences", self._on_preferences, ["<Control>comma"]),
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
        self.set_accels_for_action("win.add-anime", ["<Control>n"])
        self.set_accels_for_action("win.search", ["<Control>f"])
        self.set_accels_for_action("win.sync", ["<Control>r"])
        install_css()
        install_icons()

    def do_activate(self):
        if window := self.get_active_window():
            window.present()
            return
        path = data_dir() / "anical.db"
        try:
            self._conn = open_database(path)
        except DatabaseError as e:
            DatabaseErrorWindow(application=self, path=path, error=e).present()
            return
        try:
            automatic_backup(self._conn, data_dir() / "backups", int(time.time()))
        except (OSError, sqlite3.Error) as e:
            print(f"AniCal: automatic backup failed: {e}", file=sys.stderr)
        repository = LibraryRepository(self._conn)
        settings = SettingsRepository(self._conn)
        http = UrllibClient()
        self._sync = SyncController(
            repository, settings, MalSyncService(http, KeyringTokenStore()), http, cache_dir() / "covers"
        )
        self._display = DisplaySettings(settings)
        self._playback = PlaybackController(repository, PlaybackRepository(self._conn), settings, runtime_dir())
        MainWindow(
            application=self,
            repository=repository,
            sync_controller=self._sync,
            display=self._display,
            playback=self._playback,
        ).present()
        self._notifier = Notifier(ScheduleRepository(self._conn), settings, self._display, self.send_notification)
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

    def _on_preferences(self, *_):
        if self._sync is not None:
            window = self.get_active_window()
            PreferencesDialog(
                self._sync, self._display, self._playback, self._conn, data_dir(),
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
    return AniCalApplication().run(argv if argv is not None else sys.argv)

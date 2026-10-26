"""Application entry point."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from anical import APP_ID, __version__  # noqa: E402
from anical.auth.secrets import KeyringTokenStore  # noqa: E402
from anical.db.database import DatabaseError, open_database  # noqa: E402
from anical.db.repository import LibraryRepository  # noqa: E402
from anical.db.settings import SettingsRepository  # noqa: E402
from anical.net.http import UrllibClient  # noqa: E402
from anical.sync.mal_sync import MalSyncService  # noqa: E402
from anical.ui.style import install_css  # noqa: E402
from anical.ui.display import DisplaySettings  # noqa: E402
from anical.ui.preferences import PreferencesDialog  # noqa: E402
from anical.ui.sync_controller import SyncController  # noqa: E402
from anical.ui.window import DatabaseErrorWindow, MainWindow  # noqa: E402


def data_dir() -> Path:
    """$ANICAL_DATA_DIR (for development and tests), else $XDG_DATA_HOME/anical."""
    override = os.environ.get("ANICAL_DATA_DIR")
    return Path(override) if override else Path(GLib.get_user_data_dir()) / "anical"


def cache_dir() -> Path:
    """Re-downloadable data. Lives under the data dir override when one is set."""
    override = os.environ.get("ANICAL_DATA_DIR")
    return Path(override) / "cache" if override else Path(GLib.get_user_cache_dir()) / "anical"


class AniCalApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self._conn = None
        self._sync: SyncController | None = None
        self._display: DisplaySettings | None = None

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
        self.set_accels_for_action("win.add-anime", ["<Control>n"])
        self.set_accels_for_action("win.search", ["<Control>f"])
        self.set_accels_for_action("win.sync", ["<Control>r"])
        install_css()

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
        repository = LibraryRepository(self._conn)
        settings = SettingsRepository(self._conn)
        http = UrllibClient()
        self._sync = SyncController(
            repository, settings, MalSyncService(http, KeyringTokenStore()), http, cache_dir() / "covers"
        )
        self._display = DisplaySettings(settings)
        MainWindow(
            application=self, repository=repository, sync_controller=self._sync, display=self._display
        ).present()
        self._sync.start()  # local data is already on screen; refresh in the background if due

    def do_shutdown(self):
        if self._sync is not None:
            self._sync.stop()
        if self._conn is not None:
            self._conn.close()
        Adw.Application.do_shutdown(self)

    def _on_preferences(self, *_):
        if self._sync is not None:
            PreferencesDialog(self._sync, self._display).present(self.get_active_window())

    def _on_about(self, *_):
        Adw.AboutDialog(
            application_name="AniCal",
            application_icon="x-office-calendar-symbolic",
            version=__version__,
            comments="Anime library and airing calendar, with ani-cli playback",
            license_type=Gtk.License.GPL_3_0,
        ).present(self.get_active_window())


def main(argv: list[str] | None = None) -> int:
    return AniCalApplication().run(argv if argv is not None else sys.argv)

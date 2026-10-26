from __future__ import annotations

import datetime
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk

from anical.auth.mal_oauth import REDIRECT_URI, OAuthError
from anical.db.backup import BackupError, backup_to, restore_from
from anical.db.settings import (
    AVAILABILITY_LAG_HOURS,
    DISPLAY_ENGLISH_TITLES,
    DISPLAY_TIMEZONE,
    DISPLAY_WEEK_START,
    NOTIFICATIONS_ENABLED,
    PLAYBACK_AUTO_MARK,
    PLAYBACK_EXECUTABLE,
    PLAYBACK_MODE,
    PLAYBACK_QUALITY,
)
from anical.domain.playback import MODES, QUALITIES
from anical.sync.mal_sync import ACCOUNT, USERNAME
from anical.ui.display import DisplaySettings, available_timezones, system_timezone_name
from anical.ui.playback_controller import PlaybackController
from anical.ui.sync_controller import SyncController

_QUALITY_LABELS = {"best": "Best Available", "worst": "Lowest"}


def format_time(epoch: int | None) -> str:
    if epoch is None:
        return "Never"
    return datetime.datetime.fromtimestamp(epoch).astimezone().strftime("%x %X")


class PreferencesDialog(Adw.PreferencesDialog):
    def __init__(
        self,
        controller: SyncController,
        display: DisplaySettings,
        playback: PlaybackController,
        connection: sqlite3.Connection,
        data_dir: Path,
        on_restored: Callable[[], None],
    ):
        super().__init__(title="Preferences", search_enabled=False)
        self._controller = controller
        self._display = display
        self._playback = playback
        self._connection = connection
        self._data_dir = data_dir
        self._on_restored = on_restored
        self._login_dialog: Adw.AlertDialog | None = None
        self.add(self._general_page())
        self.add(self._playback_page())
        self.add(self._data_page())

        self._page = Adw.PreferencesPage(title="MyAnimeList", icon_name="system-users-symbolic")
        self.add(self._page)
        self._groups: list[Adw.PreferencesGroup] = []
        self._handler = controller.connect("changed", lambda *_: self._rebuild())
        self.connect("closed", lambda *_: controller.disconnect(self._handler))
        self._rebuild()

    def _general_page(self) -> Adw.PreferencesPage:
        display = self._display
        page = Adw.PreferencesPage(title="General", icon_name="preferences-system-symbolic")
        group = Adw.PreferencesGroup(title="Calendar")

        zones = [None, *sorted(available_timezones())]
        names = Gtk.StringList.new([f"System Default ({system_timezone_name()})", *zones[1:]])
        timezone = Adw.ComboRow(title="Time Zone", model=names, enable_search=True)
        timezone.set_expression(Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
        current = display.timezone_name
        timezone.set_selected(zones.index(current) if current in zones else 0)
        timezone.connect("notify::selected", lambda row, _p: display.set(DISPLAY_TIMEZONE, zones[row.get_selected()]))
        group.add(timezone)

        week_start = Adw.ComboRow(title="Week Starts On", model=Gtk.StringList.new(["Monday", "Sunday"]))
        week_start.set_selected(1 if display.first_weekday == 6 else 0)
        week_start.connect(
            "notify::selected", lambda row, _p: display.set(DISPLAY_WEEK_START, 6 if row.get_selected() else 0)
        )
        group.add(week_start)

        english = Adw.SwitchRow(
            title="Use English Titles", subtitle="When a show has an English title, use it in the calendar",
            active=display.english_titles,
        )
        english.connect("notify::active", lambda row, _p: display.set(DISPLAY_ENGLISH_TITLES, row.get_active()))
        group.add(english)

        lag = Adw.SpinRow.new_with_range(0, 48, 1)
        lag.set_title("Hours Until Available")
        lag.set_subtitle("After an episode airs, how long until it is shown as probably watchable")
        lag.set_value(display.availability_lag_s / 3600)
        lag.connect("notify::value", lambda row, _p: display.set(AVAILABILITY_LAG_HOURS, row.get_value()))
        group.add(lag)
        page.add(group)

        notifications = Adw.PreferencesGroup(
            title="Notifications", description="Only while AniCal is running. Estimated air times are never announced."
        )
        enabled = Adw.SwitchRow(
            title="New Episodes",
            subtitle="Notify when an episode you follow is probably available to watch",
            active=self._display_setting(NOTIFICATIONS_ENABLED, True),
        )
        enabled.connect("notify::active", lambda row, _p: display.set(NOTIFICATIONS_ENABLED, row.get_active()))
        notifications.add(enabled)
        page.add(notifications)
        return page

    def _display_setting(self, key: str, default):
        value = self._display.get(key)
        return default if value is None else value

    def _playback_page(self) -> Adw.PreferencesPage:
        playback = self._playback
        config = playback.config
        page = Adw.PreferencesPage(title="Playback", icon_name="media-playback-start-symbolic")

        program = Adw.PreferencesGroup(title="ani-cli")
        command = Adw.EntryRow(title="ani-cli Command or Path", show_apply_button=True, text=config.executable)
        status = Adw.ActionRow(use_markup=False)

        def update_status() -> None:
            found = playback.resolve_executable()
            status.set_title("Found" if found else "Not Found")
            status.set_subtitle(found or "Install ani-cli, or enter the full path to it")

        def apply(row: Adw.EntryRow) -> None:
            text = row.get_text().strip()
            playback.set_setting(PLAYBACK_EXECUTABLE, None if text in ("", "ani-cli") else text)
            update_status()

        command.connect("apply", apply)
        update_status()
        program.add(command)
        program.add(status)
        page.add(program)

        options = Adw.PreferencesGroup(
            title="Episodes",
            description="Watch progress is tracked with mpv, ani-cli’s default player. With other players you’ll be asked.",
        )
        mode = Adw.ComboRow(title="Audio", model=Gtk.StringList.new(["Subtitled", "Dubbed"]))
        mode.set_selected(MODES.index(config.mode))
        mode.connect("notify::selected", lambda row, _p: playback.set_setting(PLAYBACK_MODE, MODES[row.get_selected()]))
        options.add(mode)

        quality = Adw.ComboRow(
            title="Quality", model=Gtk.StringList.new([_QUALITY_LABELS.get(q, q) for q in QUALITIES])
        )
        quality.set_selected(QUALITIES.index(config.quality))
        quality.connect(
            "notify::selected", lambda row, _p: playback.set_setting(PLAYBACK_QUALITY, QUALITIES[row.get_selected()])
        )
        options.add(quality)

        auto_mark = Adw.SwitchRow(
            title="Mark Watched Automatically",
            subtitle="When an episode plays to the end or past 90%. Otherwise you’re asked.",
            active=config.auto_mark,
        )
        auto_mark.connect("notify::active", lambda row, _p: playback.set_setting(PLAYBACK_AUTO_MARK, row.get_active()))
        options.add(auto_mark)
        page.add(options)
        return page

    def _data_page(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title="Data", icon_name="drive-harddisk-symbolic")
        group = Adw.PreferencesGroup(
            title="Backups",
            description="AniCal keeps the last four weekly backups automatically. Your library never leaves this computer.",
        )
        backup = Adw.ActionRow(title="Back Up Library", subtitle="Save a copy anywhere, e.g. another drive")
        button = Gtk.Button(label="Back Up…", valign=Gtk.Align.CENTER)
        button.connect("clicked", lambda *_: self._choose_backup_file())
        backup.add_suffix(button)
        group.add(backup)

        restore = Adw.ActionRow(title="Restore From Backup", subtitle="Your current library is saved first")
        button = Gtk.Button(label="Restore…", valign=Gtk.Align.CENTER)
        button.connect("clicked", lambda *_: self._choose_restore_file())
        restore.add_suffix(button)
        group.add(restore)

        folder = Adw.ActionRow(title="Data Folder", subtitle=str(self._data_dir), use_markup=False)
        button = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Open Data Folder")
        button.connect(
            "clicked", lambda *_: Gtk.FileLauncher.new(Gio.File.new_for_path(str(self._data_dir))).launch(self, None, None)
        )
        folder.add_suffix(button)
        group.add(folder)
        page.add(group)
        return page

    def _choose_backup_file(self) -> None:
        dialog = Gtk.FileDialog(title="Back Up Library", initial_name=f"anical-backup-{datetime.date.today()}.db")

        def chosen(_dialog, result):
            try:
                file = dialog.save_finish(result)
            except GLib.Error:
                return  # cancelled
            try:
                backup_to(self._connection, Path(file.get_path()))
            except (OSError, sqlite3.Error) as e:
                self.add_toast(Adw.Toast(title=f"Backup failed: {e}", use_markup=False))
                return
            self.add_toast(Adw.Toast(title="Library backed up", use_markup=False))

        dialog.save(self.get_root(), None, chosen)

    def _choose_restore_file(self) -> None:
        dialog = Gtk.FileDialog(title="Restore From Backup")

        def chosen(_dialog, result):
            try:
                file = dialog.open_finish(result)
            except GLib.Error:
                return
            self._confirm_restore(Path(file.get_path()))

        dialog.open(self.get_root(), None, chosen)

    def _confirm_restore(self, path: Path) -> None:
        confirm = Adw.AlertDialog(
            heading="Replace Your Library?",
            body=f"Everything in AniCal is replaced with the backup “{path.name}”. "
            "A copy of your current library is saved in the backups folder first.",
        )
        confirm.add_response("cancel", "Cancel")
        confirm.add_response("restore", "Restore")
        confirm.set_response_appearance("restore", Adw.ResponseAppearance.DESTRUCTIVE)
        confirm.set_default_response("cancel")
        confirm.set_close_response("cancel")

        def respond(_dialog, response: str) -> None:
            if response != "restore":
                return
            stamp = time.strftime("%Y%m%d-%H%M%S")
            safety = self._data_dir / "backups" / f"anical-before-restore-{stamp}.db"
            try:
                restore_from(self._connection, path, safety)
            except (BackupError, OSError, sqlite3.Error) as e:
                self.add_toast(Adw.Toast(title=f"Couldn’t restore: {e}", use_markup=False))
                return
            self._on_restored()
            self.add_toast(Adw.Toast(title="Library restored", use_markup=False))

        confirm.connect("response", respond)
        confirm.present(self)

    def _rebuild(self) -> None:
        for group in self._groups:
            self._page.remove(group)
        self._groups = [self._account_group(), self._sync_group(), self._client_group()]
        for group in self._groups:
            self._page.add(group)

    def _account_group(self) -> Adw.PreferencesGroup:
        config = self._controller.config
        group = Adw.PreferencesGroup(
            title="Account",
            description="AniCal only reads your list. It never changes anything on MyAnimeList.",
        )
        if config.mode == ACCOUNT:
            row = Adw.ActionRow(title=f"Logged in as {config.username}", use_markup=False)
            logout = Gtk.Button(label="Log Out", valign=Gtk.Align.CENTER)
            logout.connect("clicked", self._on_logout)
            row.add_suffix(logout)
            group.add(row)
            return group

        login_row = Adw.ActionRow(
            title="Log In", subtitle="Opens MyAnimeList in your browser. Needed for private lists."
        )
        login = Gtk.Button(label="Log In", valign=Gtk.Align.CENTER, css_classes=["suggested-action"])
        login.set_sensitive(bool(config.client_id))
        login.connect("clicked", self._on_login)
        login_row.add_suffix(login)
        group.add(login_row)

        username = Adw.EntryRow(
            title="Or Import a Public List by Username", show_apply_button=True,
            text=config.username if config.mode == USERNAME else "",
        )
        username.set_sensitive(bool(config.client_id))
        username.connect("apply", lambda row: self._controller.use_public_list(row.get_text()))
        group.add(username)
        return group

    def _sync_group(self) -> Adw.PreferencesGroup:
        controller = self._controller
        group = Adw.PreferencesGroup(
            title="Sync", description="Your list and airing times refresh when AniCal starts and once a day while it runs."
        )
        row = Adw.ActionRow(title="Last Synced", subtitle=format_time(controller.last_run))
        if controller.busy:
            row.add_suffix(Adw.Spinner())
        else:
            sync = Gtk.Button(label="Sync Now", valign=Gtk.Align.CENTER)
            sync.connect("clicked", lambda *_: controller.sync())
            row.add_suffix(sync)
        group.add(row)
        if controller.error:
            error = Adw.ActionRow(title="Last Sync Failed", subtitle=controller.error, use_markup=False)
            error.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic", css_classes=["warning"]))
            group.add(error)
        return group

    def _client_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="API Client",
            description=f"A client ID from myanimelist.net/apiconfig with redirect URL {REDIRECT_URI}",
        )
        entry = Adw.EntryRow(title="Client ID", show_apply_button=True, text=self._controller.config.client_id)
        entry.connect("apply", lambda row: self._controller.set_client_id(row.get_text()))
        group.add(entry)
        return group

    def _on_login(self, *_):
        try:
            url, cancelled = self._controller.begin_login(self._on_login_finished)
        except OAuthError as e:
            self.add_toast(Adw.Toast(title=str(e), use_markup=False))
            return
        Gtk.UriLauncher.new(url).launch(self.get_root(), None, None, None)
        dialog = Adw.AlertDialog(
            heading="Continue in Your Browser",
            body="Log in to MyAnimeList and allow access for AniCal. This closes by itself when you’re done.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.connect("response", lambda *_: cancelled.set())
        dialog.present(self)
        self._login_dialog = dialog

    def _on_login_finished(self, error: str | None) -> None:
        if self._login_dialog is not None:
            self._login_dialog.force_close()
            self._login_dialog = None
        if error:
            self.add_toast(Adw.Toast(title=error, use_markup=False, timeout=6))

    def _on_logout(self, *_):
        self._controller.logout(
            lambda error: error and self.add_toast(Adw.Toast(title=error, use_markup=False))
        )

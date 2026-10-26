from __future__ import annotations

import datetime

from gi.repository import Adw, Gtk

from anical.auth.mal_oauth import REDIRECT_URI, OAuthError
from anical.db.settings import AVAILABILITY_LAG_HOURS, DISPLAY_ENGLISH_TITLES, DISPLAY_TIMEZONE, DISPLAY_WEEK_START
from anical.sync.mal_sync import ACCOUNT, USERNAME
from anical.ui.display import DisplaySettings, available_timezones, system_timezone_name
from anical.ui.sync_controller import SyncController


def format_time(epoch: int | None) -> str:
    if epoch is None:
        return "Never"
    return datetime.datetime.fromtimestamp(epoch).astimezone().strftime("%x %X")


class PreferencesDialog(Adw.PreferencesDialog):
    def __init__(self, controller: SyncController, display: DisplaySettings):
        super().__init__(title="Preferences", search_enabled=False)
        self._controller = controller
        self._display = display
        self._login_dialog: Adw.AlertDialog | None = None
        self.add(self._general_page())

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
        return page

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

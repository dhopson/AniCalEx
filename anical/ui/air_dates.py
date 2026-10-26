"""Dialog for entering air dates by hand (for shows no source covers, or to correct one)."""

from __future__ import annotations

import datetime
from collections.abc import Callable
from zoneinfo import ZoneInfo

from gi.repository import Adw, GObject, Gtk

from anical.db.schedule import ScheduleRepository
from anical.domain.models import MAX_EPISODES, Anime, ValidationError
from anical.domain.schedule import manual_series
from anical.ui.display import DisplaySettings

_JAPAN = "Asia/Tokyo"


class AirDatesDialog(Adw.Dialog):
    def __init__(
        self,
        schedule: ScheduleRepository,
        display: DisplaySettings,
        anime: Anime,
        first_episode: int,
        on_saved: Callable[[], None],
    ):
        super().__init__(title="Set Air Dates", content_width=480, content_height=560)
        self._schedule = schedule
        self._anime = anime
        self._on_saved = on_saved
        self._zones = [display.timezone.key, _JAPAN]

        self._first = Adw.SpinRow.new_with_range(1, MAX_EPISODES, 1)
        self._first.set_title("Episode")
        self._first.set_value(first_episode)
        today = datetime.datetime.now(display.timezone).date()
        self._date = Adw.EntryRow(title="Date (YYYY-MM-DD)", text=today.isoformat())
        self._time = Adw.EntryRow(title="Time (24-hour HH:MM)")
        self._zone = Adw.ComboRow(
            title="Time Zone",
            model=Gtk.StringList.new([f"Your Time Zone ({self._zones[0]})", "Japan (broadcast time)"]),
        )
        self._repeat = Adw.SwitchRow(title="Repeat Weekly", subtitle="Same day and time for following episodes")
        self._last = Adw.SpinRow.new_with_range(1, MAX_EPISODES, 1)
        self._last.set_title("Through Episode")
        self._last.set_value(max(first_episode, anime.total_episodes or first_episode + 11))
        self._repeat.bind_property("active", self._last, "sensitive", GObject.BindingFlags.SYNC_CREATE)

        self._rows = {"first_episode": self._first, "date": self._date, "time": self._time, "last_episode": self._last}

        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(
            description="Your dates take priority over MyAnimeList and AniList. You can clear them from the show’s menu."
        )
        for row in (self._first, self._date, self._time, self._zone, self._repeat, self._last):
            group.add(row)
        page.add(group)

        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        save = Gtk.Button(label="Save", css_classes=["suggested-action"])
        save.connect("clicked", self._on_save)
        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        header.pack_start(cancel)
        header.pack_end(save)
        view = Adw.ToolbarView(content=page)
        view.add_top_bar(header)
        self._toasts = Adw.ToastOverlay(child=view)
        self.set_child(self._toasts)
        self.set_default_widget(save)
        self.set_focus(self._time)

    def _on_save(self, *_):
        for row in self._rows.values():
            row.remove_css_class("error")
        try:
            series = manual_series(
                int(self._first.get_value()),
                int(self._last.get_value()) if self._repeat.get_active() else None,
                self._date.get_text(),
                self._time.get_text(),
                ZoneInfo(self._zones[self._zone.get_selected()]),
            )
        except ValidationError as e:
            for field in e.errors:
                self._rows[field].add_css_class("error")
            self._toasts.add_toast(Adw.Toast(title=next(iter(e.errors.values())), use_markup=False))
            return
        self._schedule.set_user_airings(self._anime.id, series)
        self.close()
        self._on_saved()

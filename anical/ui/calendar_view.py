"""Sonarr-style calendar: day, week and month views plus an aired-but-unwatched list."""

from __future__ import annotations

import datetime
import time
from itertools import groupby
from typing import TYPE_CHECKING

from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango

from anical.db.schedule import UndatedShow
from anical.domain.calendar import (
    CalendarEntry,
    CalendarMode,
    EntryStatus,
    format_time,
    group_by_day,
    local_datetime,
    range_title,
    shift,
    utc_bounds,
    visible_range,
)
from anical.domain.models import WatchState
from anical.domain.schedule import AiringSource
from anical.ui.cover import cover

if TYPE_CHECKING:
    from anical.ui.window import MainWindow

UNWATCHED = "unwatched"  # list mode shown next to the calendar modes
_MODES = (("day", "Day"), ("week", "Week"), ("month", "Month"), (UNWATCHED, "Unwatched"))
_STATUS_CLASS = {
    EntryStatus.UPCOMING: "upcoming",
    EntryStatus.JUST_AIRED: "just-aired",
    EntryStatus.AVAILABLE: "available",
    EntryStatus.WATCHED: "watched",
}
_MONTH_CELL_ITEMS = 3
_RERENDER_S = 300  # statuses move from "upcoming" to "aired" as time passes


class CalendarView(Adw.Bin):
    compact = GObject.Property(type=bool, default=False)

    def __init__(self, window: MainWindow):
        super().__init__()
        self._window = window
        self._display = window.display
        self._mode = CalendarMode.WEEK.value
        self._anchor = self._today()

        actions = Gio.SimpleActionGroup()
        open_show = Gio.SimpleAction.new("open-show", GLib.VariantType.new("x"))
        open_show.connect("activate", lambda _a, anime_id: window.show_anime(anime_id.get_int64()))
        toggle = Gio.SimpleAction.new("toggle-watched", GLib.VariantType.new("x"))
        toggle.connect("activate", lambda _a, episode_id: self._toggle_watched(episode_id.get_int64()))
        for action in (open_show, toggle):
            actions.add_action(action)
        self.insert_action_group("cal", actions)

        self._nav = Gtk.Box(css_classes=["linked"])
        for icon, label, tooltip, steps in (
            ("go-previous-symbolic", None, "Previous", -1),
            (None, "Today", "Go to today", 0),
            ("go-next-symbolic", None, "Next", 1),
        ):
            button = Gtk.Button(icon_name=icon, tooltip_text=tooltip) if icon else Gtk.Button(label=label)
            button.connect("clicked", lambda _b, s=steps: self._navigate(s))
            self._nav.append(button)
        self._title = Gtk.Label(css_classes=["title-4"], ellipsize=Pango.EllipsizeMode.END, hexpand=True, xalign=0.5)

        self._modes = Adw.ToggleGroup()
        for name, label in _MODES:
            self._modes.add(Adw.Toggle(name=name, label=label))
        self._modes.set_active_name(self._mode)
        self._modes.connect("notify::active-name", lambda *_: self._set_mode(self._modes.get_active_name()))

        self._top_row = Gtk.Box(spacing=12)
        self._top_row.append(self._nav)
        self._top_row.append(self._title)
        self._top_row.append(self._modes)
        self._modes_row = Gtk.Box(halign=Gtk.Align.CENTER, visible=False)
        header = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6, margin_bottom=6, margin_start=12, margin_end=12
        )
        header.append(self._top_row)
        header.append(self._modes_row)

        self._content = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        view = Adw.ToolbarView(content=self._content)
        view.add_top_bar(header)
        self._legend_bar = self._legend()
        view.add_bottom_bar(self._legend_bar)
        self.set_child(view)

        self.connect("notify::compact", lambda *_: self._relayout())
        self._display.connect("changed", lambda *_: self.render())
        self._timer = GLib.timeout_add_seconds(_RERENDER_S, self._on_timer)
        self.connect("destroy", lambda *_: GLib.source_remove(self._timer))
        self.render()

    # Navigation

    def _today(self) -> datetime.date:
        return datetime.datetime.now(self._display.timezone).date()

    def _navigate(self, steps: int) -> None:
        if self._mode == UNWATCHED:
            return
        self._anchor = self._today() if steps == 0 else shift(CalendarMode(self._mode), self._anchor, steps)
        self.render()

    def _set_mode(self, mode: str) -> None:
        if mode and mode != self._mode:
            self._mode = mode
            self.render()

    def show_day(self, day: datetime.date) -> None:
        self._anchor = day
        self._modes.set_active_name(CalendarMode.DAY.value)  # renders via notify

    def _relayout(self) -> None:
        self._modes.get_parent().remove(self._modes)
        (self._modes_row if self.compact else self._top_row).append(self._modes)
        self._modes_row.set_visible(self.compact)
        self._legend_bar.set_visible(not self.compact)
        self.render()

    def _on_timer(self) -> bool:
        if self.get_mapped():
            self.render()
        return GLib.SOURCE_CONTINUE

    # Rendering

    def render(self) -> None:
        now = int(time.time())
        tz = self._display.timezone
        self._nav.set_visible(self._mode != UNWATCHED)
        if self._mode == UNWATCHED:
            self._title.set_label("Aired, Not Watched")
            self._content.set_child(self._unwatched_page(now))
            return

        mode = CalendarMode(self._mode)
        first_weekday = self._display.first_weekday
        start, end = visible_range(mode, self._anchor, first_weekday)
        self._title.set_label(range_title(mode, self._anchor, first_weekday))
        entries = self._window.schedule.calendar_entries(*utc_bounds(start, end, tz))
        days = group_by_day(entries, tz)

        if mode is CalendarMode.MONTH:
            body = self._month_grid(start, end, days, now)
        elif mode is CalendarMode.WEEK and not self.compact:
            body = self._week_columns(start, days, now)
        else:
            body = self._agenda(start, end, days, now, show_empty_days=mode is CalendarMode.DAY)

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24, margin_bottom=24)
        page.append(body)
        if mode is not CalendarMode.MONTH and (undated := self._undated_group(now)):
            page.append(Adw.Clamp(child=undated, maximum_size=700, margin_start=12, margin_end=12))
        self._content.set_child(page)

    def _name(self, item: CalendarEntry | UndatedShow) -> str:
        return item.display_title(self._display.english_titles)

    def _describe(self, entry: CalendarEntry, with_date: bool = False, include_episode: bool = True) -> str:
        moment = local_datetime(entry.airs_at, self._display.timezone)
        when = format_time(moment, self._display.use_24h)
        if with_date:
            when = f"{moment:%a} {moment.day} {moment:%b}, {when}"
        if entry.estimated:
            when = f"≈ {when}"
        parts = [f"Episode {entry.number}", when] if include_episode else [when]
        if entry.episode_title and include_episode:
            parts.insert(1, entry.episode_title)
        if entry.estimated:
            parts.append("estimated")
        if entry.delayed:
            parts.append("delayed")
        if entry.source is AiringSource.USER:
            parts.append("your date")
        return " · ".join(parts)

    def _status_classes(self, entry: CalendarEntry, now: int) -> list[str]:
        classes = [_STATUS_CLASS[entry.status(now, self._display.availability_lag_s)]]
        if entry.estimated:
            classes.append("estimated")
        if entry.delayed and entry.state is not WatchState.WATCHED:
            classes.append("delayed")
        return classes

    def _entry_menu(self, entry: CalendarEntry) -> Gio.Menu:
        menu = Gio.Menu()
        item = Gio.MenuItem.new("Open Show", None)
        item.set_action_and_target_value("cal.open-show", GLib.Variant.new_int64(entry.anime_id))
        menu.append_item(item)
        label = "Mark Unwatched" if entry.state is WatchState.WATCHED else "Mark Watched"
        item = Gio.MenuItem.new(label, None)
        item.set_action_and_target_value("cal.toggle-watched", GLib.Variant.new_int64(entry.episode_id))
        menu.append_item(item)
        return menu

    def _card(self, entry: CalendarEntry, now: int) -> Gtk.Widget:
        """Compact entry for week columns: click for a menu."""
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        text.append(
            Gtk.Label(
                label=self._name(entry), wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, lines=2,
                # A wrapping label asks for its unwrapped width unless capped; the column decides.
                max_width_chars=8,
                ellipsize=Pango.EllipsizeMode.END, xalign=0, css_classes=["caption-heading"],
            )
        )
        moment = local_datetime(entry.airs_at, self._display.timezone)
        prefix = "≈ " if entry.estimated else ""
        for line in (f"Episode {entry.number}", f"{prefix}{format_time(moment, self._display.use_24h)}"):
            text.append(
                Gtk.Label(label=line, ellipsize=Pango.EllipsizeMode.END, xalign=0, css_classes=["caption", "dim-label"])
            )
        body = Gtk.Box(spacing=6)
        body.append(cover(entry.cover_path, self._name(entry), 24, 34))
        body.append(text)
        status = entry.status(now, self._display.availability_lag_s)
        description = f"{self._name(entry)}, {self._describe(entry)} — {status.label}"
        button = Gtk.MenuButton(
            child=body, menu_model=self._entry_menu(entry), tooltip_text=description,
            css_classes=["flat", "cal-card", *self._status_classes(entry, now)],
            overflow=Gtk.Overflow.HIDDEN,
        )
        button.update_property([Gtk.AccessibleProperty.LABEL], [description])
        return button

    def _entry_row(self, entry: CalendarEntry, now: int, in_show_group: bool = False) -> Adw.ActionRow:
        """Full entry for lists: checkbox marks watched, activating opens the show.

        Inside a group headed by the show's title, the row names the episode instead.
        """
        if in_show_group:
            title = f"Episode {entry.number}" + (f" · {entry.episode_title}" if entry.episode_title else "")
            subtitle = self._describe(entry, with_date=True, include_episode=False)
        else:
            title, subtitle = self._name(entry), self._describe(entry)
        row = Adw.ActionRow(title=title, subtitle=subtitle, use_markup=False, activatable=True, title_lines=2)
        if not in_show_group:
            row.add_prefix(cover(entry.cover_path, self._name(entry), 32, 45))
        check = Gtk.CheckButton(active=entry.state is WatchState.WATCHED, valign=Gtk.Align.CENTER, tooltip_text="Watched")
        check.update_property([Gtk.AccessibleProperty.LABEL], [f"{self._name(entry)} episode {entry.number} watched"])
        check.connect("toggled", lambda c: self._set_watched(entry.episode_id, c.get_active()))
        row.add_prefix(check)  # prefixes stack leftwards: the checkbox ends up first
        status = entry.status(now, self._display.availability_lag_s)
        label = "Delayed" if entry.delayed and status is EntryStatus.UPCOMING else status.label
        row.add_suffix(Gtk.Label(label=label, valign=Gtk.Align.CENTER, css_classes=["cal-status", *self._status_classes(entry, now)]))
        row.connect("activated", lambda *_: self._window.show_anime(entry.anime_id))
        return row

    def _day_heading(self, day: datetime.date) -> str:
        today = self._today()
        name = f"{day:%A} {day.day} {day:%B}"
        if day == today:
            return f"Today — {name}"
        if day == today + datetime.timedelta(days=1):
            return f"Tomorrow — {name}"
        return name

    def _week_columns(self, start: datetime.date, days: dict, now: int) -> Gtk.Widget:
        columns = Gtk.Box(homogeneous=True, vexpand=True, margin_top=6, margin_start=12, margin_end=12)
        today = self._today()
        for offset in range(7):
            day = start + datetime.timedelta(days=offset)
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, css_classes=["cal-column"])
            if offset == 0:
                column.add_css_class("first")
            heading = Gtk.Button(label=f"{day:%a} {day.day}", css_classes=["flat", "heading"])
            if day == today:
                heading.add_css_class("cal-today")
            heading.set_tooltip_text(f"Show {day:%A} {day.day} {day:%B}")
            heading.connect("clicked", lambda _b, d=day: self.show_day(d))
            column.append(heading)
            for entry in days.get(day, ()):
                column.append(self._card(entry, now))
            if day not in days:
                column.append(Gtk.Label(label="—", css_classes=["dim-label"]))
            columns.append(column)
        return columns

    def _agenda(self, start: datetime.date, end: datetime.date, days: dict, now: int, show_empty_days: bool) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=12)
        today = self._today()
        day = start
        while day < end:
            entries = days.get(day, [])
            if entries or show_empty_days or day == today:
                group = Adw.PreferencesGroup(title=self._day_heading(day))
                for entry in entries:
                    group.add(self._entry_row(entry, now))
                if not entries:
                    group.add(Adw.ActionRow(title="Nothing scheduled", css_classes=["dim-label"]))
                box.append(group)
            day += datetime.timedelta(days=1)
        if not days and not show_empty_days:
            box.append(Gtk.Label(label="Nothing scheduled this week", css_classes=["dim-label"]))
        return Adw.Clamp(child=box, maximum_size=700, margin_start=12, margin_end=12)

    def _month_grid(self, start: datetime.date, end: datetime.date, days: dict, now: int) -> Gtk.Widget:
        grid = Gtk.Grid(column_homogeneous=True, margin_top=6, margin_start=12, margin_end=12)
        first_weekday = self._display.first_weekday
        for column in range(7):
            name = (datetime.date(2024, 1, 1) + datetime.timedelta(days=(first_weekday + column) % 7)).strftime("%a")
            grid.attach(
                Gtk.Label(label=name, margin_bottom=4, css_classes=["caption-heading", "dim-label"]), column, 0, 1, 1
            )
        today = self._today()
        day, index = start, 0
        while day < end:
            cell = self._month_cell(day, days.get(day, []), now, today)
            if index % 7 == 0:
                cell.add_css_class("first-col")
            if index < 7:
                cell.add_css_class("first-row")
            grid.attach(cell, index % 7, 1 + index // 7, 1, 1)
            day += datetime.timedelta(days=1)
            index += 1
        return grid

    def _month_cell(self, day: datetime.date, entries: list[CalendarEntry], now: int, today: datetime.date) -> Gtk.Widget:
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.START)
        number = Gtk.Label(label=str(day.day), xalign=0, css_classes=["caption-heading"])
        if day == today:
            number.add_css_class("cal-today")
        content.append(number)
        if self.compact:
            if entries:
                dots = Gtk.Box(spacing=2)
                for entry in entries[:4]:
                    dots.append(Gtk.Box(valign=Gtk.Align.CENTER, css_classes=["cal-dot", *self._status_classes(entry, now)]))
                content.append(dots)
        else:
            for entry in entries[:_MONTH_CELL_ITEMS]:
                item = Gtk.Box(spacing=4)
                item.append(Gtk.Box(valign=Gtk.Align.CENTER, css_classes=["cal-dot", *self._status_classes(entry, now)]))
                item.append(Gtk.Label(label=f"{self._name(entry)} {entry.number}", ellipsize=Pango.EllipsizeMode.END, xalign=0, css_classes=["caption"]))
                content.append(item)
            if len(entries) > _MONTH_CELL_ITEMS:
                content.append(
                    Gtk.Label(label=f"+{len(entries) - _MONTH_CELL_ITEMS} more", xalign=0, css_classes=["caption", "dim-label"])
                )
        cell = Gtk.Button(child=content, css_classes=["flat", "cal-cell"])
        if self.compact:
            cell.add_css_class("compact")
        if day.month != self._anchor.month:
            cell.add_css_class("cal-outside")
        summary = "; ".join(f"{self._name(e)} episode {e.number}" for e in entries) or "nothing scheduled"
        cell.set_tooltip_text(f"{day:%A} {day.day} {day:%B}: {summary}")
        cell.connect("clicked", lambda *_: self.show_day(day))
        return cell

    def _undated_group(self, now: int) -> Adw.PreferencesGroup | None:
        shows = self._window.schedule.undated_shows(now)
        if not shows:
            return None
        group = Adw.PreferencesGroup(
            title="Airing, Date Unknown",
            description="No upcoming airing time is known. You can set one from the show’s menu.",
        )
        for show in shows:
            row = Adw.ActionRow(title=self._name(show), use_markup=False, activatable=True)
            row.add_prefix(cover(show.cover_path, self._name(show), 32, 45))
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda _r, anime_id=show.anime_id: self._window.show_anime(anime_id))
            group.add(row)
        return group

    def _unwatched_page(self, now: int) -> Gtk.Widget:
        entries = self._window.schedule.aired_unwatched(now)
        if not entries:
            return Adw.StatusPage(
                icon_name="emblem-ok-symbolic",
                title="All Caught Up",
                description="Every episode that has aired is marked watched.",
            )
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=12, margin_bottom=24)
        by_show = sorted(entries, key=lambda e: (self._name(e).casefold(), e.anime_id, e.number))
        for (title, _anime_id), show_entries in groupby(by_show, key=lambda e: (self._name(e), e.anime_id)):
            show_entries = list(show_entries)
            count = len(show_entries)
            mark_all = Gtk.Button(label="Mark All Watched", valign=Gtk.Align.CENTER, css_classes=["flat"])
            episode_ids = [e.episode_id for e in show_entries]
            mark_all.connect("clicked", lambda _b, ids=episode_ids: self._mark_watched(ids))
            group = Adw.PreferencesGroup(
                title=GLib.markup_escape_text(title),  # group titles are markup
                description=f"{count} unwatched episode{'' if count == 1 else 's'}",
                header_suffix=mark_all,
            )
            for entry in show_entries:
                group.add(self._entry_row(entry, now, in_show_group=True))
            box.append(group)
        return Adw.Clamp(child=box, maximum_size=700, margin_start=12, margin_end=12)

    def _legend(self) -> Gtk.Widget:
        legend = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE, halign=Gtk.Align.CENTER, max_children_per_line=5,
            column_spacing=12, margin_top=4, margin_bottom=4,
        )
        for classes, label in (
            (["upcoming"], "Upcoming"),
            (["available"], "Aired"),
            (["delayed"], "Delayed"),
            (["watched"], "Watched"),
            (["estimated"], "Estimated time"),
        ):
            item = Gtk.Box(spacing=6)
            item.append(Gtk.Box(valign=Gtk.Align.CENTER, css_classes=["cal-dot", *classes]))
            item.append(Gtk.Label(label=label, css_classes=["caption"]))
            legend.append(item)
        return legend

    # Watched state

    def _set_watched(self, episode_id: int, watched: bool) -> None:
        state = WatchState.WATCHED if watched else WatchState.UNWATCHED
        self._window.repository.set_watch_state([episode_id], state)
        # Rebuilding inside the checkbox's own signal handler would destroy it mid-emission.
        GLib.idle_add(self._after_watch_change)

    def _mark_watched(self, episode_ids: list[int]) -> None:
        self._window.repository.set_watch_state(episode_ids, WatchState.WATCHED)
        GLib.idle_add(self._after_watch_change)

    def _toggle_watched(self, episode_id: int) -> None:
        current = self._window.repository.watch_state(episode_id)
        self._set_watched(episode_id, current is not WatchState.WATCHED)

    def _after_watch_change(self) -> bool:
        self._window.library_changed()
        return GLib.SOURCE_REMOVE

"""Desktop notifications when followed episodes become available."""

from __future__ import annotations

import time
from collections.abc import Callable

from gi.repository import Gio, GLib

from anical.db.schedule import ScheduleRepository
from anical.db.settings import NOTIFICATIONS_ENABLED, SettingsRepository
from anical.domain.calendar import CalendarEntry
from anical.domain.notifications import MAX_AGE_S, SUMMARY_THRESHOLD, to_announce
from anical.ui.display import DisplaySettings

CHECK_INTERVAL_S = 5 * 60

Send = Callable[[str, Gio.Notification], None]


class Notifier:
    def __init__(
        self,
        schedule: ScheduleRepository,
        settings: SettingsRepository,
        display: DisplaySettings,
        send: Send,
        clock: Callable[[], int] = lambda: int(time.time()),
    ):
        self._schedule = schedule
        self._settings = settings
        self._display = display
        self._send = send
        self._clock = clock
        self._timer = 0

    @property
    def enabled(self) -> bool:
        return self._settings.get(NOTIFICATIONS_ENABLED, True) is not False

    def start(self) -> None:
        self.check()
        if not self._timer:
            self._timer = GLib.timeout_add_seconds(CHECK_INTERVAL_S, self._on_timer)

    def stop(self) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def _on_timer(self) -> bool:
        self.check()
        return GLib.SOURCE_CONTINUE

    def check(self) -> int:
        """Announce newly available episodes. Returns how many were announced."""
        now = self._clock()
        lag = self._display.availability_lag_s
        candidates = self._schedule.unannounced_entries(now - MAX_AGE_S - lag, now + 1)
        due = to_announce(candidates, now, lag)
        if not due:
            return 0
        # Recorded even when notifications are off, so turning them on later
        # doesn't announce a backlog.
        self._schedule.mark_announced([e.episode_id for e in due], now)
        if self.enabled:
            if len(due) > SUMMARY_THRESHOLD:
                self._send("available-summary", self._summary(due))
            else:
                for entry in due:
                    self._send(f"available-{entry.episode_id}", self._single(entry))
        return len(due)

    def _name(self, entry: CalendarEntry) -> str:
        return entry.display_title(self._display.english_titles)

    def _single(self, entry: CalendarEntry) -> Gio.Notification:
        notification = Gio.Notification.new(f"{self._name(entry)}: Episode {entry.number}")
        notification.set_body("New episode, probably available to watch now")
        notification.set_default_action_and_target("app.show-anime", GLib.Variant.new_int64(entry.anime_id))
        notification.add_button_with_target("Play", "app.play-episode", GLib.Variant.new_int64(entry.episode_id))
        return notification

    def _summary(self, due: list[CalendarEntry]) -> Gio.Notification:
        notification = Gio.Notification.new(f"{len(due)} New Episodes Available")
        lines = [f"{self._name(e)}: Episode {e.number}" for e in due[:6]]
        if len(due) > 6:
            lines.append(f"…and {len(due) - 6} more")
        notification.set_body("\n".join(lines))
        notification.set_default_action("app.show-unwatched")
        return notification

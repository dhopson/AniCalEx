"""How dates and times are shown: time zone, week start, clock format."""

from __future__ import annotations

import zoneinfo
from zoneinfo import ZoneInfo

from gi.repository import Gio, GObject

from anical.db.settings import (
    AVAILABILITY_LAG_HOURS,
    DISPLAY_ENGLISH_TITLES,
    DISPLAY_TIMEZONE,
    DISPLAY_WEEK_START,
    SettingsRepository,
)
from anical.domain.calendar import DEFAULT_AVAILABILITY_LAG_S
from anical.platform.timezone import system_timezone_name

_GNOME_INTERFACE = "org.gnome.desktop.interface"


class DisplaySettings(GObject.Object):
    """Emits `changed` whenever a display preference changes."""

    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, settings: SettingsRepository):
        super().__init__()
        self._settings = settings
        self._clock_format = None
        source = Gio.SettingsSchemaSource.get_default()
        if source is not None and source.lookup(_GNOME_INTERFACE, True) is not None:
            self._clock_format = Gio.Settings.new(_GNOME_INTERFACE)
            self._clock_format.connect("changed::clock-format", lambda *_: self.emit("changed"))

    @property
    def timezone_name(self) -> str | None:
        """The explicitly chosen zone, or None for the system zone."""
        return self._settings.get(DISPLAY_TIMEZONE)

    @property
    def timezone(self) -> ZoneInfo:
        for name in (self.timezone_name, system_timezone_name()):
            if not name:
                continue
            try:
                return ZoneInfo(name)
            except (zoneinfo.ZoneInfoNotFoundError, ValueError):
                continue
        return ZoneInfo("UTC")

    @property
    def first_weekday(self) -> int:
        return 6 if self._settings.get(DISPLAY_WEEK_START) == 6 else 0

    @property
    def availability_lag_s(self) -> int:
        hours = self._settings.get(AVAILABILITY_LAG_HOURS)
        return int(hours * 3600) if isinstance(hours, (int, float)) and 0 <= hours <= 72 else DEFAULT_AVAILABILITY_LAG_S

    @property
    def english_titles(self) -> bool:
        return self._settings.get(DISPLAY_ENGLISH_TITLES) is True

    @property
    def use_24h(self) -> bool:
        return self._clock_format is None or self._clock_format.get_string("clock-format") != "12h"

    def get(self, key: str):
        return self._settings.get(key)

    def set(self, key: str, value) -> None:
        self._settings.set(key, value)
        self.emit("changed")

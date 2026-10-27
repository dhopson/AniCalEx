"""The system's IANA time zone name on every operating system.

GLib names the local zone differently per OS (spike S1):
- Linux: the IANA name, e.g. "Europe/Berlin"
- macOS: a path, e.g. "/usr/share/zoneinfo/Asia/Tokyo"
- Windows: a Windows name, e.g. "Tokyo Standard Time", mapped to IANA with tzlocal's table
"""

from __future__ import annotations

import contextlib
import functools
import os
import zoneinfo
from collections.abc import Callable, Iterable, Mapping


@functools.cache
def available_timezones() -> frozenset[str]:
    return frozenset(zoneinfo.available_timezones())  # scans the zoneinfo database: cache it


def _windows_names() -> Mapping[str, str]:
    try:
        from tzlocal.windows_tz import win_tz
    except ImportError:
        return {}
    return win_tz


def resolve(
    candidates: Iterable[str | None],
    available: frozenset[str],
    windows_names: Mapping[str, str] | Callable[[], Mapping[str, str]] = _windows_names,
) -> str:
    """The first candidate that names a known zone, after undoing the OS's
    spelling of it. UTC if none does."""
    for name in candidates:
        if not name:
            continue
        name = name.lstrip(":")
        if "zoneinfo/" in name:
            name = name.split("zoneinfo/", 1)[1]
        if name not in available:
            names = windows_names() if callable(windows_names) else windows_names
            name = names.get(name, name)
        if name in available:
            return name
    return "UTC"


@functools.cache
def system_timezone_name() -> str:
    from gi.repository import GLib

    candidates = [GLib.TimeZone.new_local().get_identifier(), os.environ.get("TZ")]
    with contextlib.suppress(OSError):
        candidates.append(os.path.realpath("/etc/localtime"))
    return resolve(candidates, available_timezones())

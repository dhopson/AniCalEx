"""Operating-system specifics behind small, testable functions.

Everything that differs between Linux, Windows and macOS (where files live,
how secrets are stored, how notifications are sent, the local time zone)
goes through this package, so the rest of AniCal doesn't branch on the OS.
"""

from __future__ import annotations

import sys
from enum import StrEnum


class System(StrEnum):
    LINUX = "linux"
    WINDOWS = "windows"
    MACOS = "macos"


def current() -> System:
    if sys.platform == "win32":
        return System.WINDOWS
    if sys.platform == "darwin":
        return System.MACOS
    return System.LINUX

"""Where AniCal keeps its files on each operating system.

| | Linux | Windows | macOS |
|---|---|---|---|
| data | `$XDG_DATA_HOME/anical` | `%LOCALAPPDATA%\\AniCal` | `~/Library/Application Support/AniCal` |
| cache | `$XDG_CACHE_HOME/anical` | `%LOCALAPPDATA%\\AniCal\\cache` | `~/Library/Caches/AniCal` |
| runtime | `$XDG_RUNTIME_DIR/anical` | `%TEMP%\\AniCal` | `$TMPDIR/AniCal` |

`$ANICAL_DATA_DIR` (development and tests) puts all three under one folder.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from anical.platform import System, current


@dataclass(frozen=True)
class AppPaths:
    """Where AniCal keeps its library, cache and runtime files on this system."""

    data: Path
    cache: Path
    runtime: Path


def app_paths(
    system: System | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> AppPaths:
    system = system or current()
    env = os.environ if env is None else env
    home = home or Path.home()
    if override := env.get("ANICAL_DATA_DIR"):
        base = Path(override)
        return AppPaths(base, base / "cache", base / "run")
    if system is System.WINDOWS:
        local = Path(env.get("LOCALAPPDATA") or home / "AppData" / "Local") / "AniCal"
        return AppPaths(local, local / "cache", Path(env.get("TEMP") or tempfile.gettempdir()) / "AniCal")
    if system is System.MACOS:
        library = home / "Library"
        return AppPaths(
            library / "Application Support" / "AniCal",
            library / "Caches" / "AniCal",
            Path(env.get("TMPDIR") or tempfile.gettempdir()) / "AniCal",
        )
    from gi.repository import GLib  # XDG rules, exactly as before the port

    return AppPaths(
        Path(GLib.get_user_data_dir()) / "anical",
        Path(GLib.get_user_cache_dir()) / "anical",
        Path(GLib.get_user_runtime_dir()) / "anical",
    )

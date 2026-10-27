"""Finding ani-cli, mpv and (on Windows) Git Bash.

A setting always wins. Otherwise AniCal looks where each OS's package
manager puts them, because apps started from the Dock or the Start menu
don't see the PATH a terminal has:
- Linux: PATH.
- macOS: PATH, then Homebrew (/opt/homebrew, /usr/local).
- Windows: scoop (~\\scoop\\apps\\...\\current), then PATH. ani-cli is a shell
  script there, run by Git for Windows' bash.exe, never System32's WSL bash.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Iterable
from pathlib import Path

from anical.platform import System, current

_HOMEBREW = (Path("/opt/homebrew/bin"), Path("/usr/local/bin"))


def _first_file(candidates: Iterable[Path | str | None]) -> str | None:
    return next((str(c) for c in candidates if c and Path(c).is_file()), None)


def _configured(value: str | None, which: Callable[[str], str | None]) -> str | None:
    """A configured path must exist; a bare name is looked up on PATH."""
    if not value:
        return None
    if os.sep in value or (os.altsep and os.altsep in value):
        return value if Path(value).is_file() else None
    return which(value)


def find_ani_cli(
    configured: str | None = None,
    system: System | None = None,
    home: Path | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> str | None:
    system = system or current()
    home = home or Path.home()
    if configured and configured != "ani-cli":
        return _configured(configured, which)
    if system is System.WINDOWS:
        # scoop's shims are wrappers; bash needs the script itself.
        return _first_file([home / "scoop" / "apps" / "ani-cli" / "current" / "ani-cli"])
    found = which("ani-cli")
    if found or system is not System.MACOS:
        return found
    return _first_file(d / "ani-cli" for d in _HOMEBREW)


def find_player(
    configured: str | None = None,
    system: System | None = None,
    home: Path | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> str | None:
    system = system or current()
    home = home or Path.home()
    if configured and configured != "mpv":
        return _configured(configured, which)
    if system is System.WINDOWS:
        return _first_file([home / "scoop" / "apps" / "mpv" / "current" / "mpv.exe"]) or which("mpv")
    found = which("mpv")
    if found or system is not System.MACOS:
        return found
    return _first_file(d / "mpv" for d in _HOMEBREW)


def _git_install_dirs() -> list[Path]:
    try:
        import winreg
    except ImportError:
        return []
    found = []
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, r"SOFTWARE\GitForWindows") as key:
                found.append(Path(winreg.QueryValueEx(key, "InstallPath")[0]))
        except OSError:
            pass
    return found


def find_bash(
    configured: str | None = None,
    home: Path | None = None,
    env: dict[str, str] | None = None,
    git_dirs: Callable[[], list[Path]] = _git_install_dirs,
) -> str | None:
    """Git for Windows' bash.exe (Windows only)."""
    if configured:
        return configured if Path(configured).is_file() else None
    home = home or Path.home()
    env = os.environ if env is None else env
    dirs = [
        *git_dirs(),
        home / "scoop" / "apps" / "git" / "current",
        Path(env.get("ProgramFiles") or r"C:\Program Files") / "Git",
        Path(env.get("LOCALAPPDATA") or home / "AppData" / "Local") / "Programs" / "Git",
    ]
    return _first_file(d / "bin" / "bash.exe" for d in dirs)

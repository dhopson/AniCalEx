"""Finding ani-cli, a video player and (on Windows) Git Bash.

A setting always wins. Otherwise AniCal looks where each OS's package
manager puts them, because apps started from the Dock or the Start menu
don't see the PATH a terminal has:
- Linux: PATH.
- macOS: PATH, then Homebrew (/opt/homebrew, /usr/local).
- Windows: scoop (~\\scoop\\apps\\...\\current, or $SCOOP, $SCOOP_GLOBAL and
  %ProgramData%\\scoop), then PATH. ani-cli is a shell script there, run by Git
  for Windows' bash.exe, never System32's WSL bash. bash can't run scoop's shims
  (ani-cli.cmd, .ps1, .exe), so a shim found on PATH or set by hand is followed
  back to the script it stands for.
Players also come from installers: VLC.app and IINA.app on macOS, and VLC in
Program Files on Windows.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from anical.domain.playback import Player
from anical.platform import System, current

_HOMEBREW = (Path("/opt/homebrew/bin"), Path("/usr/local/bin"))

# The players each system offers, the one to suggest first.
_PLAYERS = {
    System.LINUX: (Player.MPV, Player.VLC),
    System.MACOS: (Player.IINA, Player.MPV, Player.VLC),
    System.WINDOWS: (Player.VLC, Player.MPV),
}
_MACOS_BUNDLES = {
    Player.VLC: "VLC.app/Contents/MacOS/VLC",
    Player.IINA: "IINA.app/Contents/MacOS/iina-cli",
}


def _first_file(candidates: Iterable[Path | str | None]) -> str | None:
    return next((str(c) for c in candidates if c and Path(c).is_file()), None)


def _configured(value: str | None, which: Callable[[str], str | None]) -> str | None:
    """A configured path must exist; a bare name is looked up on PATH."""
    if not value:
        return None
    if os.sep in value or (os.altsep and os.altsep in value):
        return value if Path(value).is_file() else None
    return which(value)


def _scoop_roots(home: Path, env: Mapping[str, str]) -> list[Path]:
    """Where scoop can live: a custom folder, a global install, or the default."""
    roots = [Path(env[key]) for key in ("SCOOP", "SCOOP_GLOBAL") if env.get(key)]
    roots.append(home / "scoop")
    if program_data := env.get("ProgramData"):
        roots.append(Path(program_data) / "scoop")
    return roots


def _unshim(path: Path) -> Path:
    """The script a scoop shim (<root>\\shims\\ani-cli.cmd, .ps1, .exe) stands for, else `path`."""
    if path.parent.name.lower() != "shims":
        return path
    script = path.parent.parent / "apps" / path.stem / "current" / path.stem
    return script if script.is_file() else path


def _windows_path_ani_cli(env: Mapping[str, str]) -> str | None:
    """ani-cli on PATH: the script itself, as a git clone leaves it, or a scoop shim."""
    for folder in filter(None, env.get("PATH", "").split(os.pathsep)):
        for name in ("ani-cli", "ani-cli.cmd", "ani-cli.ps1", "ani-cli.exe"):
            candidate = Path(folder) / name
            if candidate.is_file():
                script = _unshim(candidate)
                if not script.suffix:  # bash runs the script, not a wrapper
                    return str(script)
    return None


def find_ani_cli(
    configured: str | None = None,
    system: System | None = None,
    home: Path | None = None,
    which: Callable[[str], str | None] = shutil.which,
    env: Mapping[str, str] | None = None,
) -> str | None:
    system = system or current()
    home = home or Path.home()
    env = os.environ if env is None else env
    if configured and configured != "ani-cli":
        found = _configured(configured, which)
        return str(_unshim(Path(found))) if found and system is System.WINDOWS else found
    if system is System.WINDOWS:
        scoop = (root / "apps" / "ani-cli" / "current" / "ani-cli" for root in _scoop_roots(home, env))
        return _first_file(scoop) or _windows_path_ani_cli(env)
    found = which("ani-cli")
    if found or system is not System.MACOS:
        return found
    return _first_file(d / "ani-cli" for d in _HOMEBREW)


def players(system: System | None = None) -> tuple[Player, ...]:
    """The players this system offers, the one to suggest first."""
    return _PLAYERS[system or current()]


def _windows_player_files(player: Player, home: Path, env: Mapping[str, str]) -> list[Path]:
    name = f"{player.value}.exe"
    files = [home / "scoop" / "apps" / player.value / "current" / name]
    if player is Player.VLC:
        folders = (env.get(key) for key in ("ProgramFiles", "ProgramFiles(x86)"))
        files += [Path(folder) / "VideoLAN" / "VLC" / name for folder in folders if folder]
    return files


def _macos_player_files(player: Player, home: Path) -> list[Path]:
    files = [d / player.value for d in _HOMEBREW]
    if bundle := _MACOS_BUNDLES.get(player):
        files += [folder / bundle for folder in (Path("/Applications"), home / "Applications")]
    return files


def find_player(
    configured: str | None = None,
    system: System | None = None,
    home: Path | None = None,
    which: Callable[[str], str | None] = shutil.which,
    player: Player = Player.MPV,
    env: Mapping[str, str] | None = None,
) -> str | None:
    system = system or current()
    home = home or Path.home()
    if configured and configured != player.value:
        return _configured(configured, which)
    if system is System.WINDOWS:
        return _first_file(_windows_player_files(player, home, os.environ if env is None else env)) or which(
            player.value
        )
    found = which(player.value)
    if found or system is not System.MACOS:
        return found
    return _first_file(_macos_player_files(player, home))


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

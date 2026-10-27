"""Opening AniCal's install scripts in a terminal window the user can watch.

The setup wizard's Install buttons use this. The scripts live in
anical/data/setup; each one explains what it will do and asks before doing it,
so they run in a visible terminal and never in the background:
- Linux: the user's terminal (xdg-terminal-exec, then common terminals).
- macOS: Terminal.app, through a .command file.
- Windows: a PowerShell console window.
"""

from __future__ import annotations

import importlib.resources
import os
import shlex
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from anical.platform import System, current

SCRIPTS = {
    System.LINUX: "install-linux.sh",
    System.MACOS: "install-macos.command",
    System.WINDOWS: "install-windows.ps1",
}

# (program, arguments placed before the command to run)
LINUX_TERMINALS = (
    ("xdg-terminal-exec", []),
    ("kgx", ["--"]),
    ("gnome-terminal", ["--"]),
    ("konsole", ["-e"]),
    ("xfce4-terminal", ["-x"]),
    ("kitty", []),
    ("alacritty", ["-e"]),
    ("foot", []),
    ("wezterm", ["start", "--"]),
    ("xterm", ["-e"]),
)


class TerminalError(Exception):
    """No way to open a terminal window was found."""


def install_script(folder: Path, system: System | None = None) -> Path:
    """Copy this system's install script into `folder` (executable) and return it."""
    system = system or current()
    name = SCRIPTS[system]
    source = importlib.resources.files("anical") / "data" / "setup" / name
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    target.write_bytes(source.read_bytes())
    target.chmod(0o755)
    return target


def terminal_command(
    script: Path,
    programs: list[str],
    system: System | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> list[str]:
    """The command that shows `script programs...` in a new terminal window."""
    system = system or current()
    if system is System.WINDOWS:
        return ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), *programs]
    if system is System.MACOS:
        # Terminal.app opens .command files but can't pass them arguments, so
        # a small launcher next to the script carries them.
        launcher = script.with_name(f"run-{'-'.join(programs) or 'all'}.command")
        launcher.write_text(f"#!/bin/bash\nexec /bin/bash {shlex.join([str(script), *programs])}\n", encoding="utf-8")
        launcher.chmod(0o755)
        return ["open", "-a", "Terminal", str(launcher)]
    for program, flags in LINUX_TERMINALS:
        if which(program):
            return [program, *flags, "bash", str(script), *programs]
    raise TerminalError("No terminal app was found. Run the install steps from the README instead.")


def open_install_terminal(folder: Path, programs: list[str], system: System | None = None) -> None:
    """Open the install script for `programs` in a new terminal window.
    Raises TerminalError or OSError when it can't be opened."""
    system = system or current()
    command = terminal_command(install_script(folder, system), programs, system)
    subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=os.name != "nt",
        creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
    )

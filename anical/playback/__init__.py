"""Helper files handed to ani-cli and mpv."""

from __future__ import annotations

import importlib.resources
import os
import sys
from pathlib import Path

from anical.domain.playback import HistoryEntry, PlaybackError, parse_history


def install_helpers(directory: Path) -> tuple[Path, Path]:
    """Write the picker and the mpv progress script into `directory`.

    Returns (picker, progress_script). Rewritten on every start so they always
    match the running version of AniCal.
    """
    if any(c.isspace() for c in sys.executable):
        raise PlaybackError(f"The Python path contains spaces, which a script header can’t express: {sys.executable}")
    directory.mkdir(parents=True, exist_ok=True)
    data = importlib.resources.files("anical.playback") / "data"

    picker = directory / "anical-pick"
    source = (data / "anical-pick.py").read_text(encoding="utf-8")
    _write(picker, f"#!{sys.executable} -I\n{source}", mode=0o755)

    progress_script = directory / "anical-progress.lua"
    _write(progress_script, (data / "anical-progress.lua").read_text(encoding="utf-8"), mode=0o644)
    return picker, progress_script


def history_path() -> Path:
    """Where ani-cli keeps its history (same rules as ani-cli itself)."""
    directory = os.environ.get("ANI_CLI_HIST_DIR") or os.path.join(
        os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "ani-cli"
    )
    return Path(directory) / "ani-hsts"


def read_history(path: Path | None = None) -> list[HistoryEntry]:
    """Read-only: AniCal never writes ani-cli's history."""
    try:
        return parse_history((path or history_path()).read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return []


def _write(path: Path, text: str, mode: int) -> None:
    partial = path.with_name(path.name + ".part")
    partial.write_text(text, encoding="utf-8")
    os.chmod(partial, mode)
    os.replace(partial, path)

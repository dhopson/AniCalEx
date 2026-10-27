"""Playback through ani-cli: launch command, outcome rules, history parsing.

How a launch works (verified against ani-cli 5.1.0 and 5.1.6, D14 and D23):
- `ani-cli --no-detach --exit-after-play -e N QUERY` searches, plays episode
  N in the foreground, and exits with the player's exit code. On Windows it
  runs as `bash.exe ani-cli ...` (Git for Windows).
- AniCal's helpers are found by name on a PATH that AniCal prepends to, so
  no path has to survive ani-cli's word splitting (paths with spaces work):
  - ANI_CLI_MENU=anical-pick replaces fzf. It gets the search results as
    "N Title" lines and answers with the chosen line (only consulted when
    the search returns more than one result).
  - ANI_CLI_PLAYER=anical-mpv. ani-cli treats any player whose name contains
    "mpv" as mpv, so the shim gets mpv's options and starts the real mpv
    with a small script that records how far playback got and why it ended
    ("eof" or "quit"), because mpv exits 0 for both.

An episode is never marked watched just because it was launched.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import unicodedata
import urllib.parse
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

WATCHED_PERCENT = 90.0
MODES = ("sub", "dub")
QUALITIES = ("best", "1080p", "720p", "480p", "worst")

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b[78()][AB0-9]?")
_DIE = re.compile(r"\x1b\[1;31m(.*?)\x1b\[0m")  # ani-cli's die() prints errors in bold red
_NOT_RELEASED = ("Invalid episode", "Episode not released", "Out of range", "No sources found")
_NO_SHOW = ("No results found", "No exact match")


class PlaybackError(Exception):
    """A launch could not be prepared (missing ani-cli, unusable paths)."""


class Outcome(StrEnum):
    WATCHED = "watched"  # reached the end or the threshold
    OPENED = "opened"  # handed to a link or command; AniCal can't follow it
    PARTIAL = "partial"  # stopped partway
    UNKNOWN = "unknown"  # played, but the player reported no progress (not mpv)
    EPISODE_NOT_FOUND = "episode_not_found"
    SHOW_NOT_FOUND = "show_not_found"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True)
class Progress:
    reason: str
    percent: float | None = None
    position: float | None = None
    duration: float | None = None


@dataclass(frozen=True)
class PlaybackResult:
    outcome: Outcome
    percent: float | None = None
    message: str | None = None

    @property
    def stream_worked(self) -> bool:
        """The episode was actually playable (confirms availability)."""
        return self.outcome in (Outcome.WATCHED, Outcome.PARTIAL, Outcome.UNKNOWN)


@dataclass(frozen=True)
class PlaybackConfig:
    executable: str = "ani-cli"
    mode: str = "sub"
    quality: str = "best"
    auto_mark: bool = True
    player: str = "mpv"
    bash: str | None = None  # Windows: Git for Windows' bash.exe


@dataclass(frozen=True)
class PlaybackMapping:
    """How one library show is found in ani-cli."""

    anime_id: int
    query: str
    result_title: str | None = None  # the ani-cli search result chosen for this show
    episode_offset: int = 0
    mode: str | None = None  # None = the global setting

    def anicli_episode(self, number: int) -> int:
        return number + self.episode_offset


@dataclass(frozen=True)
class Launch:
    argv: list[str]
    env: dict[str, str]
    progress_path: Path


@dataclass(frozen=True)
class HistoryEntry:
    episode: str
    anicli_id: str
    title: str


def normalize_title(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def sanitize_query(title: str) -> str:
    """A search query ani-cli can put in a URL: ani-cli only turns spaces into '+',
    so characters like & # ? / would corrupt the request."""
    text = unicodedata.normalize("NFKC", title)
    cleaned = "".join(c if c.isalnum() or c.isspace() else " " for c in text)
    return " ".join(cleaned.split()) or title.strip()


def build_launch(
    config: PlaybackConfig,
    executable: str,
    mapping: PlaybackMapping,
    episode_number: int,
    session_dir: Path,
    helpers: Path,
    progress_script: Path,
    base_path: str = "",
) -> Launch:
    """The argument vector and environment for one playback. No shell is involved:
    the query is a single argument whatever characters it contains. `helpers` is
    the folder holding anical-pick and anical-mpv; it goes first on PATH."""
    episode = mapping.anicli_episode(episode_number)
    if episode < 1:
        raise PlaybackError(f"Episode {episode_number} with offset {mapping.episode_offset} is not a valid ani-cli episode")
    progress_path = session_dir / "progress.json"
    argv = ([config.bash] if config.bash else []) + [executable, "--no-detach", "--exit-after-play"]
    if (mapping.mode or config.mode) == "dub":
        argv.append("--dub")
    if config.quality != "best":
        argv += ["-q", config.quality]
    argv += ["-e", str(episode), mapping.query]
    env = {
        "PATH": os.pathsep.join(p for p in (str(helpers), base_path) if p),
        "ANI_CLI_MENU": "anical-pick",
        "ANI_CLI_PLAYER": "anical-mpv",
        "ANICAL_MPV": config.player,
        "ANICAL_PROGRESS_SCRIPT": str(progress_script),
        "ANICAL_PROGRESS_OUT": str(progress_path),
        "ANICAL_PICK_DIR": str(session_dir),
        "ANICAL_PICK_TITLE": mapping.result_title or "",
    }
    return Launch(argv, env, progress_path)


PLACEHOLDERS = ("title", "query", "episode", "mal_id")
_URL = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


@dataclass(frozen=True)
class External:
    """Where "Open a link or run a command" sends an episode: a URL to open, or
    a command to run (an argument vector; no shell)."""

    url: str | None = None
    argv: list[str] | None = None


def expand_external(template: str, values: dict[str, str], windows: bool = False) -> External:
    """Fill {title}, {query}, {episode} and {mal_id} into the user's template.
    A template that starts with a URL scheme (https:, or an app's own scheme)
    is a link, and the values are URL-encoded into it. Anything else is a
    command, split into arguments like a shell would, with each value staying
    inside its argument whatever it contains."""
    template = template.strip()
    if not template:
        raise PlaybackError("Set a link or command in Preferences → Playback")

    def fill(text: str, encode) -> str:
        for name in PLACEHOLDERS:
            text = text.replace("{" + name + "}", encode(values.get(name, "")))
        return text

    first = template.split(None, 1)[0]
    if _URL.match(first) and not (windows and re.match(r"^[A-Za-z]:[\\/]", first)):  # C:\... is a path
        return External(url=fill(template, lambda v: urllib.parse.quote(v, safe="")))
    try:
        # Windows paths use backslashes, which POSIX splitting treats as escapes.
        parts = shlex.split(template, posix=not windows)
    except ValueError as e:
        raise PlaybackError(f"Can’t read the command: {e}") from e
    if windows:
        parts = [p[1:-1] if len(p) > 1 and p[0] == p[-1] and p[0] in "\"'" else p for p in parts]
    return External(argv=[fill(part, str) for part in parts])


def parse_progress(text: str | None) -> Progress | None:
    if not text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("reason"), str):
        return None

    def number(key: str) -> float | None:
        value = data.get(key)
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else None

    return Progress(data["reason"], number("percent"), number("position"), number("duration"))


def error_message(stderr: str) -> str | None:
    """ani-cli's own error message (its last red line), else the last non-empty line."""
    dies = _DIE.findall(stderr or "")
    if dies:
        return _ANSI.sub("", dies[-1]).strip() or None
    lines = [line.strip() for line in _ANSI.sub("", stderr or "").splitlines() if line.strip()]
    return lines[-1] if lines else None


def decide(exit_code: int, progress: Progress | None, stderr: str) -> PlaybackResult:
    if progress is not None:
        if progress.reason == "eof" or (progress.percent is not None and progress.percent >= WATCHED_PERCENT):
            return PlaybackResult(Outcome.WATCHED, progress.percent)
        if progress.reason == "error" and not progress.position:
            return PlaybackResult(Outcome.FAILED, None, "The player couldn’t play the stream")
        if progress.position or progress.percent:
            return PlaybackResult(Outcome.PARTIAL, progress.percent)
        return PlaybackResult(Outcome.PARTIAL, 0.0)

    message = error_message(stderr)
    if message:
        if any(marker in message for marker in _NOT_RELEASED):
            return PlaybackResult(Outcome.EPISODE_NOT_FOUND, message=message)
        if any(marker in message for marker in _NO_SHOW):
            return PlaybackResult(Outcome.SHOW_NOT_FOUND, message=message)
        if "Invalid anime selection" in message:
            return PlaybackResult(Outcome.CANCELLED, message=message)
    if exit_code == 0:
        # Ran to completion without the mpv reporter (e.g. VLC): progress is unknown.
        return PlaybackResult(Outcome.UNKNOWN)
    return PlaybackResult(Outcome.FAILED, message=message or f"ani-cli exited with status {exit_code}")


def parse_history(text: str) -> list[HistoryEntry]:
    """ani-cli's history file: `episode<TAB>id<TAB>title` per show, last *launched* episode."""
    entries = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and all(p.strip() for p in parts):
            entries.append(HistoryEntry(*(p.strip() for p in parts)))
    return entries


def history_for(entries: list[HistoryEntry], result_title: str | None) -> HistoryEntry | None:
    if not result_title:
        return None
    wanted = normalize_title(result_title)
    return next((e for e in entries if normalize_title(e.title) == wanted), None)

"""Plays one episode at a time without blocking the UI: through ani-cli, or
by opening the user's link or command ("external")."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from gi.repository import Gio, GLib, GObject

from anical.db.playback import PlaybackRepository
from anical.db.repository import LibraryRepository
from anical.db.settings import (
    PLAYBACK_AUTO_MARK,
    PLAYBACK_BACKEND,
    PLAYBACK_BASH,
    PLAYBACK_EXECUTABLE,
    PLAYBACK_EXTERNAL,
    PLAYBACK_MODE,
    PLAYBACK_PLAYER,
    PLAYBACK_QUALITY,
    SettingsRepository,
)
from anical.domain.models import EpisodeKind, WatchSource, WatchState
from anical.domain.playback import (
    MODES,
    QUALITIES,
    Outcome,
    PlaybackConfig,
    PlaybackError,
    PlaybackMapping,
    PlaybackResult,
    build_launch,
    decide,
    expand_external,
    parse_progress,
    sanitize_query,
)
from anical.platform import System, current
from anical.platform.anicli import find_ani_cli, find_bash, find_player
from anical.playback import install_helpers

BACKENDS = ("ani-cli", "external")

_PICK_POLL_MS = 200


@dataclass
class PickRequest:
    """ani-cli found several shows; the user chooses (or cancels with None)."""

    anime_title: str
    query: str
    choices: list[tuple[str, str]]  # (index, title)
    respond: Callable[[str | None], None]


@dataclass(frozen=True)
class PlaybackReport:
    anime_id: int
    episode_id: int
    label: str
    query: str
    result: PlaybackResult
    marked: bool


@dataclass
class _Session:
    session_id: int
    anime_id: int
    episode_id: int
    label: str
    mapping: PlaybackMapping
    directory: Path
    progress_path: Path
    process: subprocess.Popen
    terminal: int | None = None
    asked: bool = False
    poll_id: int = 0
    answered: bool = False


class PlaybackController(GObject.Object):
    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "pick-requested": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "finished": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    def __init__(
        self,
        repository: LibraryRepository,
        playback: PlaybackRepository,
        settings: SettingsRepository,
        runtime_dir: Path,
    ):
        super().__init__()
        self._repo = repository
        self._playback = playback
        self._settings = settings
        self._runtime_dir = runtime_dir
        self._helpers: tuple[Path, Path] | None = None
        self._session: _Session | None = None
        playback.close_interrupted()

    # Settings

    @property
    def config(self) -> PlaybackConfig:
        mode = self._settings.get(PLAYBACK_MODE, "sub")
        quality = self._settings.get(PLAYBACK_QUALITY, "best")
        return PlaybackConfig(
            executable=self._settings.get(PLAYBACK_EXECUTABLE) or "ani-cli",
            mode=mode if mode in MODES else "sub",
            quality=quality if quality in QUALITIES else "best",
            auto_mark=self._settings.get(PLAYBACK_AUTO_MARK, True) is not False,
            player=self._settings.get(PLAYBACK_PLAYER) or "mpv",
            bash=self._settings.get(PLAYBACK_BASH),
        )

    @property
    def backend(self) -> str:
        backend = self._settings.get(PLAYBACK_BACKEND)
        return backend if backend in BACKENDS else "ani-cli"

    @property
    def external_template(self) -> str:
        return self._settings.get(PLAYBACK_EXTERNAL) or ""

    @property
    def backend_label(self) -> str:
        return "ani-cli" if self.backend == "ani-cli" else "your link or command"

    def resolve_executable(self) -> str | None:
        return find_ani_cli(self._settings.get(PLAYBACK_EXECUTABLE))

    def resolve_player(self) -> str | None:
        return find_player(self._settings.get(PLAYBACK_PLAYER))

    def resolve_bash(self) -> str | None:
        """Git Bash, which runs ani-cli on Windows. None elsewhere."""
        return find_bash(self._settings.get(PLAYBACK_BASH)) if current() is System.WINDOWS else None

    def mapping_for(self, anime_id: int, title: str) -> PlaybackMapping:
        return self._playback.mapping(anime_id) or PlaybackMapping(anime_id, sanitize_query(title))

    def set_setting(self, key: str, value) -> None:
        self._settings.set(key, value)
        self.emit("changed")

    @property
    def playing(self) -> str | None:
        return self._session.label if self._session else None

    # Playback

    def play(self, episode_id: int) -> str | None:
        """Start playback. Returns a message explaining why it couldn't start, or None."""
        if self._session:
            return f"Already playing {self._session.label}"
        episode = self._repo.episode(episode_id)
        anime = self._repo.get_anime(episode.anime_id)
        if episode.kind is not EpisodeKind.REGULAR:
            return "Only numbered episodes can be played"
        mapping = self.mapping_for(anime.id, anime.title)
        if self.backend == "external":
            return self._open_external(anime, episode, mapping)
        executable = self.resolve_executable()
        if executable is None:
            return "ani-cli wasn’t found. Set its location in Preferences → Playback."
        player = self.resolve_player()
        if player is None:
            return "mpv wasn’t found. Set its location in Preferences → Playback."
        windows = current() is System.WINDOWS
        bash = self.resolve_bash()
        if windows and bash is None:
            return "Git Bash wasn’t found. Install Git for Windows, or set bash.exe in Preferences → Playback."
        config = PlaybackConfig(
            self.config.executable, self.config.mode, self.config.quality, self.config.auto_mark, player, bash
        )
        if windows:
            executable = executable.replace("\\", "/")  # bash takes C:/... paths as they are
        directory = terminal = None
        try:
            if self._helpers is None:
                self._helpers = install_helpers(self._runtime_dir / "helpers")
            directory = Path(tempfile.mkdtemp(prefix="session-", dir=self._runtime_dir))
            launch = build_launch(
                config, executable, mapping, episode.number, directory, *self._helpers, os.environ.get("PATH", "")
            )
            process, terminal = _spawn(launch.argv, {**os.environ, **launch.env})
        except (PlaybackError, OSError) as e:
            _remove(directory, terminal)
            return str(e) if isinstance(e, PlaybackError) else f"Couldn’t start ani-cli: {e}"

        self._session = _Session(
            session_id=self._playback.start_session(episode.id),
            anime_id=anime.id,
            episode_id=episode.id,
            label=f"{anime.title}, {episode.label}",
            mapping=mapping,
            directory=directory,
            progress_path=launch.progress_path,
            process=process,
            terminal=terminal,
        )
        self._session.poll_id = GLib.timeout_add(_PICK_POLL_MS, self._poll_pick, self._session)
        session = self._session

        def wait() -> None:
            _stdout, stderr = process.communicate()
            text = (stderr or b"").decode("utf-8", errors="replace")
            GLib.idle_add(lambda: self._on_exit(session, process.returncode, text) and False)

        threading.Thread(target=wait, daemon=True).start()
        self.emit("changed")
        return None

    def _open_external(self, anime, episode, mapping: PlaybackMapping) -> str | None:
        """Hands the episode to the user's link or command. AniCal can't tell how
        far it was watched, so the user is offered "Mark Watched"."""
        values = {
            "title": anime.title,
            "query": mapping.query,
            "episode": str(mapping.anicli_episode(episode.number)),
            "mal_id": str(anime.mal_id or ""),
        }
        try:
            target = expand_external(self.external_template, values, windows=current() is System.WINDOWS)
            if target.url:
                Gio.AppInfo.launch_default_for_uri(target.url, None)
            else:
                subprocess.Popen(
                    target.argv,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=os.name != "nt",
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
        except PlaybackError as e:
            return str(e)
        except (OSError, GLib.Error) as e:
            return f"Couldn’t open it: {getattr(e, 'message', e)}"
        session_id = self._playback.start_session(episode.id)
        self._playback.finish_session(session_id, 0, Outcome.OPENED.value, None, None)
        self.emit(
            "finished",
            PlaybackReport(
                anime.id, episode.id, f"{anime.title}, {episode.label}", mapping.query,
                PlaybackResult(Outcome.OPENED), False,
            ),
        )
        return None

    def _poll_pick(self, session: _Session) -> bool:
        if session is not self._session:
            return GLib.SOURCE_REMOVE
        choices_path = session.directory / "choices.json"
        if session.asked or not choices_path.exists():
            return GLib.SOURCE_CONTINUE
        session.asked = True
        try:
            asked = json.loads(choices_path.read_text(encoding="utf-8"))
            choices = [(str(c["index"]), str(c["title"])) for c in asked["choices"]]
        except (OSError, ValueError, KeyError, TypeError):
            self._answer(session, None)
            return GLib.SOURCE_CONTINUE
        anime = self._repo.get_anime(session.anime_id)
        self.emit(
            "pick-requested",
            PickRequest(anime.title, session.mapping.query, choices, lambda index: self._answer(session, index, choices)),
        )
        return GLib.SOURCE_CONTINUE

    def _answer(self, session: _Session, index: str | None, choices: list[tuple[str, str]] = ()) -> None:
        if session.answered:
            return
        session.answered = True
        title = dict(choices).get(index) if index is not None else None
        if title:
            mapping = session.mapping
            self._playback.save_mapping(
                PlaybackMapping(mapping.anime_id, mapping.query, title, mapping.episode_offset, mapping.mode)
            )
        partial = session.directory / "answer.json.part"
        partial.write_text(json.dumps({"index": index if title else None}), encoding="utf-8")
        os.replace(partial, session.directory / "answer.json")

    def _on_exit(self, session: _Session, exit_code: int, stderr: str) -> None:
        try:
            progress = parse_progress(session.progress_path.read_text(encoding="utf-8"))
        except OSError:
            progress = None
        outcome = decide(exit_code, progress, stderr)
        self._playback.finish_session(
            session.session_id,
            exit_code,
            progress.reason if progress else outcome.outcome.value,
            outcome.percent,
            outcome.message,
        )
        marked = False
        if outcome.outcome is Outcome.WATCHED and self.config.auto_mark:
            self._repo.set_watch_state([session.episode_id], WatchState.WATCHED, WatchSource.PLAYBACK)
            marked = True
        if session.poll_id:
            GLib.source_remove(session.poll_id)
        _remove(session.directory, session.terminal)
        self._session = None
        self.emit("changed")
        self.emit(
            "finished",
            PlaybackReport(session.anime_id, session.episode_id, session.label, session.mapping.query, outcome, marked),
        )

    def mark_watched(self, episode_id: int) -> None:
        self._repo.set_watch_state([episode_id], WatchState.WATCHED, WatchSource.PLAYBACK)


def _spawn(argv: list[str], env: dict[str, str]) -> tuple[subprocess.Popen, int | None]:
    """Start ani-cli with stderr captured. Returns the process and, on Linux and
    macOS, the controlling end of the pseudo-terminal given to it as stdin:
    without one, ani-cli replaces ANI_CLI_MENU with rofi or dmenu whenever
    either is installed. It stays open until playback ends. On Windows ani-cli
    runs in Git Bash, where neither exists, and no console window is shown."""
    if not hasattr(os, "openpty"):
        process = subprocess.Popen(
            argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return process, None
    controller, child_end = os.openpty()
    try:
        process = subprocess.Popen(argv, env=env, stdin=child_end, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except OSError:
        os.close(controller)
        raise
    finally:
        os.close(child_end)
    return process, controller


def _remove(directory: Path | None, terminal: int | None = None) -> None:
    if directory is not None:
        shutil.rmtree(directory, ignore_errors=True)
    if terminal is not None:
        os.close(terminal)

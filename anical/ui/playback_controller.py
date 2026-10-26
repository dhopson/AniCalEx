"""Runs ani-cli for one episode at a time without blocking the UI."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from gi.repository import Gio, GLib, GObject

from anical.db.playback import PlaybackRepository
from anical.db.repository import LibraryRepository
from anical.db.settings import PLAYBACK_AUTO_MARK, PLAYBACK_EXECUTABLE, PLAYBACK_MODE, PLAYBACK_QUALITY, SettingsRepository
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
    parse_progress,
    sanitize_query,
)
from anical.playback import install_helpers

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
    process: Gio.Subprocess
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
        )

    def resolve_executable(self) -> str | None:
        executable = self.config.executable
        if os.sep in executable:
            return executable if os.access(executable, os.X_OK) else None
        return GLib.find_program_in_path(executable)

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
            return "Only numbered episodes can be played through ani-cli"
        executable = self.resolve_executable()
        if executable is None:
            return "ani-cli wasn’t found. Set its location in Preferences → Playback."
        mapping = self.mapping_for(anime.id, anime.title)
        directory = None
        try:
            if self._helpers is None:
                self._helpers = install_helpers(self._runtime_dir / "helpers")
            directory = Path(tempfile.mkdtemp(prefix="session-", dir=self._runtime_dir))
            launch = build_launch(self.config, executable, mapping, episode.number, directory, *self._helpers)
            launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_PIPE)
            for name, value in launch.env.items():
                launcher.setenv(name, value, True)
            process = launcher.spawnv(launch.argv)
        except (PlaybackError, OSError) as e:
            _remove(directory)
            return str(e)
        except GLib.Error as e:
            _remove(directory)
            return f"Couldn’t start ani-cli: {e.message}"

        self._session = _Session(
            session_id=self._playback.start_session(episode.id),
            anime_id=anime.id,
            episode_id=episode.id,
            label=f"{anime.title}, {episode.label}",
            mapping=mapping,
            directory=directory,
            progress_path=launch.progress_path,
            process=process,
        )
        self._session.poll_id = GLib.timeout_add(_PICK_POLL_MS, self._poll_pick, self._session)
        process.communicate_async(None, None, self._on_exit, self._session)
        self.emit("changed")
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

    def _on_exit(self, process: Gio.Subprocess, result: Gio.AsyncResult, session: _Session) -> None:
        stderr = ""
        try:
            _ok, _stdout, stderr_bytes = process.communicate_finish(result)
            if stderr_bytes is not None:
                stderr = stderr_bytes.get_data().decode("utf-8", errors="replace")
        except GLib.Error:
            pass
        exit_code = process.get_exit_status() if process.get_if_exited() else -1
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
        _remove(session.directory)
        self._session = None
        self.emit("changed")
        self.emit(
            "finished",
            PlaybackReport(session.anime_id, session.episode_id, session.label, session.mapping.query, outcome, marked),
        )

    def mark_watched(self, episode_id: int) -> None:
        self._repo.set_watch_state([episode_id], WatchState.WATCHED, WatchSource.PLAYBACK)


def _remove(directory: Path | None) -> None:
    if directory is not None:
        shutil.rmtree(directory, ignore_errors=True)

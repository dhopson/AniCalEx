"""Coordinates MyAnimeList sync and login between worker threads and the UI."""

from __future__ import annotations

import secrets
import threading
from pathlib import Path

from gi.repository import GLib, GObject

from anical.auth.mal_oauth import CallbackReceiver, LoginCancelled, OAuthError, authorize_url, new_verifier
from anical.db.mal_import import apply_mal_list
from anical.db.repository import Clock, LibraryRepository, system_clock
from anical.db.settings import (
    LAST_REFRESH_ERROR,
    LAST_REFRESH_RUN,
    MAL_CLIENT_ID,
    MAL_MODE,
    MAL_USERNAME,
    SettingsRepository,
)
from anical.domain.refresh import refresh_due
from anical.net.http import HttpClient, NetworkError
from anical.sync.covers import download_covers
from anical.sync.mal_sync import ACCOUNT, USERNAME, MalConfig, MalSyncService
from anical.ui.tasks import run_in_thread

LOGIN_TIMEOUT_S = 5 * 60
_CHECK_INTERVAL_S = 60 * 60


def describe_error(error: Exception) -> str:
    if isinstance(error, NetworkError):
        return "Couldn’t reach MyAnimeList. Showing saved data."
    return str(error) or error.__class__.__name__


class SyncController(GObject.Object):
    """Emits `changed` when busy/error/account state changes and
    `library-changed` when the library contents were updated."""

    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "library-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "synced": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    def __init__(
        self,
        repository: LibraryRepository,
        settings: SettingsRepository,
        service: MalSyncService,
        http: HttpClient,
        cover_dir: Path,
        clock: Clock = system_clock,
    ):
        super().__init__()
        self._repo = repository
        self._settings = settings
        self._service = service
        self._http = http
        self._cover_dir = cover_dir
        self._clock = clock
        self.busy = False
        self._timer_id = 0

    # State

    @property
    def config(self) -> MalConfig:
        return MalConfig(
            client_id=self._settings.get(MAL_CLIENT_ID, ""),
            mode=self._settings.get(MAL_MODE),
            username=self._settings.get(MAL_USERNAME),
        )

    @property
    def error(self) -> str | None:
        return self._settings.get_state(LAST_REFRESH_ERROR)

    @property
    def last_run(self) -> int | None:
        return self._settings.last_refresh_run()

    def set_client_id(self, client_id: str) -> None:
        self._settings.set(MAL_CLIENT_ID, client_id.strip() or None)
        self.emit("changed")

    def use_public_list(self, username: str) -> None:
        username = username.strip()
        self._settings.set(MAL_MODE, USERNAME if username else None)
        self._settings.set(MAL_USERNAME, username or None)
        self.emit("changed")
        if username:
            self.sync()

    # Refresh

    def start(self) -> None:
        """Run a refresh now if one is due, then keep checking while the app runs."""
        self.sync_if_due()
        if not self._timer_id:
            self._timer_id = GLib.timeout_add_seconds(_CHECK_INTERVAL_S, self._on_timer)

    def stop(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0

    def _on_timer(self) -> bool:
        self.sync_if_due()
        return GLib.SOURCE_CONTINUE

    def sync_if_due(self) -> None:
        if self.config.ready and refresh_due(self.last_run, self._clock()):
            self.sync()

    def sync(self) -> None:
        config = self.config
        if self.busy or not config.ready:
            return
        self._set_busy(True)
        run_in_thread(lambda: self._service.fetch(config), self._on_fetched, self._on_failed)

    def _on_fetched(self, result) -> None:
        now = self._clock()
        try:
            summary = apply_mal_list(self._repo.connection, result, now)
        except Exception as e:  # noqa: BLE001 - shown to the user; the import rolled back
            self._on_failed(e)
            return
        self._settings.set_state(LAST_REFRESH_RUN, str(now))
        self._settings.set_state(LAST_REFRESH_ERROR, None)
        self.emit("library-changed")
        self.emit("synced", summary)
        self._fetch_covers()

    def _fetch_covers(self) -> None:
        items = self._repo.covers_to_fetch()
        if not items:
            self._set_busy(False)
            return
        run_in_thread(
            lambda: download_covers(self._http, items, self._cover_dir),
            self._on_covers,
            lambda _e: self._set_busy(False),  # covers are cosmetic; retried next sync
        )

    def _on_covers(self, saved) -> None:
        if saved:
            self._repo.set_cover_paths(saved)
            self.emit("library-changed")
        self._set_busy(False)

    def _on_failed(self, error: Exception) -> None:
        self._settings.set_state(LAST_REFRESH_ERROR, describe_error(error))
        self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.emit("changed")

    # Account

    def begin_login(self, on_finished) -> tuple[str, threading.Event]:
        """Start the OAuth flow. Returns the URL to open and an event that cancels the wait.

        `on_finished(error_message_or_None)` runs on the main thread.
        Raises OAuthError if the callback port can't be opened.
        """
        client_id = self.config.client_id
        if not client_id:
            raise OAuthError("Enter a MyAnimeList client ID first")
        verifier = new_verifier()
        state = secrets.token_urlsafe(24)
        receiver = CallbackReceiver(state)
        cancelled = threading.Event()

        def work() -> str:
            code = receiver.wait(LOGIN_TIMEOUT_S, cancelled)
            return self._service.complete_login(client_id, code, verifier)

        def done(username: str) -> None:
            self._settings.set(MAL_MODE, ACCOUNT)
            self._settings.set(MAL_USERNAME, username)
            self._settings.set_state(LAST_REFRESH_ERROR, None)
            self.emit("changed")
            on_finished(None)
            self.sync()

        def failed(error: Exception) -> None:
            on_finished(None if isinstance(error, LoginCancelled) else describe_error(error))

        run_in_thread(work, done, failed)
        return authorize_url(client_id, verifier, state), cancelled

    def logout(self, on_finished) -> None:
        def done(_):
            self._settings.set(MAL_MODE, None)
            self._settings.set(MAL_USERNAME, None)
            self.emit("changed")
            on_finished(None)

        run_in_thread(self._service.logout, done, lambda e: on_finished(describe_error(e)))


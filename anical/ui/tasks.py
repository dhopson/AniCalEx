"""Run blocking work off the UI thread and deliver the outcome back on it."""

from __future__ import annotations

import threading
import traceback
from collections.abc import Callable

from gi.repository import GLib

from anical.auth.mal_oauth import OAuthError
from anical.net.http import NetworkError
from anical.providers.anilist import AniListError
from anical.providers.mal import MalError
from anical.sync.mal_sync import MalSyncError

# Failures with a user-facing message; anything else also gets a traceback on stderr.
_EXPECTED = (OAuthError, NetworkError, MalError, MalSyncError, AniListError)


def run_in_thread[T](
    work: Callable[[], T], on_done: Callable[[T], None], on_error: Callable[[Exception], None]
) -> None:
    def deliver(callback, value):
        callback(value)
        return GLib.SOURCE_REMOVE

    def target():
        try:
            result = work()
        except Exception as e:  # noqa: BLE001 - reported to the UI
            if not isinstance(e, _EXPECTED):
                traceback.print_exc()
            GLib.idle_add(deliver, on_error, e)
        else:
            GLib.idle_add(deliver, on_done, result)

    threading.Thread(target=target, daemon=True).start()

"""What the setup wizard, Preferences and the login popup share about MyAnimeList client IDs."""

from __future__ import annotations

import re

from gi.repository import Gtk

MAL_API_CONFIG_URL = "https://myanimelist.net/apiconfig"
UNUSUAL_CLIENT_ID = "Client IDs are usually 32 letters and digits. Check you copied the Client ID."
_CLIENT_ID = re.compile(r"[0-9a-f]{32}")  # what MAL hands out; only used for a gentle hint


def looks_like_client_id(text: str) -> bool:
    return _CLIENT_ID.fullmatch(text.strip().lower()) is not None


def open_registration_page(widget: Gtk.Widget) -> None:
    Gtk.UriLauncher.new(MAL_API_CONFIG_URL).launch(widget.get_root(), None, None)

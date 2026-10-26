"""OAuth token storage in the desktop keyring (libsecret / Secret Service)."""

from __future__ import annotations

import gi

gi.require_version("Secret", "1")
from gi.repository import GLib, Secret  # noqa: E402

from anical import APP_ID  # noqa: E402
from anical.auth.mal_oauth import Token, TokenStore  # noqa: E402,F401


class TokenStoreError(Exception):
    pass


_SCHEMA = Secret.Schema.new(
    f"{APP_ID}.OAuthToken", Secret.SchemaFlags.NONE, {"service": Secret.SchemaAttributeType.STRING}
)
_ATTRIBUTES = {"service": "myanimelist"}


class KeyringTokenStore:
    """Blocking D-Bus calls: use from a worker thread."""

    def load(self) -> Token | None:
        try:
            secret = Secret.password_lookup_sync(_SCHEMA, _ATTRIBUTES, None)
        except GLib.Error as e:
            raise TokenStoreError(f"Couldn’t read the keyring: {e.message}") from e
        if not secret:
            return None
        try:
            return Token.from_json(secret)
        except (ValueError, KeyError, TypeError):
            return None  # unreadable entry: treat as logged out

    def save(self, token: Token) -> None:
        try:
            Secret.password_store_sync(
                _SCHEMA, _ATTRIBUTES, Secret.COLLECTION_DEFAULT, "AniCal MyAnimeList login", token.to_json(), None
            )
        except GLib.Error as e:
            raise TokenStoreError(f"Couldn’t save the login to the keyring: {e.message}") from e

    def clear(self) -> None:
        try:
            Secret.password_clear_sync(_SCHEMA, _ATTRIBUTES, None)
        except GLib.Error as e:
            raise TokenStoreError(f"Couldn’t remove the login from the keyring: {e.message}") from e

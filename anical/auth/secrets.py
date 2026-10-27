"""OAuth token storage in the desktop keyring (libsecret / Secret Service), on Linux.

Windows and macOS use the stores in `anical.platform.secrets`.
"""

from __future__ import annotations

import gi

gi.require_version("Secret", "1")
from gi.repository import GLib, Secret

from anical import APP_ID
from anical.auth.mal_oauth import TOKEN_LABEL, Token, TokenStoreError


def _schema(name: str) -> Secret.Schema:
    return Secret.Schema.new(name, Secret.SchemaFlags.NONE, {"service": Secret.SchemaAttributeType.STRING})


_SCHEMA = _schema(f"{APP_ID}.OAuthToken")
# Logins saved before the app ID was renamed (io.github.anical → io.github.dhopson).
_LEGACY_SCHEMA = _schema("io.github.anical.AniCal.OAuthToken")
_ATTRIBUTES = {"service": "myanimelist"}


class KeyringTokenStore:
    """Blocking D-Bus calls: use from a worker thread."""

    def load(self) -> Token | None:
        try:
            secret = Secret.password_lookup_sync(_SCHEMA, _ATTRIBUTES, None)
            if not secret:
                secret = self._migrate_legacy()
        except GLib.Error as e:
            raise TokenStoreError(f"Couldn’t read the keyring: {e.message}") from e
        return Token.from_saved(secret)

    @staticmethod
    def _migrate_legacy() -> str | None:
        secret = Secret.password_lookup_sync(_LEGACY_SCHEMA, _ATTRIBUTES, None)
        if secret:
            Secret.password_store_sync(_SCHEMA, _ATTRIBUTES, Secret.COLLECTION_DEFAULT, TOKEN_LABEL, secret, None)
            Secret.password_clear_sync(_LEGACY_SCHEMA, _ATTRIBUTES, None)
        return secret

    def save(self, token: Token) -> None:
        try:
            Secret.password_store_sync(
                _SCHEMA, _ATTRIBUTES, Secret.COLLECTION_DEFAULT, TOKEN_LABEL, token.to_json(), None
            )
        except GLib.Error as e:
            raise TokenStoreError(f"Couldn’t save the login to the keyring: {e.message}") from e

    def clear(self) -> None:
        try:
            Secret.password_clear_sync(_SCHEMA, _ATTRIBUTES, None)
        except GLib.Error as e:
            raise TokenStoreError(f"Couldn’t remove the login from the keyring: {e.message}") from e

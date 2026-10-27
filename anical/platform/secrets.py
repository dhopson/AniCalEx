"""Where the MyAnimeList login is kept on each operating system (D25).

- Linux: the desktop keyring through libsecret (`anical.auth.secrets`).
- Windows: a file encrypted with DPAPI for the current Windows user.
  Credential Manager can't be used: it holds at most 1,280 characters and
  the login is longer.
- macOS: the login Keychain, through keyring.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from anical import APP_ID
from anical.auth.mal_oauth import TOKEN_LABEL, Token, TokenStore, TokenStoreError
from anical.platform import System, current

_ACCOUNT = "myanimelist"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


def token_store(data_dir: Path, system: System | None = None) -> TokenStore:
    system = system or current()
    if system is System.WINDOWS:
        return DpapiTokenStore(data_dir / "mal-login.bin")
    if system is System.MACOS:
        return KeychainTokenStore()
    from anical.auth.secrets import KeyringTokenStore

    return KeyringTokenStore()


def _dpapi(name: str) -> Callable[[bytes], bytes]:
    """CryptProtectData or CryptUnprotectData through ctypes (both take the same arguments)."""

    def call(data: bytes) -> bytes:
        import ctypes
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        buffer = ctypes.create_string_buffer(data, len(data))
        source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
        result = Blob()
        description = TOKEN_LABEL if name == "CryptProtectData" else None
        function = getattr(ctypes.windll.crypt32, name)
        if not function(
            ctypes.byref(source), description, None, None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(result)
        ):
            raise ctypes.WinError()
        try:
            return ctypes.string_at(result.pbData, result.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(result.pbData)

    return call


class DpapiTokenStore:
    """Windows: the token JSON, encrypted for the current user, in one file."""

    def __init__(
        self,
        path: Path,
        protect: Callable[[bytes], bytes] | None = None,
        unprotect: Callable[[bytes], bytes] | None = None,
    ):
        self._path = path
        self._protect = protect or _dpapi("CryptProtectData")
        self._unprotect = unprotect or _dpapi("CryptUnprotectData")

    def load(self) -> Token | None:
        try:
            sealed = self._path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as e:
            raise TokenStoreError(f"Couldn’t read the saved login: {e}") from e
        try:
            text = self._unprotect(sealed).decode("utf-8")
        except (OSError, ValueError):
            return None  # another user's, or damaged: treat as logged out
        return Token.from_saved(text)

    def save(self, token: Token) -> None:
        try:
            sealed = self._protect(token.to_json().encode("utf-8"))
            self._path.parent.mkdir(parents=True, exist_ok=True)
            partial = self._path.with_name(self._path.name + ".part")
            partial.write_bytes(sealed)
            os.replace(partial, self._path)
        except OSError as e:
            raise TokenStoreError(f"Couldn’t save the login: {e}") from e

    def clear(self) -> None:
        try:
            self._path.unlink(missing_ok=True)
        except OSError as e:
            raise TokenStoreError(f"Couldn’t remove the saved login: {e}") from e


class KeychainTokenStore:
    """macOS: the login Keychain. keyring is imported on first use, so it is
    only needed where this store is chosen."""

    def __init__(self, backend=None):
        self._backend = backend

    def _keyring(self):
        if self._backend is None:
            import keyring

            self._backend = keyring
        return self._backend

    def load(self) -> Token | None:
        try:
            secret = self._keyring().get_password(APP_ID, _ACCOUNT)
        except Exception as e:  # keyring raises its own KeyringError family
            raise TokenStoreError(f"Couldn’t read the Keychain: {e}") from e
        return Token.from_saved(secret)

    def save(self, token: Token) -> None:
        try:
            self._keyring().set_password(APP_ID, _ACCOUNT, token.to_json())
        except Exception as e:
            raise TokenStoreError(f"Couldn’t save the login to the Keychain: {e}") from e

    def clear(self) -> None:
        keyring = self._keyring()
        try:
            keyring.delete_password(APP_ID, _ACCOUNT)
        except Exception as e:
            if type(e).__name__ != "PasswordDeleteError":  # nothing was saved
                raise TokenStoreError(f"Couldn’t remove the login from the Keychain: {e}") from e

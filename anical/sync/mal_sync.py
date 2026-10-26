"""MyAnimeList synchronization: network side only.

Everything here blocks and runs on a worker thread. It returns plain data;
applying it to the database happens on the main thread (db/mal_import.py).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from anical.auth.mal_oauth import OAuthError, Token, TokenStore, exchange_code, refresh
from anical.db.repository import Clock, system_clock
from anical.net.http import HttpClient
from anical.providers.mal import MalAuthError, MalClient, MalListResult

ACCOUNT = "account"
USERNAME = "username"


class MalSyncError(Exception):
    """A problem the user has to fix (not configured, logged out)."""


@dataclass(frozen=True)
class MalConfig:
    client_id: str
    mode: str | None  # ACCOUNT, USERNAME, or None when not set up
    username: str | None = None

    @property
    def ready(self) -> bool:
        return bool(self.client_id) and (self.mode == ACCOUNT or (self.mode == USERNAME and bool(self.username)))


ClientFactory = Callable[..., MalClient]


class MalSyncService:
    def __init__(
        self,
        http: HttpClient,
        token_store: TokenStore,
        clock: Clock = system_clock,
        client_factory: ClientFactory = MalClient,
    ):
        self._http = http
        self._tokens = token_store
        self._clock = clock
        self._client = client_factory

    def fetch(self, config: MalConfig) -> MalListResult:
        if not config.ready:
            raise MalSyncError("Set up MyAnimeList in Preferences first")
        if config.mode == USERNAME:
            return self._client(self._http, config.client_id).fetch_list(config.username)

        token = self._tokens.load()
        if token is None:
            raise MalSyncError("Log in to MyAnimeList in Preferences")
        if token.expired(self._clock()):
            token = self._refresh(config, token)
        try:
            return self._client(self._http, config.client_id, token.access_token).fetch_list("@me")
        except MalAuthError:
            # The stated expiry can be wrong; refresh once and retry.
            token = self._refresh(config, token)
            return self._client(self._http, config.client_id, token.access_token).fetch_list("@me")

    def complete_login(self, client_id: str, code: str, verifier: str) -> str:
        """Exchange the authorization code, store the token, return the MAL user name."""
        token = exchange_code(self._http, client_id, code, verifier, self._clock())
        username = self._client(self._http, client_id, token.access_token).fetch_username()
        self._tokens.save(token)
        return username

    def logout(self) -> None:
        self._tokens.clear()

    def _refresh(self, config: MalConfig, token: Token) -> Token:
        try:
            token = refresh(self._http, config.client_id, token, self._clock())
        except OAuthError as e:
            raise MalAuthError("Your MyAnimeList login has expired. Log in again in Preferences.") from e
        self._tokens.save(token)
        return token

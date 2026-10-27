"""MyAnimeList OAuth 2.0: authorization code grant with PKCE.

MAL supports only the `plain` PKCE method, so the challenge is the verifier.
Reference: https://myanimelist.net/apiconfig/references/authorization
"""

from __future__ import annotations

import contextlib
import http.server
import json
import secrets
import socket
import threading
import urllib.parse
from dataclasses import asdict, dataclass
from typing import Protocol

from anical.net.http import HttpClient, Response

AUTHORIZE_URL = "https://myanimelist.net/v1/oauth2/authorize"
TOKEN_URL = "https://myanimelist.net/v1/oauth2/token"  # noqa: S105 - a URL, not a password
# Must match the redirect URL registered for the client ID exactly.
REDIRECT_PORT = 8765
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/callback"
# What the saved login is called in the keyring, Keychain or DPAPI file.
TOKEN_LABEL = "AniCal MyAnimeList login"  # noqa: S105 - a label, not a password
# Refresh a little before the stated expiry to avoid racing it.
EXPIRY_MARGIN_S = 300


class OAuthError(Exception):
    pass


class LoginCancelled(OAuthError):
    pass


@dataclass(frozen=True)
class Token:
    """A MyAnimeList login: the access and refresh tokens, and when the access token expires."""

    access_token: str
    refresh_token: str
    expires_at: int

    def expired(self, now: int) -> bool:
        return now >= self.expires_at - EXPIRY_MARGIN_S

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> Token:
        data = json.loads(text)
        return cls(str(data["access_token"]), str(data["refresh_token"]), int(data["expires_at"]))

    @classmethod
    def from_saved(cls, text: str | None) -> Token | None:
        """A login read back from a token store; None when nothing usable was saved
        (empty, or damaged), which the app treats as logged out."""
        if not text:
            return None
        try:
            return cls.from_json(text)
        except (ValueError, KeyError, TypeError):
            return None


class TokenStoreError(Exception):
    """The secure store couldn't be read or written."""


class TokenStore(Protocol):
    """Where the login is kept between runs: the keyring, a DPAPI file or the Keychain (anical.platform.secrets)."""

    def load(self) -> Token | None: ...
    def save(self, token: Token) -> None: ...
    def clear(self) -> None: ...


def new_verifier() -> str:
    # 86 characters from the unreserved set; MAL accepts 43-128.
    return secrets.token_urlsafe(64)


def authorize_url(client_id: str, verifier: str, state: str, redirect_uri: str = REDIRECT_URI) -> str:
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "state": state,
            "redirect_uri": redirect_uri,
            "code_challenge": verifier,
            "code_challenge_method": "plain",
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def _token_from(response: Response, now: int) -> Token:
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status != 200:
        detail = body.get("hint") or body.get("message") or body.get("error") or f"HTTP {response.status}"
        raise OAuthError(f"MyAnimeList login failed: {detail}")
    try:
        return Token(
            access_token=str(body["access_token"]),
            refresh_token=str(body["refresh_token"]),
            expires_at=now + int(body["expires_in"]),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise OAuthError("MyAnimeList returned an incomplete token") from e


def exchange_code(http: HttpClient, client_id: str, code: str, verifier: str, now: int) -> Token:
    response = http.request(
        "POST",
        TOKEN_URL,
        form={
            "client_id": client_id,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
        },
    )
    return _token_from(response, now)


def refresh(http: HttpClient, client_id: str, token: Token, now: int) -> Token:
    response = http.request(
        "POST",
        TOKEN_URL,
        form={"client_id": client_id, "grant_type": "refresh_token", "refresh_token": token.refresh_token},
    )
    return _token_from(response, now)


_DONE_PAGE = (
    "<!doctype html><meta charset=utf-8><title>AniCal</title>"
    "<body style='font-family:sans-serif;text-align:center;margin-top:20vh'>"
    "<h1>{heading}</h1><p>You can close this tab and return to AniCal.</p>"
)


class CallbackReceiver:
    """Receives the OAuth redirect on the loopback interface only.

    Both 127.0.0.1 and ::1 are bound, because browsers may resolve
    `localhost` to either.
    """

    def __init__(self, state: str, port: int = REDIRECT_PORT):
        self._state = state
        self._done = threading.Event()
        self._code: str | None = None
        self._error: str | None = None
        self._servers: list[http.server.HTTPServer] = []

        receiver = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != "/callback":
                    self.send_error(404)
                    return
                params = urllib.parse.parse_qs(parsed.query)
                if params.get("state", [None])[0] != receiver._state:
                    self.send_error(400, "State mismatch")  # stray or forged request: keep waiting
                    return
                if code := params.get("code", [None])[0]:
                    receiver._code = code
                    heading = "Logged in to MyAnimeList"
                else:
                    receiver._error = params.get("error", ["unknown error"])[0]
                    heading = "Login was not completed"
                body = _DONE_PAGE.format(heading=heading).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                receiver._done.set()

            def log_message(self, *args):  # never log request lines: they contain the code
                pass

        class IPv6Server(http.server.HTTPServer):
            address_family = socket.AF_INET6

        try:
            self._servers.append(http.server.HTTPServer(("127.0.0.1", port), Handler))
        except OSError as e:
            raise OAuthError(f"Can’t listen on port {port} for the login reply: {e.strerror}") from e
        with contextlib.suppress(OSError):  # no IPv6 loopback; 127.0.0.1 is enough
            self._servers.append(IPv6Server(("::1", port), Handler))
        for server in self._servers:
            threading.Thread(target=server.serve_forever, daemon=True).start()

    def wait(self, timeout: float, cancelled: threading.Event) -> str:
        """Block until the redirect arrives. Returns the authorization code."""
        try:
            remaining = timeout
            while not self._done.wait(min(remaining, 0.25)):
                remaining -= 0.25
                if cancelled.is_set():
                    raise LoginCancelled("Login cancelled")
                if remaining <= 0:
                    raise OAuthError("Timed out waiting for the browser")
            if self._error:
                raise OAuthError(f"MyAnimeList login was not completed ({self._error})")
            return self._code
        finally:
            self.close()

    def close(self) -> None:
        for server in self._servers:
            server.shutdown()
            server.server_close()
        self._servers.clear()

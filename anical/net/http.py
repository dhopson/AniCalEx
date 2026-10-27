"""Minimal HTTP layer on the standard library.

Providers depend on the `HttpClient` protocol, so tests substitute a fake and
never touch the network.
"""

from __future__ import annotations

import functools
import json
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from anical import __version__

USER_AGENT = f"AniCal/{__version__}"
# Some URLs come from API responses (cover images), so urllib's file: and ftp:
# handlers must never be reachable.
ALLOWED_SCHEMES = ("https", "http")
# Far above any real reply (MAL list pages, AniList pages, covers); stops a
# broken or hostile server from filling memory.
MAX_RESPONSE_BYTES = 32 * 1024 * 1024


class NetworkError(Exception):
    """The request never produced an HTTP response (offline, DNS, timeout, TLS)."""


@dataclass(frozen=True)
class Response:
    """An HTTP reply: status, body and headers."""

    status: int
    body: bytes = b""
    headers: Mapping[str, str] = field(default_factory=dict)

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))

    def header(self, name: str) -> str | None:
        name = name.lower()
        return next((v for k, v in self.headers.items() if k.lower() == name), None)


class HttpClient(Protocol):
    """What the providers need from HTTP: `UrllibClient` in the app, a fake in tests."""

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        form: Mapping[str, str] | None = None,
        json_body: Any = None,
    ) -> Response: ...


@functools.cache
def tls_context() -> ssl.SSLContext:
    """The system's CA certificates, except in a frozen build (Windows and
    macOS bundles), where the system's OpenSSL looks for them in the build
    machine's paths. Those use the bundled certifi file instead (D22)."""
    if getattr(sys, "frozen", False):
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    return ssl.create_default_context()


def allowed_url(url: str) -> bool:
    """Whether `url` is a web address the client will fetch."""
    return urllib.parse.urlsplit(url).scheme.lower() in ALLOWED_SCHEMES


def _read_limited(reply) -> bytes:
    body = reply.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise NetworkError(f"The reply was larger than {MAX_RESPONSE_BYTES // (1024 * 1024)} MB")
    return body


class UrllibClient:
    """`HttpClient` on urllib, with the TLS context from `tls_context`."""

    def __init__(self, timeout: float = 20):
        self._timeout = timeout

    def request(self, method, url, *, params=None, headers=None, form=None, json_body=None) -> Response:
        if not allowed_url(url):
            raise NetworkError(f"Not a web address: {url[:80]}")
        if params:
            url = f"{url}{'&' if '?' in url else '?'}{urllib.parse.urlencode(params)}"
        data = None
        request_headers = {"User-Agent": USER_AGENT}
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            request_headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif json_body is not None:
            data = json.dumps(json_body).encode()
            request_headers["Content-Type"] = "application/json"
            request_headers["Accept"] = "application/json"
        request = urllib.request.Request(url, data=data, method=method, headers=request_headers)
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        try:
            with urllib.request.urlopen(request, timeout=self._timeout, context=tls_context()) as reply:
                return Response(reply.status, _read_limited(reply), dict(reply.headers.items()))
        except urllib.error.HTTPError as e:
            return Response(e.code, _read_limited(e), dict(e.headers.items()))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise NetworkError(str(getattr(e, "reason", e))) from e

"""Minimal HTTP layer on the standard library.

Providers depend on the `HttpClient` protocol, so tests substitute a fake and
never touch the network.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from anical import __version__

USER_AGENT = f"AniCal/{__version__}"


class NetworkError(Exception):
    """The request never produced an HTTP response (offline, DNS, timeout, TLS)."""


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes = b""
    headers: Mapping[str, str] = field(default_factory=dict)

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))

    def header(self, name: str) -> str | None:
        name = name.lower()
        return next((v for k, v in self.headers.items() if k.lower() == name), None)


class HttpClient(Protocol):
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


class UrllibClient:
    def __init__(self, timeout: float = 20):
        self._timeout = timeout

    def request(self, method, url, *, params=None, headers=None, form=None, json_body=None) -> Response:
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
            with urllib.request.urlopen(request, timeout=self._timeout) as reply:
                return Response(reply.status, reply.read(), dict(reply.headers.items()))
        except urllib.error.HTTPError as e:
            return Response(e.code, e.read(), dict(e.headers.items()))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise NetworkError(str(getattr(e, "reason", e))) from e

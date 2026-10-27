"""Test doubles and fixture loading shared by the test modules."""

import json
from pathlib import Path

from anical.net.http import Response

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_json(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def json_response(body, status: int = 200, headers=None) -> Response:
    return Response(status, json.dumps(body).encode(), headers or {"Content-Type": "application/json"})


class FakeHttp:
    """Replays queued responses (or raises queued exceptions) and records requests."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def request(self, method, url, *, params=None, headers=None, form=None, json_body=None):
        self.requests.append(
            {"method": method, "url": url, "params": params, "headers": headers or {}, "form": form, "json": json_body}
        )
        if not self.replies:
            raise AssertionError(f"unexpected request: {method} {url}")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class MemoryTokenStore:
    def __init__(self, token=None):
        self.token = token
        self.saved = []

    def load(self):
        return self.token

    def save(self, token):
        self.token = token
        self.saved.append(token)

    def clear(self):
        self.token = None

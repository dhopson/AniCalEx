import socket
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest
from helpers import FakeHttp, MemoryTokenStore, json_response

from anical.auth.mal_oauth import (
    REDIRECT_URI,
    CallbackReceiver,
    LoginCancelled,
    OAuthError,
    Token,
    authorize_url,
    exchange_code,
    new_verifier,
)
from anical.net.http import Response
from anical.providers.mal import MalAuthError
from anical.sync.mal_sync import ACCOUNT, USERNAME, MalConfig, MalSyncError, MalSyncService

NOW = 1_790_000_000
EMPTY_LIST = {"data": [], "paging": {}}


def token_reply(access="new-access", refresh="new-refresh", expires_in=3600):
    return json_response(
        {"token_type": "Bearer", "access_token": access, "refresh_token": refresh, "expires_in": expires_in}
    )


def test_authorize_url_uses_plain_pkce():
    verifier = new_verifier()
    assert 43 <= len(verifier) <= 128
    query = urllib.parse.parse_qs(urllib.parse.urlparse(authorize_url("cid", verifier, "st")).query)
    assert query["code_challenge"] == [verifier]
    assert query["code_challenge_method"] == ["plain"]
    assert query["redirect_uri"] == [REDIRECT_URI]
    assert query["client_id"] == ["cid"] and query["state"] == ["st"]


def test_exchange_code_sends_verifier_without_secret():
    http = FakeHttp(token_reply(expires_in=2678400))
    token = exchange_code(http, "cid", "the-code", "the-verifier", NOW)
    assert token == Token("new-access", "new-refresh", NOW + 2678400)
    form = http.requests[0]["form"]
    assert form["code_verifier"] == "the-verifier" and form["grant_type"] == "authorization_code"
    assert "client_secret" not in form


def test_token_errors_are_reported():
    with pytest.raises(OAuthError, match="invalid_grant"):
        exchange_code(FakeHttp(json_response({"error": "invalid_grant"}, 400)), "c", "x", "v", NOW)
    with pytest.raises(OAuthError, match="incomplete"):
        exchange_code(FakeHttp(json_response({"access_token": "a"})), "c", "x", "v", NOW)


def test_token_round_trip_and_expiry():
    token = Token("a", "r", NOW + 1000)
    assert Token.from_json(token.to_json()) == token
    assert not token.expired(NOW)
    assert token.expired(NOW + 800)  # inside the safety margin


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def test_callback_receiver_validates_state_and_returns_code():
    port = free_port()
    receiver = CallbackReceiver("expected-state", port)
    result = {}
    waiter = threading.Thread(target=lambda: result.update(code=receiver.wait(10, threading.Event())))
    waiter.start()
    assert get(f"http://127.0.0.1:{port}/callback?code=evil&state=wrong") == 400
    assert get(f"http://127.0.0.1:{port}/other") == 404
    assert get(f"http://127.0.0.1:{port}/callback?code=good&state=expected-state") == 200
    waiter.join(5)
    assert result["code"] == "good"


def test_callback_receiver_reports_denial_and_cancellation():
    port = free_port()
    receiver = CallbackReceiver("s", port)
    errors = []

    def wait():
        try:
            receiver.wait(10, threading.Event())
        except OAuthError as e:
            errors.append(e)

    waiter = threading.Thread(target=wait)
    waiter.start()
    get(f"http://127.0.0.1:{port}/callback?error=access_denied&state=s")
    waiter.join(5)
    assert "access_denied" in str(errors[0])

    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(LoginCancelled):
        CallbackReceiver("s", free_port()).wait(10, cancelled)


def service(http, store):
    return MalSyncService(http, store, clock=lambda: NOW)


def test_public_username_mode_needs_no_token():
    http = FakeHttp(json_response(EMPTY_LIST))
    service(http, MemoryTokenStore()).fetch(MalConfig("cid", USERNAME, "someone"))
    assert http.requests[0]["headers"] == {"X-MAL-CLIENT-ID": "cid"}
    assert "/users/someone/" in http.requests[0]["url"]


def test_unconfigured_or_logged_out_accounts_are_explained():
    with pytest.raises(MalSyncError, match="Preferences"):
        service(FakeHttp(), MemoryTokenStore()).fetch(MalConfig("", None))
    with pytest.raises(MalSyncError, match="Log in"):
        service(FakeHttp(), MemoryTokenStore()).fetch(MalConfig("cid", ACCOUNT))


def test_expired_token_is_refreshed_before_fetching():
    store = MemoryTokenStore(Token("old", "refresh-me", NOW - 10))
    http = FakeHttp(token_reply(), json_response(EMPTY_LIST))
    service(http, store).fetch(MalConfig("cid", ACCOUNT))
    assert http.requests[0]["form"]["refresh_token"] == "refresh-me"
    assert http.requests[1]["headers"] == {"Authorization": "Bearer new-access"}
    assert store.token.access_token == "new-access"


def test_rejected_token_is_refreshed_once_and_retried():
    store = MemoryTokenStore(Token("stale", "r", NOW + 99999))
    http = FakeHttp(Response(401, b"{}"), token_reply(), json_response(EMPTY_LIST))
    service(http, store).fetch(MalConfig("cid", ACCOUNT))
    assert len(http.requests) == 3


def test_failed_refresh_asks_user_to_log_in_again():
    store = MemoryTokenStore(Token("old", "r", NOW - 10))
    with pytest.raises(MalAuthError, match="Log in again"):
        service(FakeHttp(json_response({"error": "invalid_grant"}, 400)), store).fetch(MalConfig("cid", ACCOUNT))


def test_complete_login_stores_token_and_returns_username():
    store = MemoryTokenStore()
    http = FakeHttp(token_reply(), json_response({"id": 1, "name": "example_user"}))
    assert service(http, store).complete_login("cid", "code", "verifier") == "example_user"
    assert store.token.access_token == "new-access"

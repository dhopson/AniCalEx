import socket
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest
from helpers import FakeHttp, MemoryTokenStore, json_response

from anical.auth.mal_oauth import (
    REDIRECT_URI,
    RENEW_AFTER_S,
    CallbackReceiver,
    LoginCancelled,
    OAuthError,
    Token,
    authorize_url,
    exchange_code,
    new_verifier,
)
from anical.net.http import NetworkError, Response
from anical.providers.mal import MalAuthError
from anical.sync.mal_sync import ACCOUNT, USERNAME, LoginExpired, MalConfig, MalSyncError, MalSyncService

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
    assert token == Token("new-access", "new-refresh", NOW + 2678400, NOW)
    form = http.requests[0]["form"]
    assert form["code_verifier"] == "the-verifier" and form["grant_type"] == "authorization_code"
    assert "client_secret" not in form


def test_token_errors_are_reported():
    with pytest.raises(OAuthError, match="invalid_grant"):
        exchange_code(FakeHttp(json_response({"error": "invalid_grant"}, 400)), "c", "x", "v", NOW)
    with pytest.raises(OAuthError, match="incomplete"):
        exchange_code(FakeHttp(json_response({"access_token": "a"})), "c", "x", "v", NOW)


def test_token_round_trip_and_expiry():
    token = Token("a", "r", NOW + 1000, NOW)
    assert Token.from_json(token.to_json()) == token
    assert not token.expired(NOW)
    assert token.expired(NOW + 800)  # inside the safety margin


def test_login_saved_before_issue_times_were_recorded_still_loads():
    saved = '{"access_token": "a", "refresh_token": "r", "expires_at": 5}'
    assert Token.from_json(saved) == Token("a", "r", 5, 0)
    assert Token.from_json(saved).renewal_due(NOW)  # so it renews on the next refresh


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
    with pytest.raises(LoginExpired):
        service(FakeHttp(), MemoryTokenStore()).fetch(MalConfig("cid", ACCOUNT))


def test_expired_token_is_refreshed_before_fetching():
    store = MemoryTokenStore(Token("old", "refresh-me", NOW - 10))
    http = FakeHttp(token_reply(), json_response(EMPTY_LIST))
    service(http, store).fetch(MalConfig("cid", ACCOUNT))
    assert http.requests[0]["form"]["refresh_token"] == "refresh-me"
    assert http.requests[1]["headers"] == {"Authorization": "Bearer new-access"}
    assert store.token.access_token == "new-access"


def test_rejected_token_is_refreshed_once_and_retried():
    store = MemoryTokenStore(Token("stale", "r", NOW + 99999, NOW))
    http = FakeHttp(Response(401, b"{}"), token_reply(), json_response(EMPTY_LIST))
    service(http, store).fetch(MalConfig("cid", ACCOUNT))
    assert len(http.requests) == 3


@pytest.mark.parametrize("status", [400, 401])
def test_refused_refresh_means_the_login_expired(status):
    store = MemoryTokenStore(Token("old", "r", NOW - 10))
    with pytest.raises(LoginExpired):
        service(FakeHttp(json_response({"error": "invalid_grant"}, status)), store).fetch(MalConfig("cid", ACCOUNT))


@pytest.mark.parametrize(
    "reply",
    [json_response({"message": "busy"}, 500), json_response({"message": "slow down"}, 429), json_response({}, 200)],
    ids=["server-error", "rate-limited", "garbled-token"],
)
def test_other_refresh_failures_dont_claim_the_login_expired(reply):
    store = MemoryTokenStore(Token("old", "r", NOW - 10))
    with pytest.raises(MalSyncError):
        service(FakeHttp(reply), store).fetch(MalConfig("cid", ACCOUNT))


def test_a_rejected_new_token_means_the_login_expired():
    store = MemoryTokenStore(Token("stale", "r", NOW + 99999, NOW))
    http = FakeHttp(Response(401, b"{}"), token_reply(), Response(401, b"{}"))
    with pytest.raises(LoginExpired):
        service(http, store).fetch(MalConfig("cid", ACCOUNT))


def test_a_public_list_rejection_isnt_about_a_login():
    with pytest.raises(MalAuthError) as caught:
        service(FakeHttp(Response(401, b"{}")), MemoryTokenStore()).fetch(MalConfig("bad", USERNAME, "someone"))
    assert not isinstance(caught.value, LoginExpired)


def a_day_old_token():
    return Token("working", "refresh-me", NOW + 99999, NOW - RENEW_AFTER_S)


def test_a_day_old_login_is_renewed_while_it_still_works():
    store = MemoryTokenStore(a_day_old_token())
    http = FakeHttp(token_reply(), json_response(EMPTY_LIST))
    service(http, store).fetch(MalConfig("cid", ACCOUNT))
    assert http.requests[0]["form"]["refresh_token"] == "refresh-me"
    assert http.requests[1]["headers"] == {"Authorization": "Bearer new-access"}
    assert store.token == Token("new-access", "new-refresh", NOW + 3600, NOW)


def test_a_new_login_isnt_renewed():
    store = MemoryTokenStore(Token("working", "r", NOW + 99999, NOW - RENEW_AFTER_S + 60))
    http = FakeHttp(json_response(EMPTY_LIST))
    service(http, store).fetch(MalConfig("cid", ACCOUNT))
    assert len(http.requests) == 1 and store.token.access_token == "working"


@pytest.mark.parametrize(
    "reply",
    [
        json_response({"error": "invalid_grant"}, 400),
        json_response({"message": "busy"}, 500),
        NetworkError("offline"),
    ],
    ids=["refused", "server-error", "offline"],
)
def test_failing_to_renew_early_keeps_using_the_working_login(reply):
    store = MemoryTokenStore(a_day_old_token())
    http = FakeHttp(reply, json_response(EMPTY_LIST))
    service(http, store).fetch(MalConfig("cid", ACCOUNT))
    assert http.requests[1]["headers"] == {"Authorization": "Bearer working"}
    assert store.token == a_day_old_token()  # so it's tried again next time


def test_refused_early_renewal_asks_once_the_login_really_stops():
    store = MemoryTokenStore(a_day_old_token())
    refused = json_response({"error": "invalid_grant"}, 400)
    http = FakeHttp(refused, Response(401, b"{}"), refused)
    with pytest.raises(LoginExpired):
        service(http, store).fetch(MalConfig("cid", ACCOUNT))


def test_a_renewal_failure_is_described_without_the_tokens():
    store = MemoryTokenStore(a_day_old_token())
    reply = json_response({"error": "invalid_grant", "hint": "Token has expired"}, 400)
    mal = service(FakeHttp(reply, json_response(EMPTY_LIST)), store)
    mal.fetch(MalConfig("cid", ACCOUNT))
    problem = mal.take_renewal_problem()
    assert problem.startswith("400 invalid_grant: ") and "Token has expired" in problem
    assert "1.0 days old" in problem
    assert "working" not in problem and "refresh-me" not in problem
    assert mal.take_renewal_problem() is None


def test_a_good_renewal_has_nothing_to_report():
    mal = service(FakeHttp(token_reply(), json_response(EMPTY_LIST)), MemoryTokenStore(a_day_old_token()))
    mal.fetch(MalConfig("cid", ACCOUNT))
    assert mal.take_renewal_problem() is None


def test_complete_login_stores_token_and_returns_username():
    store = MemoryTokenStore()
    http = FakeHttp(token_reply(), json_response({"id": 1, "name": "example_user"}))
    assert service(http, store).complete_login("cid", "code", "verifier") == "example_user"
    assert store.token.access_token == "new-access"

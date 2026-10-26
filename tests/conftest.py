import pytest

from anical.db.database import connect, migrate
from anical.db.repository import LibraryRepository
from anical.providers.mal import MalClient
from helpers import FakeHttp, fixture_json, json_response


class FakeClock:
    def __init__(self, start: int = 1_790_000_000):
        self.now = start

    def __call__(self) -> int:
        return self.now

    def advance(self, seconds: int = 1) -> None:
        self.now += seconds


@pytest.fixture
def conn():
    conn = connect(":memory:")
    migrate(conn)
    yield conn
    conn.close()


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def repo(conn, clock):
    return LibraryRepository(conn, clock)


@pytest.fixture
def mal_list_result():
    http = FakeHttp(
        json_response(fixture_json("mal_animelist_page1.json")),
        json_response(fixture_json("mal_animelist_page2.json")),
    )
    return MalClient(http, "client", "token", sleep=lambda s: None).fetch_list()

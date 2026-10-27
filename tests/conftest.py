import pytest
from helpers import FakeHttp, fixture_json, json_response

from anical.db.database import connect, migrate
from anical.db.repository import LibraryRepository
from anical.providers.mal import MalClient


class FakeClock:
    def __init__(self, start: int = 1_790_000_000):
        self.now = start

    def __call__(self) -> int:
        return self.now

    def advance(self, seconds: int = 1) -> None:
        self.now += seconds


@pytest.fixture(autouse=True)
def private_ani_cli_history(tmp_path, monkeypatch):
    """Never read the real ani-cli history from tests."""
    directory = tmp_path / "ani-cli-history"
    directory.mkdir()
    monkeypatch.setenv("ANI_CLI_HIST_DIR", str(directory))
    return directory / "ani-hsts"


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

import pytest

from anical.db.schedule import ScheduleRepository
from anical.db.settings import NOTIFICATIONS_ENABLED, SettingsRepository
from anical.domain.calendar import CalendarEntry
from anical.domain.models import AiringStatus, AnimeDraft, WatchState
from anical.domain.notifications import MAX_AGE_S, to_announce
from anical.domain.schedule import AiringSource, Precision

NOW = 1_793_040_000
LAG = 7200
HOUR = 3600


def entry(airs_at, precision=Precision.EXACT, state=WatchState.UNWATCHED, number=1):
    return CalendarEntry(number, 1, "Show", number, airs_at, precision, AiringSource.ANILIST, state)


def test_announces_only_recently_available_real_unwatched_episodes():
    entries = [
        entry(NOW - LAG - HOUR, number=1),  # available an hour ago: yes
        entry(NOW - LAG + HOUR, number=2),  # aired, but not available yet
        entry(NOW - LAG - MAX_AGE_S - 1, number=3),  # too old (app was closed)
        entry(NOW - LAG - HOUR, Precision.ESTIMATED, number=4),  # guessed time
        entry(NOW - LAG - HOUR, state=WatchState.WATCHED, number=5),
        entry(NOW + HOUR, number=6),  # future
    ]
    assert [e.number for e in to_announce(entries, NOW, LAG)] == [1]


gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from anical.ui.display import DisplaySettings  # noqa: E402
from anical.ui.notifier import Notifier  # noqa: E402


@pytest.fixture
def setup(repo, conn, clock):
    clock.now = NOW
    schedule = ScheduleRepository(conn, clock)
    settings = SettingsRepository(conn, clock)
    sent = []
    notifier = Notifier(schedule, settings, DisplaySettings(settings), lambda key, n: sent.append(key), clock)
    return repo, schedule, settings, notifier, sent


def airing_show(repo, schedule, title, *hours_ago):
    anime_id = repo.create_anime(AnimeDraft(title=title, airing_status=AiringStatus.AIRING))
    schedule.set_user_airings(anime_id, [(i + 1, NOW - h * HOUR) for i, h in enumerate(hours_ago)])
    return anime_id


def test_each_episode_is_announced_once(setup):
    repo, schedule, _settings, notifier, sent = setup
    airing_show(repo, schedule, "A", 3)
    assert notifier.check() == 1
    assert notifier.check() == 0
    assert len(sent) == 1 and sent[0].startswith("available-")


def test_many_at_once_become_one_summary(setup):
    repo, schedule, _settings, notifier, sent = setup
    airing_show(repo, schedule, "A", 3, 4)
    airing_show(repo, schedule, "B", 3, 5)
    assert notifier.check() == 4
    assert sent == ["available-summary"]


def test_disabled_notifications_still_consume_the_backlog(setup):
    repo, schedule, settings, notifier, sent = setup
    settings.set(NOTIFICATIONS_ENABLED, False)
    airing_show(repo, schedule, "A", 3)
    assert notifier.check() == 1 and sent == []
    settings.set(NOTIFICATIONS_ENABLED, True)
    assert notifier.check() == 0  # nothing old is announced after re-enabling


def test_single_notification_plays_that_episode(setup):
    repo, schedule, _settings, notifier, _sent = setup
    sent = []
    notifier._send = lambda key, notice: sent.append(notice)
    anime_id = airing_show(repo, schedule, "A", 3)
    notifier.check()
    notice, = sent
    assert (notice.action, notice.target) == ("app.show-anime", anime_id)
    button, = notice.buttons
    assert (button.label, button.action) == ("Play", "app.play-episode")
    from anical.platform.notify import to_gio

    assert to_gio(notice) is not None

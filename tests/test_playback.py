import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from anical.db.playback import PlaybackRepository
from anical.domain.models import AnimeDraft
from anical.domain.playback import (
    Outcome,
    PlaybackConfig,
    PlaybackError,
    PlaybackMapping,
    Progress,
    build_launch,
    decide,
    error_message,
    history_for,
    parse_history,
    parse_progress,
    sanitize_query,
)
from anical.playback import install_helpers

FAKE_ANI_CLI = str(Path(__file__).parent / "fake_ani_cli.py")
RED = "\033[1;31m{}\033[0m"


@pytest.fixture
def helpers(tmp_path):
    return install_helpers(tmp_path / "helpers")


def launch_for(tmp_path, helpers, mapping=None, episode=5, config=PlaybackConfig()):
    session = tmp_path / "session"
    session.mkdir(exist_ok=True)
    mapping = mapping or PlaybackMapping(1, "Some Show")
    return build_launch(config, FAKE_ANI_CLI, mapping, episode, session, *helpers)


def run(launch, answer=None, **fake_env):
    """Run the fake ani-cli with the launch's argv/env; optionally answer a pick request."""
    env = {**os.environ, **launch.env, **{f"FAKE_{k.upper()}": v for k, v in fake_env.items()}}
    process = subprocess.Popen(launch.argv, env=env, stderr=subprocess.PIPE, text=True)
    if answer is not None:
        choices = Path(launch.env["ANICAL_PICK_DIR"]) / "choices.json"
        deadline = time.monotonic() + 10
        while not choices.exists():
            assert time.monotonic() < deadline, "picker never asked"
            time.sleep(0.05)
        (Path(launch.env["ANICAL_PICK_DIR"]) / "answer.json").write_text(json.dumps({"index": answer}))
    _, stderr = process.communicate(timeout=20)
    progress = launch.progress_path.read_text() if launch.progress_path.exists() else None
    return process.returncode, parse_progress(progress), stderr


# Pure rules


@pytest.mark.parametrize(
    ("title", "query"),
    [
        ("Re:Zero − Starting Life", "Re Zero Starting Life"),
        ("Fate/stay night [UBW] & co?", "Fate stay night UBW co"),
        ("葬送のフリーレン", "葬送のフリーレン"),
        ("!!!", "!!!"),
    ],
)
def test_sanitize_query(title, query):
    assert sanitize_query(title) == query


def test_launch_uses_an_argument_vector(tmp_path, helpers):
    title = "Show; rm -rf ~ $(id) `x` \"q\""
    mapping = PlaybackMapping(1, title, "Chosen", episode_offset=12, mode="dub")
    launch = launch_for(tmp_path, helpers, mapping, episode=3, config=PlaybackConfig(quality="720p"))
    assert launch.argv == [FAKE_ANI_CLI, "--no-detach", "--exit-after-play", "--dub", "-q", "720p", "-e", "15", title]
    assert launch.env["ANICAL_PICK_TITLE"] == "Chosen"
    assert f"anical-out={launch.progress_path}" in launch.env["ANI_CLI_PLAYER_FLAGS"]


def test_launch_rejects_unusable_input(tmp_path, helpers):
    with pytest.raises(PlaybackError, match="not a valid"):
        launch_for(tmp_path, helpers, PlaybackMapping(1, "x", episode_offset=-5), episode=3)
    spaced = tmp_path / "has space"
    spaced.mkdir()
    with pytest.raises(PlaybackError, match="spaces"):
        build_launch(PlaybackConfig(), "ani-cli", PlaybackMapping(1, "x"), 1, spaced, *helpers)


@pytest.mark.parametrize(
    ("exit_code", "progress", "stderr", "outcome"),
    [
        (0, Progress("eof", 94.9, 1400, 1420), "", Outcome.WATCHED),
        (0, Progress("quit", 91.0, 1300, 1420), "", Outcome.WATCHED),
        (0, Progress("quit", 42.0, 600, 1420), "", Outcome.PARTIAL),
        (0, Progress("quit", None, None, None), "", Outcome.PARTIAL),
        (2, Progress("error"), "", Outcome.FAILED),
        (1, None, RED.format("Invalid episode!"), Outcome.EPISODE_NOT_FOUND),
        (1, None, RED.format("Episode not released!"), Outcome.EPISODE_NOT_FOUND),
        (1, None, RED.format("No results found!"), Outcome.SHOW_NOT_FOUND),
        (1, None, RED.format("Invalid anime selection"), Outcome.CANCELLED),
        (0, None, "", Outcome.UNKNOWN),  # e.g. VLC: no progress reporter
        (1, None, "tput: unknown terminal\n" + RED.format("Blocked by cloudflare."), Outcome.FAILED),
    ],
)
def test_decide(exit_code, progress, stderr, outcome):
    assert decide(exit_code, progress, stderr).outcome is outcome


def test_launching_alone_never_counts_as_watched():
    assert decide(0, Progress("quit", 0.4, 5, 1400), "").outcome is Outcome.PARTIAL
    assert not decide(0, None, "").outcome is Outcome.WATCHED


def test_error_message_prefers_ani_cli_die_output():
    assert error_message("noise\n" + RED.format("Blocked by cloudflare.") + "\nmore noise") == "Blocked by cloudflare."
    assert error_message("\x1b[2Kplain failure\n") == "plain failure"
    assert error_message("") is None


@pytest.mark.parametrize("text", ["", "not json", "[]", '{"percent": 5}', '{"reason": 1}'])
def test_parse_progress_rejects_garbage(text):
    assert parse_progress(text) is None


def test_parse_progress_drops_bad_numbers():
    progress = parse_progress('{"reason":"quit","percent":-3,"position":"x","duration":1420.5}')
    assert progress == Progress("quit", None, None, 1420.5)


def test_history_parsing_and_matching():
    text = "5\tovergeared-10469\tOvergeared\nbroken line\n1\tfrieren-481\tFrieren: Beyond Journey's End\n"
    entries = parse_history(text)
    assert [e.anicli_id for e in entries] == ["overgeared-10469", "frieren-481"]
    assert history_for(entries, "  overgeared ").episode == "5"
    assert history_for(entries, None) is None
    assert history_for(entries, "Other") is None


# Picker and fake ani-cli end to end (real subprocesses, no network)


def test_single_result_needs_no_picker(tmp_path, helpers):
    code, progress, _ = run(launch_for(tmp_path, helpers), progress='{"reason":"eof","percent":95}')
    assert code == 0 and progress.reason == "eof"


def test_remembered_title_is_picked_automatically(tmp_path, helpers):
    mapping = PlaybackMapping(1, "Show", result_title="Show Season 2")
    code, progress, _ = run(
        launch_for(tmp_path, helpers, mapping), results="Show|Show Season 2|Show Movie",
        progress='{"reason":"quit","percent":10}',
    )
    assert code == 0 and progress.percent == 10
    assert not (tmp_path / "session" / "choices.json").exists()


def test_ambiguous_results_ask_the_app(tmp_path, helpers):
    launch = launch_for(tmp_path, helpers)
    code, progress, _ = run(launch, answer="2", results="A|B", progress='{"reason":"eof"}')
    assert code == 0 and progress.reason == "eof"
    asked = json.loads((tmp_path / "session" / "choices.json").read_text())
    assert [c["title"] for c in asked["choices"]] == ["A", "B"]


def test_cancelled_pick_stops_ani_cli(tmp_path, helpers):
    launch = launch_for(tmp_path, helpers)
    env = {**os.environ, **launch.env, "FAKE_RESULTS": "A|B"}
    process = subprocess.Popen(launch.argv, env=env, stderr=subprocess.PIPE, text=True)
    choices = Path(launch.env["ANICAL_PICK_DIR"]) / "choices.json"
    deadline = time.monotonic() + 10
    while not choices.exists():
        assert time.monotonic() < deadline, "picker never asked"
        time.sleep(0.05)
    (choices.parent / "answer.json").write_text('{"index": null}')
    _, stderr = process.communicate(timeout=20)
    assert decide(process.returncode, None, stderr).outcome is Outcome.CANCELLED


def test_unreleased_episode(tmp_path, helpers):
    code, progress, stderr = run(launch_for(tmp_path, helpers, episode=13), episodes="12")
    assert decide(code, progress, stderr).outcome is Outcome.EPISODE_NOT_FOUND


def test_picker_quits_ani_cli_menu_after_non_mpv_playback(helpers):
    picker, _ = helpers
    reply = subprocess.run([str(picker), "Playing episode 5 of Show... "], input="next\nreplay\nquit\n",
                           capture_output=True, text=True)
    assert reply.stdout.strip() == "quit"


# Repository


def test_mappings_and_sessions(repo, conn, clock):
    anime_id = repo.create_anime(AnimeDraft(title="Show", total_episodes=2))
    playback = PlaybackRepository(conn, clock)
    assert playback.mapping(anime_id) is None
    playback.save_mapping(PlaybackMapping(anime_id, " Show ", "Show (TV)", 12, "dub"))
    assert playback.mapping(anime_id) == PlaybackMapping(anime_id, "Show", "Show (TV)", 12, "dub")
    with pytest.raises(ValueError):
        playback.save_mapping(PlaybackMapping(anime_id, "   "))

    episode_id = repo.episodes(anime_id).episodes[0].id
    finished = playback.start_session(episode_id)
    playback.finish_session(finished, 0, "eof", 95.0, None)
    playback.start_session(episode_id)  # never finished: AniCal quit mid-playback
    assert playback.close_interrupted() == 1
    reasons = [r[0] for r in conn.execute("SELECT end_reason FROM playback_session ORDER BY id")]
    assert reasons == ["eof", "interrupted"]
    playback.delete_mapping(anime_id)
    assert playback.mapping(anime_id) is None

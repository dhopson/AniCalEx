"""Drive the real ani-cli the way AniCal does, against a fake site.

Started as spikes S2 and S3; CI runs it against each build's own helpers.

What is real: ani-cli, the shell it runs in (Git Bash on Windows), AniCal's
picker and player shim (frozen .exe files on Windows), mpv and the progress
script. What is fake: the streaming site. A fake `curl` answers ani-cli's
requests from generated fixtures, and the "stream" is a local test video
served from 127.0.0.1. Nothing is fetched from the internet.

Usage: python harness.py --out DIR --work "DIR WITH SPACES" [--helpers DIR]
       [--ani-cli PATH] [--bash PATH] [--mpv PATH] [--headless]
       [--real "SHOW TITLE" --episode N]

--real skips the fakes and plays one episode from ani-cli's real source,
asking in the terminal when the picker needs an answer. It is meant to be run
by hand, never in CI.

Exit code 0 when every case ended with the expected outcome.
"""

from __future__ import annotations

import argparse
import base64
import functools
import http.server
import json
import os
import shutil
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from anical.domain.playback import decide, parse_progress

HERE = Path(__file__).resolve().parent
QUIT_AFTER = """-- Quits partway through, like a user pressing q.
mp.register_event("file-loaded", function()
    mp.add_timeout(2, function() mp.command("quit") end)
end)
"""
WINDOWS = os.name == "nt"
PORT = 8777
VIDEO_SECONDS = 6
CASE_TIMEOUT_S = 90
FAKE_CURL_NAMES = ("curl", "curl_firefox135", "curl_chrome136", "curl_chrome116", "curl_ff117")
EMBED_KEY = b"otaku-embed-v1"

SHOWS = {"101": "Spike Show", "102": "Spike Show: Kōhen"}


# Fixtures --------------------------------------------------------------------


def write_fixtures(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    films = "".join(
        f'<div class="film-detail"><h3 class="film-name"> '
        f'<a href="/spike-show-{"" if id_ == "101" else "kohen-"}{id_}" '
        f'title="{title}">{title}</a></h3></div>\n'
        for id_, title in SHOWS.items()
    )
    (directory / "search.html").write_text(
        f'<html><body>\n{films}<div id="main-sidebar"></div></body></html>', encoding="utf-8"
    )
    (directory / "empty.html").write_text("<html><body><p>No results</p></body></html>", encoding="utf-8")
    for id_, slug in (("101", "spike-show-101"), ("102", "spike-show-kohen-102")):
        items = "".join(
            f'<a class="ep-item" data-number="{n}" data-id="{id_}{n:02d}" href="/watch/{slug}?ep={id_}{n:02d}"></a>'
            for n in (1, 2, 3)
        )
        (directory / f"episodes-{id_}.html").write_text(json.dumps({"html": items}), encoding="utf-8")
    embed = f"http://127.0.0.1:{PORT}/embed/mal/1/1"
    data_hash = base64.b64encode(embed.encode()).decode()
    (directory / "servers.html").write_text(
        f'<div class="server-item" data-type="sub" data-server-name="ZokoAnime" data-hash="{data_hash}"></div>\n'
        f'<div class="server-item" data-type="dub" data-server-name="ZokoAnime" data-hash="{data_hash}"></div>',
        encoding="utf-8",
    )
    # compact separators: ani-cli matches "src":"..." with no spaces
    config = json.dumps(
        {"sources": [{"src": f"http://127.0.0.1:{PORT}/master.m3u8"}], "subtitles": []}, separators=(",", ":")
    ).encode()
    blob = base64.b64encode(bytes(b ^ EMBED_KEY[i % len(EMBED_KEY)] for i, b in enumerate(config))).decode()
    (directory / "embed.html").write_text(f'<script>window.__P="{blob}";</script>', encoding="utf-8")
    (directory / "master.m3u8").write_text(
        "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=320x240,FRAME-RATE=25\nep.mp4\n", encoding="utf-8"
    )


def make_video(path: Path) -> None:
    if path.exists():
        return
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        sys.exit("ffmpeg is needed to make the test video")
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [ffmpeg, "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc=duration={VIDEO_SECONDS}:size=320x240:rate=25",
         "-f", "lavfi", "-i", f"sine=duration={VIDEO_SECONDS}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
        check=True,
    )  # fmt: skip


def serve(directory: Path) -> http.server.ThreadingHTTPServer:
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), functools.partial(Quiet, directory=str(directory)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# Discovery (prototype of the port's AniCliLocator) ---------------------------


def find_bash() -> tuple[str | None, list[str]]:
    """Git for Windows' bash.exe. Never System32\\bash.exe, which is WSL."""
    tried = []
    candidates = []
    try:
        import winreg

        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(hive, r"SOFTWARE\GitForWindows") as key:
                    candidates.append(Path(winreg.QueryValueEx(key, "InstallPath")[0]) / "bin" / "bash.exe")
            except OSError:
                pass
    except ImportError:
        pass
    home = Path.home()
    candidates += [
        home / "scoop" / "apps" / "git" / "current" / "bin" / "bash.exe",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Git" / "bin" / "bash.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Git" / "bin" / "bash.exe",
    ]
    for candidate in candidates:
        tried.append(str(candidate))
        if candidate.is_file():
            return str(candidate), tried
    return None, tried


def find_ani_cli() -> tuple[str | None, list[str]]:
    tried = []
    candidates = []
    if WINDOWS:
        # scoop keeps the real script here; its shims directory only has wrappers.
        candidates.append(Path.home() / "scoop" / "apps" / "ani-cli" / "current" / "ani-cli")
    else:
        found = shutil.which("ani-cli")
        if found:
            candidates.append(Path(found))
        candidates += [Path("/opt/homebrew/bin/ani-cli"), Path("/usr/local/bin/ani-cli")]
    for candidate in candidates:
        tried.append(str(candidate))
        if candidate.is_file():
            return str(candidate), tried
    return None, tried


def find_mpv() -> str | None:
    if WINDOWS:
        real = Path.home() / "scoop" / "apps" / "mpv" / "current" / "mpv.exe"
        if real.is_file():
            return str(real)
    return shutil.which("mpv")


# Helpers ---------------------------------------------------------------------


def install_helpers(source: Path | None, target: Path) -> None:
    """Frozen helpers (a PyInstaller folder) are copied as they are. Without
    them, small sh wrappers run the helper scripts with this Python (POSIX only)."""
    if target.exists():
        shutil.rmtree(target)
    if source:
        shutil.copytree(source, target)
        return
    if WINDOWS:
        sys.exit("--helpers is required on Windows")
    target.mkdir(parents=True)
    data = REPO / "anical" / "playback" / "data"
    for name in ("anical-pick", "anical-mpv"):
        wrapper = target / name
        wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" -I "{data / (name + ".py")}" "$@"\n', encoding="utf-8")
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def install_fake_curl(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for name in FAKE_CURL_NAMES:
        path = target / name
        shutil.copyfile(HERE / "fake-curl", path)
        path.chmod(0o755)


# Watching for console windows (Windows) --------------------------------------


def visible_windows() -> dict[int, dict]:
    if not WINDOWS:
        return {}
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found = {}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            title, cls = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, title, 256)
            user32.GetClassNameW(hwnd, cls, 256)
            found[hwnd] = {"class": cls.value, "title": title.value}
        return True

    user32.EnumWindows(callback, 0)
    return found


CONSOLE_CLASSES = ("ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS", "mintty")


# Cases -----------------------------------------------------------------------

CASES = [
    # name, query, episode, remembered result title, picker answer, mpv extras, expected outcome
    ("pick_then_watch_to_end", "Spike Show", 1, None, "Spike Show", [], "watched"),
    ("remembered_unicode_title_quit_partway", "Spike Show", 2, "Spike Show: Kōhen", None, ["quit_after"], "partial"),
    ("episode_not_released", "Spike Show", 99, "Spike Show", None, [], "episode_not_found"),
    ("no_search_results", "Nothing Here", 1, None, None, [], "show_not_found"),
    ("pick_cancelled", "Spike Show", 1, None, "<cancel>", [], "cancelled"),
]


def run_case(case, ctx) -> dict:
    name, query, episode, remembered, answer, extras, expected = case
    session = ctx["sessions"] / name
    if session.exists():
        shutil.rmtree(session)
    session.mkdir(parents=True)
    progress = session / "progress.json"
    shim_log = session / "shim-args.json"
    flags = list(ctx["mpv_flags"])
    if "quit_after" in extras:
        quit_after = ctx["sessions"] / "quit_after.lua"
        quit_after.write_text(QUIT_AFTER, encoding="utf-8")
        flags.append(f"--script={quit_after}")

    env = dict(os.environ)
    fakes = [] if ctx["real"] else [str(ctx["fakebin"])]
    env["PATH"] = os.pathsep.join([str(ctx["helpers"]), *fakes, env.get("PATH", "")])
    env.update(
        ANI_CLI_MENU="anical-pick",
        ANI_CLI_PLAYER="anical-mpv",
        ANI_CLI_HIST_DIR=str(ctx["history"]),  # never touch the user's history
        ANI_CLI_LOG="0",
        ANICAL_PICK_DIR=str(session),
        ANICAL_PICK_TITLE=remembered or "",
        ANICAL_MPV=ctx["mpv"],
        ANICAL_PROGRESS_SCRIPT=str(REPO / "anical" / "playback" / "data" / "anical-progress.lua"),
        ANICAL_PROGRESS_OUT=str(progress),
        ANICAL_MPV_FLAGS=json.dumps(flags),
        ANICAL_SHIM_LOG=str(shim_log),
        FAKE_HIANIME=str(ctx["fixtures"]),
    )
    argv = ([ctx["bash"]] if WINDOWS else []) + [
        ctx["ani_cli"],
        "--no-detach",
        "--exit-after-play",
        "-e",
        str(episode),
        query,
    ]

    kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.PIPE, "env": env}
    pty_fds = ()
    if WINDOWS:
        kwargs["stdin"] = subprocess.DEVNULL
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        # ani-cli swaps ANI_CLI_MENU for rofi/dmenu when stdin isn't a terminal.
        import pty

        pty_fds = pty.openpty()
        kwargs["stdin"] = pty_fds[1]

    before = visible_windows()
    new_windows: dict[int, dict] = {}
    asked = None
    started = time.monotonic()
    process = subprocess.Popen(argv, **kwargs)
    stderr_chunks: list[bytes] = []
    reader = threading.Thread(target=lambda: stderr_chunks.append(process.stderr.read()), daemon=True)
    reader.start()
    while process.poll() is None and time.monotonic() - started < CASE_TIMEOUT_S:
        choices = session / "choices.json"
        if asked is None and choices.exists():
            asked = json.loads(choices.read_text(encoding="utf-8"))
            if answer == "<ask>":
                for choice in asked["choices"]:
                    print(f"  {choice['index']}. {choice['title']}")
                index = input("Which result? (number, empty to cancel) ").strip() or None
            else:
                index = next((c["index"] for c in asked["choices"] if c["title"] == answer), None)
            partial = session / "answer.json.part"
            partial.write_text(json.dumps({"index": index}), encoding="utf-8")
            os.replace(partial, session / "answer.json")
        for hwnd, info in visible_windows().items():
            if hwnd not in before:
                new_windows.setdefault(hwnd, info)
        time.sleep(0.1)
    timed_out = process.poll() is None
    if timed_out:
        process.kill()
    exit_code = process.wait()
    reader.join(5)
    for fd in pty_fds:
        os.close(fd)
    stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace")

    progress_data = parse_progress(progress.read_text(encoding="utf-8")) if progress.exists() else None
    result = decide(exit_code, progress_data, stderr)
    shim_args = json.loads(shim_log.read_text(encoding="utf-8")) if shim_log.exists() else None
    consoles = [w for w in new_windows.values() if w["class"] in CONSOLE_CLASSES]

    problems = []
    if timed_out:
        problems.append("timed out")
    if expected is not None and result.outcome.value != expected:
        problems.append(f"outcome {result.outcome.value}, expected {expected}")
    if answer not in (None, "<ask>") and asked is None:
        problems.append("the picker never asked")
    if answer is None and asked is not None:
        problems.append("the picker asked although it shouldn’t have")
    if expected in ("watched", "partial"):
        if not shim_args:
            problems.append("the player shim never ran")
        elif not any(a.startswith("--referrer=http://127.0.0.1") for a in shim_args):
            problems.append("the shim didn’t get --referrer")
    if consoles:
        problems.append(f"console windows appeared: {consoles}")
    return {
        "case": name,
        "ok": not problems,
        "problems": problems,
        "outcome": result.outcome.value,
        "percent": result.percent,
        "message": result.message,
        "exit_code": exit_code,
        "seconds": round(time.monotonic() - started, 1),
        "picker_choices": asked["choices"] if asked else None,
        "progress": progress_data.__dict__ if progress_data else None,
        "shim_args": shim_args,
        "new_windows": list(new_windows.values()),
        "stderr_tail": stderr[-1500:],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--work", required=True, type=Path, help="scratch directory; give it a space in its name")
    parser.add_argument("--helpers", type=Path, help="folder with frozen anical-pick and anical-mpv")
    parser.add_argument("--ani-cli")
    parser.add_argument("--bash")
    parser.add_argument("--mpv")
    parser.add_argument("--headless", action="store_true", help="no video or audio output")
    parser.add_argument("--only", help="run only the case with this name")
    parser.add_argument("--real", metavar="TITLE", help="play one episode from the real source (by hand only)")
    parser.add_argument("--episode", type=int, default=1)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    work = args.work.resolve()
    discovery = {}
    bash, tried = (args.bash, []) if args.bash or not WINDOWS else find_bash()
    discovery["bash"] = {"found": bash, "tried": tried}
    ani_cli, tried = (args.ani_cli, []) if args.ani_cli else find_ani_cli()
    discovery["ani_cli"] = {"found": ani_cli, "tried": tried}
    mpv = args.mpv or find_mpv()
    discovery["mpv"] = {"found": mpv}
    if WINDOWS:
        shims = Path.home() / "scoop" / "shims"
        discovery["scoop_shims"] = sorted(p.name for p in shims.glob("ani-cli*")) if shims.is_dir() else None
    print(json.dumps(discovery, indent=2))
    if not (ani_cli and mpv and (bash or not WINDOWS)):
        (args.out / "report.json").write_text(
            json.dumps({"passed": False, "discovery": discovery}, indent=2), encoding="utf-8"
        )
        return 2
    if WINDOWS:
        ani_cli = ani_cli.replace("\\", "/")  # bash takes C:/... paths as they are

    server = None
    if not args.real:
        www = work / "www"
        make_video(www / "ep.mp4")
        server = serve(www)
    ctx = {
        "bash": bash,
        "ani_cli": ani_cli,
        "mpv": mpv,
        "helpers": work / "helper bin",
        "fakebin": work / "fake bin",
        "fixtures": work / "fixtures",
        "sessions": work / "sessions",
        "history": work / "history",
        "mpv_flags": [] if args.real else ["--no-config", "--ao=null"] + (["--vo=null"] if args.headless else []),
        "real": bool(args.real),
    }
    install_helpers(args.helpers, ctx["helpers"])
    install_fake_curl(ctx["fakebin"])
    write_fixtures(ctx["fixtures"])
    ctx["history"].mkdir(parents=True, exist_ok=True)

    cases = [("real_source", args.real, args.episode, None, "<ask>", [], None)] if args.real else CASES
    results = []
    for case in cases:
        if args.only and case[0] != args.only:
            continue
        result = run_case(case, ctx)
        results.append(result)
        print(
            f"{'PASS' if result['ok'] else 'FAIL'} {result['case']}: {result['outcome']} "
            f"in {result['seconds']} s {result['problems'] or ''}"
        )
    if server:
        server.shutdown()

    version = subprocess.run(
        ([bash] if WINDOWS else []) + [ani_cli, "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout.strip()
    requests = (
        (ctx["fixtures"] / "requests.log").read_text(encoding="utf-8")
        if (ctx["fixtures"] / "requests.log").exists()
        else ""
    )
    report = {
        "passed": all(r["ok"] for r in results),
        "ani_cli_version": version,
        "work_dir": str(work),
        "discovery": discovery,
        "cases": results,
        "fake_requests": requests.splitlines(),
    }
    (args.out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())

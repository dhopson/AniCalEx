"""anical-mpv: the player AniCal gives ani-cli (ANI_CLI_PLAYER=anical-mpv).

The name matters. ani-cli treats any player whose name contains "mpv" as mpv:
it passes mpv options (--referrer, --audio-file, --sub-file, --force-media-title
and the stream URL), waits for it, and exits with its exit code. It would detach
VLC and IINA instead. So every player goes through this one shim, which starts
the real player in the foreground with those options translated for it:

- mpv: AniCal's progress script, added as separate arguments so no path has to
  survive ani-cli's word splitting of ANI_CLI_PLAYER_FLAGS.
- VLC: its web interface on loopback, with a password made for this launch, is
  asked how far playback got. If VLC rejects those options (a build without
  them), it's started again without them, and AniCal asks the user afterwards.
  It exits 0 for a stream it can't open, so ending at once counts as failing.
- IINA: its mpv's JSON socket is asked the same. A script can't be used, because
  IINA applies command-line options after its mpv has started. Closing the window
  doesn't end IINA, so the shim ends it once its mpv goes idle.

Environment:
- ANICAL_PLAYER: the real player (default: mpv on PATH)
- ANICAL_PLAYER_KIND: "mpv" (default), "vlc" or "iina"
- ANICAL_PROGRESS_SCRIPT: the mpv progress script
- ANICAL_PROGRESS_OUT: the progress file
- ANICAL_PLAYER_FLAGS: extra player options as a JSON list
- ANICAL_SHIM_LOG: if set, the arguments received are written there as JSON (tests)

Standalone on purpose: runs as `python3 -I` (or frozen) with no AniCal imports.
"""

import base64
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

POLL_S = 0.5
REQUEST_TIMEOUT_S = 1
QUIT_WAIT_S = 3
END_MARGIN_S = 2  # a last reading this close to the end means it played to the end: readings are POLL_S apart
VLC_GAVE_UP_WITHIN_S = 5  # ending this soon without ever answering: it refused the options, or the stream


def split_mpv_args(args):
    """ani-cli's mpv arguments as ({option: value}, [stream URLs])."""
    options, urls = {}, []
    for arg in args:
        if arg.startswith("--"):
            name, _, value = arg[2:].partition("=")
            options[name] = value
        else:
            urls.append(arg)
    return options, urls


def vlc_args(args):
    """ani-cli's mpv arguments in VLC's terms, the way its own VLC launch does it."""
    options, urls = split_mpv_args(args)
    argv = ["--play-and-exit"]
    if options.get("referrer"):
        argv.append(f"--http-referrer={options['referrer']}")
    if options.get("force-media-title"):
        argv.append(f"--meta-title={options['force-media-title']}")
    argv += urls
    slaves = "#".join(options[name] for name in ("audio-file", "sub-file") if options.get(name))
    if slaves:
        argv.append(f":input-slave={slaves}")
    return argv


def iina_args(args):
    """ani-cli's mpv arguments in iina-cli's terms. --keep-running keeps iina-cli
    in the foreground. mpv splits these file lists on ':', so URLs escape it."""
    options, urls = split_mpv_args(args)
    argv = ["--no-stdin", "--keep-running"]
    for mpv_name, iina_name, escape in (
        ("referrer", "referrer", False),
        ("audio-file", "audio-files", True),
        ("sub-file", "sub-files", True),
        ("force-media-title", "force-media-title", False),
    ):
        if options.get(mpv_name):
            value = options[mpv_name].replace(":", "\\:") if escape else options[mpv_name]
            argv.append(f"--mpv-{iina_name}={value}")
    return argv + urls


def write_progress(path, reason, percent=None, position=None, duration=None):
    """The same file anical-progress.lua writes."""
    partial = path + ".part"
    with open(partial, "w", encoding="utf-8") as file:
        json.dump({"reason": reason, "percent": percent, "position": position, "duration": duration}, file)
    os.replace(partial, path)


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def vlc_sampler(port, password):
    """A function returning VLC's (percent, position, duration), or None if it doesn't answer."""
    token = base64.b64encode(f":{password}".encode()).decode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/requests/status.json", headers={"Authorization": f"Basic {token}"}
    )

    def sample():
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
                status = json.load(response)
            return float(status["position"]) * 100, float(status["time"]), float(status["length"])
        except (OSError, ValueError, KeyError, TypeError):
            return None  # not up yet, or going away

    return sample


def mpv_request(path, *commands):
    """Send commands to an mpv JSON socket; returns each one's data, in order."""
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(REQUEST_TIMEOUT_S)
        client.connect(path)
        for number, command in enumerate(commands):
            client.sendall(json.dumps({"command": command, "request_id": number}).encode() + b"\n")
        answers, buffer = {}, b""
        while len(answers) < len(commands):
            data = client.recv(65536)
            if not data:
                raise OSError("mpv closed its socket")
            buffer += data
            *lines, buffer = buffer.split(b"\n")
            for line in lines:
                reply = json.loads(line)
                if "request_id" in reply:  # the rest are events
                    answers[reply["request_id"]] = reply.get("data")
    return [answers[number] for number in range(len(commands))]


def iina_sampler(path):
    """A function returning IINA's (percent, position, duration, idle), or None if it doesn't answer."""

    def sample():
        try:
            percent, position, duration, idle = mpv_request(
                path,
                ["get_property", "percent-pos"],
                ["get_property", "time-pos"],
                ["get_property", "duration"],
                ["get_property", "idle-active"],
            )
        except (OSError, ValueError):
            return None
        return percent, position, duration, idle

    return sample


def watch(process, sample, should_end=lambda _reading: False):
    """Wait for the player, keeping its latest reading. Returns (exit code, the
    latest reading while it played, whether it ever answered)."""
    last, answered = None, False
    while process.poll() is None:
        reading = sample()
        if reading is not None:
            answered = True
            if reading[1]:  # a position past zero: it's really playing
                last = reading
            if last and should_end(reading):
                return finish(process), last, True
        time.sleep(POLL_S)
    return process.returncode, last, answered


def finish(process):
    """iina-cli ends when IINA does; this leaves only its own IINA running, not the user's."""
    process.terminate()
    try:
        process.wait(QUIT_WAIT_S)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    return 0


def report(out, last, answered):
    """No answer at all says nothing, so AniCal asks. Answers but no playback means it failed."""
    if not out or not answered:
        return
    if not last:
        write_progress(out, "error")
        return
    percent, position, duration = last[:3]
    at_end = duration and percent is not None and duration * (1 - percent / 100) <= END_MARGIN_S
    write_progress(out, "eof" if at_end else "quit", percent, position, duration)


def run_vlc(player, args, flags, out):
    plain = [player, *flags, *vlc_args(args)]
    port, password = free_port(), secrets.token_urlsafe(16)
    tracked = [
        player,
        "--extraintf=http",
        "--http-host=127.0.0.1",
        f"--http-port={port}",
        f"--http-password={password}",
        # Otherwise a running VLC takes the episode and this one exits. macOS's VLC has no such option.
        *([] if sys.platform == "darwin" else ["--no-one-instance"]),
        *flags,
        *vlc_args(args),
    ]
    started = time.monotonic()
    code, last, answered = watch(subprocess.Popen(tracked), vlc_sampler(port, password))
    gave_up = not answered and time.monotonic() - started < VLC_GAVE_UP_WITHIN_S
    if gave_up and code:
        return subprocess.call(plain)
    report(out, last, answered or gave_up)  # VLC exits 0 even when it can't open the stream
    return code


def run_iina(player, args, flags, out):
    folder = tempfile.mkdtemp(prefix="anical-")  # short: socket paths are limited to about 100 characters
    path = os.path.join(folder, "mpv")
    try:
        argv = [player, f"--mpv-input-ipc-server={path}", *flags, *iina_args(args)]
        code, last, answered = watch(subprocess.Popen(argv), iina_sampler(path), lambda reading: reading[3])
        report(out, last, answered)
        return code
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def run_mpv(player, args, flags, out):
    argv = [player]
    script = os.environ.get("ANICAL_PROGRESS_SCRIPT")
    if script and out:
        argv += [f"--script={script}", f"--script-opts=anical-out={out}"]
    return subprocess.call(argv + flags + args)


RUNNERS = {"mpv": run_mpv, "vlc": run_vlc, "iina": run_iina}


def main():
    args = sys.argv[1:]
    log = os.environ.get("ANICAL_SHIM_LOG")
    if log:
        with open(log, "w", encoding="utf-8") as file:
            json.dump(args, file, ensure_ascii=False)
    player = os.environ.get("ANICAL_PLAYER") or "mpv"
    run = RUNNERS.get(os.environ.get("ANICAL_PLAYER_KIND") or "mpv", run_mpv)
    flags = json.loads(os.environ.get("ANICAL_PLAYER_FLAGS") or "[]")
    try:
        return run(player, args, flags, os.environ.get("ANICAL_PROGRESS_OUT"))
    except OSError as e:
        print(f"anical-mpv: couldn’t start {player}: {e}", file=sys.stderr)
        return 127


if __name__ == "__main__":
    sys.exit(main())

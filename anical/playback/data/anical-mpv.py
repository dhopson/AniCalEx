"""anical-mpv: the player AniCal gives ani-cli (ANI_CLI_PLAYER=anical-mpv).

ani-cli treats any player whose name contains "mpv" as mpv and passes it mpv
options: --referrer, --audio-file, --sub-file, --force-media-title and the
stream URL. This starts the real mpv with AniCal's progress script added as
separate arguments, so no path has to survive ani-cli's word splitting of
ANI_CLI_PLAYER_FLAGS, and it runs in the foreground even where ani-cli would
detach the player (IINA on macOS).

Environment:
- ANICAL_MPV: the real mpv (default: mpv on PATH)
- ANICAL_PROGRESS_SCRIPT, ANICAL_PROGRESS_OUT: the progress script and its output file
- ANICAL_MPV_FLAGS: extra mpv options as a JSON list
- ANICAL_SHIM_LOG: if set, the arguments received are written there as JSON (tests)

Standalone on purpose: runs as `python3 -I` (or frozen) with no AniCal imports.
"""

import json
import os
import subprocess
import sys


def main():
    args = sys.argv[1:]
    log = os.environ.get("ANICAL_SHIM_LOG")
    if log:
        with open(log, "w", encoding="utf-8") as file:
            json.dump(args, file, ensure_ascii=False)
    argv = [os.environ.get("ANICAL_MPV") or "mpv"]
    script, out = os.environ.get("ANICAL_PROGRESS_SCRIPT"), os.environ.get("ANICAL_PROGRESS_OUT")
    if script and out:
        argv += [f"--script={script}", f"--script-opts=anical-out={out}"]
    argv += json.loads(os.environ.get("ANICAL_MPV_FLAGS") or "[]")
    try:
        return subprocess.call(argv + args)
    except OSError as e:
        print(f"anical-mpv: couldn’t start {argv[0]}: {e}", file=sys.stderr)
        return 127


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Stand-in for mpv in tests, started by anical-mpv.

Writes FAKE_PROGRESS where AniCal's progress script would (the file named by
--script-opts=anical-out=...), records its arguments in FAKE_MPV_LOG, and
exits with FAKE_EXIT (default 0).
"""

import json
import os
import sys


def main():
    args = sys.argv[1:]
    if log := os.environ.get("FAKE_MPV_LOG"):
        with open(log, "w", encoding="utf-8") as file:
            json.dump(args, file)
    out = next((a.split("=", 2)[2] for a in args if a.startswith("--script-opts=anical-out=")), None)
    progress = os.environ.get("FAKE_PROGRESS")
    if progress and out:
        with open(out, "w", encoding="utf-8") as file:
            file.write(progress)
    sys.exit(int(os.environ.get("FAKE_EXIT", "0")))


if __name__ == "__main__":
    main()

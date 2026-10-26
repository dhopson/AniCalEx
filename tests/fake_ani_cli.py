#!/usr/bin/env python3
"""Stand-in for ani-cli in tests. Mirrors the parts of ani-cli 5.1.6 AniCal relies on.

Behaviour is set through environment variables:
  FAKE_RESULTS   search results, "|"-separated (default: one result, the query)
  FAKE_EPISODES  highest available episode (default 12)
  FAKE_PROGRESS  JSON the mpv progress script would write ("" = player wrote nothing)
  FAKE_EXIT      exit status after "playing" (default 0)
  FAKE_LOG       file to record argv and relevant environment into
"""

import json
import os
import re
import subprocess
import sys


def die(message):
    sys.stderr.write(f"\33[2K\r\033[1;31m{message}\033[0m\n")
    sys.exit(1)


def main():
    args = sys.argv[1:]
    episode, query, flags = None, [], []
    while args:
        arg = args.pop(0)
        if arg == "-e":
            episode = int(args.pop(0))
        elif arg == "-q":
            flags.append(("-q", args.pop(0)))
        elif arg.startswith("-"):
            flags.append(arg)
        else:
            query.append(arg)
    query = " ".join(query)
    if log := os.environ.get("FAKE_LOG"):
        with open(log, "w", encoding="utf-8") as file:
            json.dump({"argv": sys.argv[1:], "menu": os.environ.get("ANI_CLI_MENU"),
                       "player_flags": os.environ.get("ANI_CLI_PLAYER_FLAGS")}, file)
    if not query:
        die("No query")

    results = os.environ.get("FAKE_RESULTS", query).split("|")
    results = [r for r in results if r]
    if not results:
        die("No results found!")
    if len(results) > 1:
        menu_input = "".join(f"{i} {title}\n" for i, title in enumerate(results, start=1))
        reply = subprocess.run(
            [os.environ["ANI_CLI_MENU"], "Select anime: "], input=menu_input, capture_output=True, text=True
        ).stdout.strip()
        index = reply.split(" ")[0]
        if not index.isdigit() or not 1 <= int(index) <= len(results):
            die("Invalid anime selection")

    if episode is None or episode > int(os.environ.get("FAKE_EPISODES", "12")):
        die("Invalid episode!")

    progress = os.environ.get("FAKE_PROGRESS")
    match = re.search(r"--script-opts=anical-out=(\S+)", os.environ.get("ANI_CLI_PLAYER_FLAGS", ""))
    if progress and match:
        with open(match.group(1), "w", encoding="utf-8") as file:
            file.write(progress)
    sys.exit(int(os.environ.get("FAKE_EXIT", "0")))


if __name__ == "__main__":
    main()

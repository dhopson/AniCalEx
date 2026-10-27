"""AniCal's picker for ani-cli (set as ANI_CLI_MENU).

ani-cli runs it as `anical-pick PROMPT` (found on PATH) with choices on stdin, one per line,
as "N Title", and reads the chosen line from stdout (an empty answer cancels).

- If ANICAL_PICK_TITLE matches exactly one choice, that choice is printed.
- Otherwise the choices are written to $ANICAL_PICK_DIR/choices.json and the
  picker waits for AniCal to write answer.json ({"index": "N"} or {"index": null}).
- ani-cli's post-playback menu (only reached with non-mpv players) is answered
  with "quit" so the process ends.

Standalone on purpose: runs as `python3 -I` with no AniCal imports.
"""

import json
import os
import sys
import time
import unicodedata

WAIT_S = 300
POLL_S = 0.2


def normalize(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def write_atomic(path, data):
    partial = path + ".part"
    with open(partial, "w", encoding="utf-8") as file:
        json.dump(data, file)
    os.replace(partial, path)


def main():
    # ani-cli talks UTF-8 with "\n" endings; a Windows console program would
    # otherwise read the locale code page and answer with "\r\n".
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    prompt = sys.argv[-1] if len(sys.argv) > 1 else ""
    lines = [line.rstrip("\n") for line in sys.stdin if line.strip()]
    if prompt.startswith("Playing episode"):
        print("quit")
        return 0
    choices = []
    for line in lines:
        index, _, title = line.partition(" ")
        choices.append({"index": index, "title": title.strip(), "line": line})

    wanted = normalize(os.environ.get("ANICAL_PICK_TITLE", ""))
    if wanted:
        matches = [c for c in choices if normalize(c["title"]) == wanted]
        if len(matches) == 1:
            print(matches[0]["line"])
            return 0

    directory = os.environ.get("ANICAL_PICK_DIR")
    if not directory or not choices:
        return 1
    answer_path = os.path.join(directory, "answer.json")
    write_atomic(
        os.path.join(directory, "choices.json"),
        {"prompt": prompt, "choices": [{"index": c["index"], "title": c["title"]} for c in choices]},
    )
    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        if os.path.exists(answer_path):
            with open(answer_path, encoding="utf-8") as file:
                index = json.load(file).get("index")
            chosen = next((c for c in choices if c["index"] == index), None)
            if chosen is None:
                return 1
            print(chosen["line"])
            return 0
        time.sleep(POLL_S)
    return 1


if __name__ == "__main__":
    sys.exit(main())

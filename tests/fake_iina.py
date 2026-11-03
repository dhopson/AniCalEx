#!/usr/bin/env python3
"""Stand-in for iina-cli in tests, started by anical-mpv.

Records its arguments in FAKE_IINA_LOG and answers mpv's JSON socket with a
position that plays once and then goes idle, as IINA's mpv does when the window
is closed. It then stays running until it's terminated, as IINA does.
"""

import json
import os
import socket
import sys
import time

PLAYING = {"percent-pos": 95.0, "time-pos": 1368.0, "duration": 1440.0, "idle-active": False}
IDLE = {"percent-pos": None, "time-pos": None, "duration": None, "idle-active": True}


def main():
    args = sys.argv[1:]
    with open(os.environ["FAKE_IINA_LOG"], "w", encoding="utf-8") as file:
        json.dump(args, file)
    path = next(a.split("=", 1)[1] for a in args if a.startswith("--mpv-input-ipc-server="))
    server = socket.socket(socket.AF_UNIX)
    server.bind(path)
    server.listen()
    server.settimeout(10)
    for state in (PLAYING, IDLE, IDLE):
        client, _ = server.accept()
        with client, client.makefile("rw", encoding="utf-8") as stream:
            print(json.dumps({"event": "property-change"}), file=stream, flush=True)  # replies mix with events
            for _ in state:
                request = json.loads(stream.readline())
                value = state[request["command"][1]]
                print(json.dumps({"data": value, "request_id": request["request_id"]}), file=stream, flush=True)
    time.sleep(10)
    sys.exit(3)  # not terminated: the test sees a wrong exit code


if __name__ == "__main__":
    main()

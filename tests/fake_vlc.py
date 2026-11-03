#!/usr/bin/env python3
"""Stand-in for VLC in tests, started by anical-mpv.

Records its arguments in FAKE_VLC_LOG and serves FAKE_VLC_STATUS (JSON) as VLC's
web interface does, needing the password it was given, for FAKE_VLC_SECONDS.
With FAKE_VLC_NO_HTTP, it rejects the web interface's options as a build without
them does. With FAKE_VLC_SILENT, it plays without answering.
"""

import base64
import http.server
import json
import os
import sys
import threading
import time


def option(args, name):
    return next((a.split("=", 1)[1] for a in args if a.startswith(f"--{name}=")), None)


def main():
    args = sys.argv[1:]
    if log := os.environ.get("FAKE_VLC_LOG"):
        with open(log, "a", encoding="utf-8") as file:
            file.write(json.dumps(args) + "\n")
    port, password = option(args, "http-port"), option(args, "http-password")
    if port and os.environ.get("FAKE_VLC_NO_HTTP"):
        sys.exit(1)
    if port and not os.environ.get("FAKE_VLC_SILENT"):
        wanted = "Basic " + base64.b64encode(f":{password}".encode()).decode()
        body = os.environ.get("FAKE_VLC_STATUS", "{}").encode()

        class Status(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                allowed = self.headers.get("Authorization") == wanted and self.path == "/requests/status.json"
                self.send_response(200 if allowed else 401)
                self.end_headers()
                if allowed:
                    self.wfile.write(body)

            def log_message(self, *_args):
                pass

        server = http.server.ThreadingHTTPServer((option(args, "http-host"), int(port)), Status)
        threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(float(os.environ.get("FAKE_VLC_SECONDS", "0")))
    sys.exit(int(os.environ.get("FAKE_EXIT", "0")))


if __name__ == "__main__":
    main()

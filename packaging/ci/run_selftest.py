"""Run a built AniCal in self-test mode, screenshot it, and print the report.

Usage: python run_selftest.py OUT_DIR -- COMMAND [ARGS...]

AniCal runs with ANICAL_SELFTEST=OUT_DIR/report.json and a scratch
ANICAL_DATA_DIR, and keeps its window up for a few seconds after writing the
report so the screen can be captured. Exits with AniCal's status, or 2 when no
report was written. Standard library only: it runs on the system Python while
the build toolchain is hidden.
"""

import json
import os
import subprocess
import sys
import tempfile
import time

REPORT_TIMEOUT_S = 120
HOLD_S = 6

_WINDOWS_SCREENSHOT = """
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
$b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
[System.Drawing.Graphics]::FromImage($bmp).CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size)
$bmp.Save($args[0])
"""


def screenshot(path):
    if sys.platform == "darwin":
        argv = ["screencapture", "-x", path]
    elif os.name == "nt":
        argv = ["powershell", "-NoProfile", "-Command", f"& {{{_WINDOWS_SCREENSHOT}}} '{path}'"]
    else:
        return "skipped"
    result = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    return "ok" if result.returncode == 0 else f"failed: {result.stderr.strip()}"


def main():
    out_dir = os.path.abspath(sys.argv[1])
    command = sys.argv[sys.argv.index("--") + 1 :]
    os.makedirs(out_dir, exist_ok=True)
    report_path = os.path.join(out_dir, "report.json")
    data_dir = tempfile.mkdtemp(prefix="AniCal selftest ")  # a space on purpose
    env = dict(os.environ, ANICAL_SELFTEST=report_path, ANICAL_SELFTEST_HOLD_S=str(HOLD_S), ANICAL_DATA_DIR=data_dir)

    started = time.monotonic()
    process = subprocess.Popen(command, env=env)
    while not os.path.exists(report_path) and process.poll() is None and time.monotonic() - started < REPORT_TIMEOUT_S:
        time.sleep(0.2)
    wall = round(time.monotonic() - started, 2)
    shot = "skipped: no report"
    if os.path.exists(report_path) and process.poll() is None:
        time.sleep(1)
        shot = screenshot(os.path.join(out_dir, "screenshot.png"))
    try:
        code = process.wait(timeout=HOLD_S + 30)
    except subprocess.TimeoutExpired:
        process.kill()
        code = 3
    if not os.path.exists(report_path):
        code = code or 2
    print(f"exit {code}, report after {wall} s, screenshot {shot}")
    for name in ("report.json", "report.log"):
        path = os.path.join(out_dir, name)
        if os.path.exists(path):
            print(f"--- {name}")
            print(open(path, encoding="utf-8", errors="replace").read())
    return code


if __name__ == "__main__":
    sys.exit(main())

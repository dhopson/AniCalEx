"""Checks that a packaged build has what AniCal needs at run time (CI).

Run AniCal with ANICAL_SELFTEST=REPORT.json and ANICAL_DATA_DIR pointing at a
scratch folder. It opens its window as usual, runs these checks, writes the
report, and quits with status 0 when every required check passed. A
.log next to the report collects GLib and GTK warnings.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import zoneinfo
from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk

from anical import APP_ID, __version__
from anical.platform import System, current

REQUIRED_ADW = (1, 8)  # Adw.ShortcutsDialog (D22)
ICONS = (
    APP_ID,
    "dialog-information-symbolic",
    "dialog-warning-symbolic",
    "edit-copy-symbolic",
    "object-select-symbolic",
    "view-list-bullet-symbolic",
    "document-edit-symbolic",
    "edit-find-symbolic",
    "folder-open-symbolic",
    "go-previous-symbolic",
    "list-add-symbolic",
    "media-playback-start-symbolic",
    "open-menu-symbolic",
    "view-more-symbolic",
    "x-office-calendar-symbolic",
)


def redirect_stderr(report: str) -> None:
    """A windowed build has no console: send C-level stderr to a file. Call before GTK starts."""
    log = open(os.path.splitext(report)[0] + ".log", "w", encoding="utf-8", buffering=1)  # noqa: SIM115 - stays open while the app runs
    os.dup2(log.fileno(), 2)
    sys.stderr = log


def run(window: Gtk.Window, paths, send) -> dict:
    checks: dict[str, dict] = {}
    system = current()
    frozen = bool(getattr(sys, "frozen", False))

    def check(name: str, required: bool, fn) -> None:
        try:
            ok, detail = fn()
        except Exception as e:  # noqa: BLE001 - recorded in the report
            ok, detail = False, f"{type(e).__name__}: {e}"
        checks[name] = {"ok": bool(ok), "required": required, "detail": detail}

    def adw():
        version = (Adw.get_major_version(), Adw.get_minor_version())
        return version >= REQUIRED_ADW, f"{version}"

    def icons():
        theme = Gtk.IconTheme.get_for_display(window.get_display())
        missing = [name for name in ICONS if not theme.has_icon(name)]
        return not missing, f"missing: {missing}" if missing else "all found"

    def file_chooser():
        source = Gio.SettingsSchemaSource.get_default()
        return source is not None and source.lookup("org.gtk.gtk4.Settings.FileChooser", True) is not None, "schema"

    def tls():
        from anical.net.http import UrllibClient

        return True, f"HTTP {UrllibClient(timeout=15).request('GET', 'https://anilist.co/').status}"

    def zones():
        from anical.platform.timezone import system_timezone_name

        zoneinfo.ZoneInfo("Asia/Tokyo")
        return True, f"system zone {system_timezone_name()}"

    def helpers():
        from anical.playback import install_helpers

        folder, script = install_helpers(paths.runtime / "helpers")

        def program(name: str) -> str:
            return str(next(p for p in folder.iterdir() if p.name in (name, f"{name}.exe")))

        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        pick = subprocess.run(
            [program("anical-pick"), "Playing episode 1 of Show... "],
            input=b"next\nquit\n",
            capture_output=True,
            timeout=30,
            creationflags=flags,
        )
        shim = subprocess.run(
            [program("anical-mpv"), "https://example.invalid/ep.m3u8"],
            capture_output=True,
            timeout=30,
            env={**os.environ, "ANICAL_MPV": str(paths.runtime / "no-such-mpv")},
            creationflags=flags,
        )
        ok = pick.stdout.strip() == b"quit" and shim.returncode == 127 and script.is_file()
        return ok, f"{folder}: picker {pick.stdout.strip()!r}, shim exit {shim.returncode}"

    def token_store():
        from anical.platform.secrets import DpapiTokenStore, KeychainTokenStore, token_store

        store = token_store(paths.data)
        if isinstance(store, DpapiTokenStore):
            from anical.auth.mal_oauth import Token

            with tempfile.TemporaryDirectory() as scratch:
                probe = DpapiTokenStore(Path(scratch) / "probe.bin")
                token = Token("a" * 1500, "r" * 900, 1)
                probe.save(token)
                return probe.load() == token, "DPAPI round trip"
        if isinstance(store, KeychainTokenStore):
            import keyring

            backend = type(keyring.get_keyring()).__module__
            return "macOS" in backend, backend
        return True, type(store).__name__

    def notifications():
        return True, getattr(send, "backend", None) or "Gio.Notification"

    def ani_cli_runs():
        """ani-cli --version through the same launch code as playback (Git Bash and
        no console window on Windows, a pseudo-terminal elsewhere)."""
        from anical.platform.anicli import find_ani_cli, find_bash
        from anical.ui.playback_controller import _spawn

        ani_cli = find_ani_cli()
        bash = find_bash() if system is System.WINDOWS else None
        if ani_cli is None or (system is System.WINDOWS and bash is None):
            return False, f"not installed (ani-cli {ani_cli}, bash {bash})"
        argv = [bash, ani_cli.replace("\\", "/")] if bash else [ani_cli]
        process, terminal = _spawn([*argv, "--version"], dict(os.environ))
        try:
            _stdout, stderr = process.communicate(timeout=60)
        finally:
            if terminal is not None:
                os.close(terminal)
        return (
            process.returncode == 0,
            f"{' '.join(argv)}: exit {process.returncode} {stderr.decode(errors='replace')[-300:]}",
        )

    def setup_art():
        from gi.repository import Gdk

        from anical.ui.setup_wizard import POSES, mascot_path

        sizes = {pose: Gdk.Texture.new_from_filename(str(mascot_path(pose))).get_width() for pose in POSES}
        return all(width > 0 for width in sizes.values()), f"{sizes}"

    def programs():
        from anical.platform.anicli import find_ani_cli, find_bash, find_player

        found = {"ani-cli": find_ani_cli(), "mpv": find_player()}
        if system is System.WINDOWS:
            found["bash"] = find_bash()
        return True, json.dumps(found)

    check("adw_version", True, adw)
    check("icons", True, icons)
    check("file_chooser_schema", True, file_chooser)
    check("tls", True, tls)
    check("time_zones", True, zones)
    check("playback_helpers", True, helpers)
    check("token_store", frozen and system is not System.LINUX, token_store)
    check("setup_wizard_art", True, setup_art)
    if os.environ.get("ANICAL_SELFTEST_WIZARD") or os.environ.get("ANICAL_SELFTEST_DIALOG"):
        check("dialog_shown", False, lambda: _dialog_state(window))
    check("notifications", False, notifications)
    check("playback_programs", False, programs)
    check("ani_cli_runs", bool(os.environ.get("ANICAL_SELFTEST_ANICLI")), ani_cli_runs)
    if frozen and system is System.MACOS:
        checks["notifications"].update(required=True, ok=getattr(send, "backend", "") == "CocoaNotificationCenter")
    if frozen and system is System.WINDOWS:
        ready = getattr(send, "ready", None)
        checks["notifications"].update(required=True, ok=ready is not None and ready.wait(15))
    return {
        "passed": all(c["ok"] for c in checks.values() if c["required"]),
        "version": __version__,
        "system": system.value,
        "platform": platform.platform(),
        "frozen": frozen,
        "gtk": f"{Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()}",
        "adw": f"{Adw.get_major_version()}.{Adw.get_minor_version()}.{Adw.get_micro_version()}",
        "checks": checks,
    }


def _dialog_state(window: Gtk.Window):
    """Diagnostics for screenshot runs: is the dialog hosted inside the window and drawn?"""
    dialog = window.get_visible_dialog() if hasattr(window, "get_visible_dialog") else None
    if dialog is None:
        toplevels = [
            f"{type(w).__name__}(visible={w.get_visible()} mapped={w.get_mapped()} "
            f"{w.get_width()}x{w.get_height()} title={w.get_title()!r})"
            for w in Gtk.Window.list_toplevels()
        ]
        wizard = getattr(window.get_application(), "selftest_wizard", None)
        where = (
            f"wizard root={type(wizard.get_root()).__name__} mapped={wizard.get_mapped()}"
            if wizard
            else "no wizard made"
        )
        return False, f"the window shows no dialog; {where}; toplevels: {toplevels}"
    root = dialog.get_root()
    return dialog.get_mapped(), (
        f"{type(dialog).__name__} mapped={dialog.get_mapped()} size={dialog.get_width()}x{dialog.get_height()} "
        f"root={type(root).__name__} same_window={root is window}"
    )


def schedule(app, window: Gtk.Window, paths, send, report_path: str) -> None:
    """Runs the checks once the window has been up for a moment, then quits.
    $ANICAL_SELFTEST_HOLD_S keeps the window open longer for a screenshot."""
    started = time.monotonic()

    def finish() -> bool:
        report = run(window, paths, send)
        report["seconds_to_checks"] = round(time.monotonic() - started, 2)
        Path(report_path).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        app.selftest_status = 0 if report["passed"] else 1
        GLib.timeout_add_seconds(int(os.environ.get("ANICAL_SELFTEST_HOLD_S", "0")), lambda: app.quit())
        return GLib.SOURCE_REMOVE

    # The checks block the main loop (14 s on macOS CI), so when a wizard step is
    # being screenshotted, let it appear and draw first.
    showing_dialog = os.environ.get("ANICAL_SELFTEST_WIZARD") or os.environ.get("ANICAL_SELFTEST_DIALOG")
    delay_ms = 5000 if showing_dialog else 1500
    GLib.timeout_add(delay_ms, finish)

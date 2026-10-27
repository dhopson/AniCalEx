"""Desktop notifications, independent of how each OS shows them (D24).

A `Notice` says what to show and which application action to activate when
it, or one of its buttons, is clicked. A sender turns it into the OS's
notification:
- Linux: Gio.Notification.
- macOS: desktop-notifier (UNUserNotificationCenter) in this process. It
  needs a signed app bundle; anywhere else it falls back to Gio.
- Windows: the anical-notify helper program, in a frozen build. GLib's own
  Windows notifications drop the buttons.
Anywhere a native route can't start, Gio.Notification is used.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from gi.repository import Gio, GLib

from anical.platform import System, current


@dataclass(frozen=True)
class Button:
    label: str
    action: str  # a detailed action name, e.g. "app.play-episode"
    target: int | None = None


@dataclass(frozen=True)
class Notice:
    title: str
    body: str
    action: str  # activated by clicking the notification itself
    target: int | None = None
    buttons: tuple[Button, ...] = ()


Send = Callable[[str, Notice], None]


def to_gio(notice: Notice) -> Gio.Notification:
    notification = Gio.Notification.new(notice.title)
    notification.set_body(notice.body)
    if notice.target is None:
        notification.set_default_action(notice.action)
    else:
        notification.set_default_action_and_target(notice.action, GLib.Variant.new_int64(notice.target))
    for button in notice.buttons:
        if button.target is None:
            notification.add_button(button.label, button.action)
        else:
            notification.add_button_with_target(button.label, button.action, GLib.Variant.new_int64(button.target))
    return notification


def _activate(app: Gio.Application, action: str, target: int | None) -> None:
    """Run a notice's action on the main thread, as GLib does for its own notifications."""

    def run() -> bool:
        app.activate_action(action.removeprefix("app."), None if target is None else GLib.Variant.new_int64(target))
        return GLib.SOURCE_REMOVE

    GLib.idle_add(run)


class DesktopNotifierSender:
    """macOS: desktop-notifier on an asyncio loop in a worker thread (D24). GTK's
    main loop runs the main CFRunLoop, which delivers clicks; they are passed
    back to the main thread as application actions. Permission is asked for
    with the first notification."""

    def __init__(self, app: Gio.Application):
        from desktop_notifier import DesktopNotifier

        self._app = app
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, name="notifications", daemon=True).start()
        self._notifier = DesktopNotifier(app_name="AniCal")
        self._asked = False

    @property
    def backend(self) -> str:
        return type(self._notifier._backend).__name__

    def __call__(self, key: str, notice: Notice) -> None:
        asyncio.run_coroutine_threadsafe(self._send(notice), self._loop)

    async def _send(self, notice: Notice) -> None:
        from desktop_notifier import Button as NativeButton

        if not self._asked:
            self._asked = True
            if not await self._notifier.has_authorisation():
                await self._notifier.request_authorisation()
        await self._notifier.send(
            title=notice.title,
            message=notice.body,
            buttons=[
                NativeButton(title=b.label, on_pressed=lambda b=b: _activate(self._app, b.action, b.target))
                for b in notice.buttons
            ],
            on_clicked=lambda: _activate(self._app, notice.action, notice.target),
        )


class HelperNotifierSender:
    """Windows: toasts through the anical-notify helper (D24), which runs
    beside AniCal and reports clicks back as JSON lines."""

    backend = "anical-notify"

    def __init__(self, app: Gio.Application, program: Path):
        self._app = app
        self._notices: dict[str, Notice] = {}
        self.ready = threading.Event()
        self._process = subprocess.Popen(
            [str(program)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            encoding="utf-8",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        threading.Thread(target=self._read, name="anical-notify", daemon=True).start()

    def _read(self) -> None:
        for line in self._process.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            kind = event.get("event")
            notice = self._notices.get(event.get("key"))
            if kind == "ready":
                self.ready.set()
            elif kind == "clicked" and notice:
                _activate(self._app, notice.action, notice.target)
            elif kind == "button" and notice and str(event.get("action", "")).isdecimal():
                index = int(event["action"])
                if index < len(notice.buttons):
                    _activate(self._app, notice.buttons[index].action, notice.buttons[index].target)
            elif kind == "error":
                print(f"AniCal: anical-notify: {event.get('message')}", file=sys.stderr)

    def __call__(self, key: str, notice: Notice) -> None:
        self._notices[key] = notice
        message = {
            "op": "send",
            "key": key,
            "title": notice.title,
            "body": notice.body,
            "buttons": [[str(i), b.label] for i, b in enumerate(notice.buttons)],
        }
        try:
            self._process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            self._process.stdin.flush()
        except (OSError, ValueError) as e:
            print(f"AniCal: couldn’t send a notification: {e}", file=sys.stderr)


def _gio_sender(app: Gio.Application) -> Send:
    def send(key: str, notice: Notice) -> None:
        app.send_notification(key, to_gio(notice))

    return send


def notification_sender(app: Gio.Application, system: System | None = None) -> Send:
    system = system or current()
    if getattr(sys, "frozen", False) and system is not System.LINUX:
        try:
            if system is System.MACOS:
                return DesktopNotifierSender(app)
            return HelperNotifierSender(app, Path(sys.executable).parent / "anical-notify" / "anical-notify.exe")
        except Exception as e:  # noqa: BLE001 - notifications are optional; Gio still works
            print(f"AniCal: native notifications unavailable, using GLib: {e}", file=sys.stderr)
    return _gio_sender(app)

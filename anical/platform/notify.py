"""Desktop notifications, independent of how each OS shows them (D24).

A `Notice` says what to show and which application action to activate when
it, or one of its buttons, is clicked. A sender turns it into the OS's
notification:
- Linux: Gio.Notification.
- Windows and macOS: Gio.Notification for now. GLib shows them there but
  drops the buttons on Windows. The anical-notify helper (Windows) and
  desktop-notifier (macOS) replace it when those builds are made.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from gi.repository import Gio, GLib


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


def notification_sender(app: Gio.Application) -> Send:
    def send(key: str, notice: Notice) -> None:
        app.send_notification(key, to_gio(notice))

    return send

"""anical-notify: Windows toasts with buttons for AniCal (D24).

AniCal's GTK build runs on MSYS2's Python, which can't load the WinRT
extension modules that toasts need, so this helper is built separately with
python.org Python (packaging/windows/anical-notify.spec) and shipped in the
anical-notify folder next to AniCal.exe. AniCal starts it when it starts
and talks to it in JSON lines:

  stdin:  {"op": "send", "key": "...", "title": "...", "body": "...", "buttons": [["play", "Play"], ...]}
          {"op": "query", "id": 1}
  stdout: {"event": "ready", "app_id": "..."}
          {"event": "sent", "key": "...", "identifier": "..."}
          {"event": "clicked", "key": "..."}
          {"event": "button", "key": "...", "action": "play"}
          {"event": "dismissed", "key": "..."}
          {"event": "current", "id": 1, "count": N}
          {"event": "error", "message": "..."}

It exits when stdin closes, so it never outlives AniCal.
"""

import asyncio
import json
import sys
import threading

from desktop_notifier import Button, DesktopNotifier

APP_NAME = "AniCal"  # also the AppUserModelID the installer's shortcut carries


def emit(**event):
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def read_stdin(loop, queue):
    for line in sys.stdin:
        if line.strip():
            loop.call_soon_threadsafe(queue.put_nowait, line)
    loop.call_soon_threadsafe(queue.put_nowait, None)


async def handle(notifier, message):
    op = message.get("op")
    if op == "send":
        key = message["key"]
        buttons = [
            Button(title=label, on_pressed=lambda action=action: emit(event="button", key=key, action=action))
            for action, label in message.get("buttons", [])
        ]
        identifier = await notifier.send(
            title=message["title"],
            message=message.get("body", ""),
            buttons=buttons,
            on_clicked=lambda: emit(event="clicked", key=key),
            on_dismissed=lambda: emit(event="dismissed", key=key),
        )
        emit(event="sent", key=key, identifier=identifier)
    elif op == "query":
        current = await notifier.get_current_notifications()
        emit(event="current", id=message.get("id"), count=len(current))
    else:
        emit(event="error", message=f"unknown op {op!r}")


async def main():
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    notifier = DesktopNotifier(app_name=APP_NAME)
    emit(event="ready", app_id=getattr(notifier._backend, "app_id", None))
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue()
    threading.Thread(target=read_stdin, args=(loop, queue), daemon=True).start()
    while (line := await queue.get()) is not None:
        try:
            await handle(notifier, json.loads(line))
        except Exception as e:  # noqa: BLE001 - reported to AniCal
            emit(event="error", message=f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    asyncio.run(main())

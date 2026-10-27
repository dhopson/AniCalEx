"""Pointer gestures shared by the list views."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Gdk, GLib, Gtk


def on_double_click(row: Gtk.ListBoxRow, callback: Callable[[], None]) -> Gtk.GestureClick:
    """Double-clicking `row` calls `callback`; a single click still activates it.

    A list activates a row on the first click, and here activating opens another page
    before a second click could land. So a single click waits out the double-click
    time before activating the row. Keyboard activation (Enter) is unchanged.
    Returns the gesture."""
    gesture = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
    pending = 0

    def cancel() -> None:
        nonlocal pending
        if pending:
            GLib.source_remove(pending)
            pending = 0

    def activate() -> bool:
        nonlocal pending
        pending = 0
        # Gtk.Widget's activate, not Adw.ActionRow's: it goes through the list, so both
        # the list's row-activated and the row's activated handlers run.
        Gtk.Widget.activate(row)
        return GLib.SOURCE_REMOVE

    def pressed(gesture: Gtk.GestureClick, n_press: int, _x: float, _y: float) -> None:
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)  # the list must not activate the row itself
        row.grab_focus()
        cancel()
        if n_press == 2:
            callback()

    def released(_gesture: Gtk.GestureClick, n_press: int, _x: float, _y: float) -> None:
        nonlocal pending
        if n_press == 1:
            delay = Gtk.Settings.get_default().props.gtk_double_click_time
            pending = GLib.timeout_add(delay, activate)

    gesture.connect("pressed", pressed)
    gesture.connect("released", released)
    row.connect("unrealize", lambda *_: cancel())
    row.add_controller(gesture)
    return gesture

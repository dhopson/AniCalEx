"""Pointer and keyboard gestures shared by the list and calendar views: double-click to
play, and a context menu."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Gdk, Gio, GLib, Gtk

from anical.platform import System, current


def context_click_hint() -> str:
    """How to open a context menu, in the platform's words."""
    return "Control-click" if current() is System.MACOS else "Right-click"


def _is_context_click(gesture: Gtk.GestureClick) -> bool:
    """A Control-click on macOS: the usual secondary click there, for a mouse or trackpad
    without a right button (a two-finger tap already arrives as a right-click)."""
    state = gesture.get_current_event_state()
    return current() is System.MACOS and bool(state & Gdk.ModifierType.CONTROL_MASK)


def _on_inner_control(widget: Gtk.Widget, x: float, y: float) -> bool:
    """Whether (x, y) is on a button or check box inside `widget`, which handles its own clicks."""
    target = widget.pick(x, y, Gtk.PickFlags.DEFAULT)
    while target is not None and target is not widget:
        if isinstance(target, Gtk.Button | Gtk.CheckButton | Gtk.Switch):
            return True
        target = target.get_parent()
    return False


def on_double_click(
    widget: Gtk.Widget, on_double: Callable[[], None], on_single: Callable[[], None] | None = None
) -> Gtk.GestureClick:
    """Double-clicking `widget` calls `on_double`.

    With `on_single`, a single click calls it once the double-click time has passed
    without a second click: calling it straight away (say, opening another page) would
    swallow the second click. Without it, a single click does nothing.

    The gesture runs in the capture phase, so the widget's own click handling (a list
    activating its row, a button) never sees the clicks; buttons and check boxes inside
    the widget keep theirs. Returns the gesture."""
    gesture = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY, propagation_phase=Gtk.PropagationPhase.CAPTURE)
    pending = 0

    def cancel() -> None:
        nonlocal pending
        if pending:
            GLib.source_remove(pending)
            pending = 0

    def single() -> bool:
        nonlocal pending
        pending = 0
        on_single()
        return GLib.SOURCE_REMOVE

    def pressed(gesture: Gtk.GestureClick, n_press: int, x: float, y: float) -> None:
        if _is_context_click(gesture) or _on_inner_control(widget, x, y):
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        widget.grab_focus()
        cancel()
        if n_press == 2:
            on_double()

    def released(_gesture: Gtk.GestureClick, n_press: int, _x: float, _y: float) -> None:
        nonlocal pending
        if n_press == 1 and on_single is not None:
            delay = Gtk.Settings.get_default().props.gtk_double_click_time
            pending = GLib.timeout_add(delay, single)

    gesture.connect("pressed", pressed)
    gesture.connect("released", released)
    widget.connect("unrealize", lambda *_: cancel())
    widget.add_controller(gesture)
    return gesture


def on_row_double_click(row: Gtk.ListBoxRow, on_double: Callable[[], None]) -> Gtk.GestureClick:
    """Double-clicking a list row calls `on_double`; a single click still activates it,
    a moment later (see on_double_click). Keyboard activation (Enter) is unchanged."""
    # Gtk.Widget's activate, not Adw.ActionRow's: it goes through the list, so both the
    # list's row-activated and the row's activated handlers run.
    return on_double_click(row, on_double, on_single=lambda: Gtk.Widget.activate(row))


def add_context_menu(widget: Gtk.Widget, menu: Gio.MenuModel) -> None:
    """Show `menu` at the pointer on a right-click (or Control-click on macOS), a long
    press on a touch screen, or the Menu key / Shift+F10 when `widget` has focus."""

    def popup(x: float, y: float) -> None:
        popover = Gtk.PopoverMenu(menu_model=menu, has_arrow=False, halign=Gtk.Align.START)
        popover.set_parent(widget)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)
        # A new popover each time, unparented once closed (after its action has run), so
        # nothing is left attached to the widget.
        popover.connect("closed", lambda p: GLib.idle_add(p.unparent))
        popover.popup()

    def clicked(gesture: Gtk.GestureClick, _n_press: int, x: float, y: float) -> None:
        if gesture.get_current_button() == Gdk.BUTTON_PRIMARY and not _is_context_click(gesture):
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        popup(x, y)

    # Any button: secondary, and primary for Control-click on macOS; other primary
    # clicks are let through to on_double_click.
    click = Gtk.GestureClick(button=0, propagation_phase=Gtk.PropagationPhase.CAPTURE)
    click.connect("pressed", clicked)
    widget.add_controller(click)

    def held(gesture: Gtk.GestureLongPress, x: float, y: float) -> None:
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        popup(x, y)

    long_press = Gtk.GestureLongPress(touch_only=True)
    long_press.connect("pressed", held)
    widget.add_controller(long_press)

    def from_keyboard(*_) -> bool:
        popup(widget.get_width() / 2, widget.get_height() / 2)
        return True

    keys = Gtk.ShortcutController()
    keys.add_shortcut(
        Gtk.Shortcut(
            trigger=Gtk.ShortcutTrigger.parse_string("Menu|<Shift>F10"),
            action=Gtk.CallbackAction.new(from_keyboard),
        )
    )
    widget.add_controller(keys)

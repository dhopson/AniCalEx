"""The "Playing episode" card shown in the window's corner while ani-cli starts an episode."""

from __future__ import annotations

import importlib.resources

from gi.repository import Gdk, GLib, Graphene, Gtk

# The animation is one sprite sheet: FRAMES frames of FRAME_W x FRAME_H, COLUMNS to a row,
# played at FPS. Made with the local ComfyUI; see packaging/art/mascot.json.
FRAMES = 48
COLUMNS = 8
FRAME_W, FRAME_H = 480, 332
FPS = 16
SCENE_WIDTH = 288  # shown size; the sheet is drawn larger for HiDPI screens


def theatre_path():
    return importlib.resources.files("anical") / "data" / "playing" / "theatre.jpg"


class TheatreAnimation(Gtk.Widget):
    """The mascot in a theatre seat with popcorn, watching an old TV, looping.

    The sheet is only loaded while the widget is mapped, and frames only advance then.
    With animations turned off in the system settings, the first frame stays still."""

    def __init__(self):
        super().__init__(css_classes=["playing-scene"], overflow=Gtk.Overflow.HIDDEN)
        self.set_size_request(SCENE_WIDTH, round(SCENE_WIDTH * FRAME_H / FRAME_W))
        self.update_property([Gtk.AccessibleProperty.LABEL], ["The mascot eating popcorn in front of a TV"])
        self._sheet: Gdk.Texture | None = None
        self._frame = 0
        self._start_us = 0
        self._tick = 0

    def do_map(self) -> None:
        Gtk.Widget.do_map(self)
        try:
            self._sheet = Gdk.Texture.new_from_filename(str(theatre_path()))
        except GLib.Error:
            self._sheet = None  # a broken install: the card still says what's playing
        self._frame = 0
        self._start_us = 0
        if Gtk.Settings.get_default().props.gtk_enable_animations:
            self._tick = self.add_tick_callback(self._on_tick)

    def do_unmap(self) -> None:
        if self._tick:
            self.remove_tick_callback(self._tick)
            self._tick = 0
        self._sheet = None  # about 30 MB decoded; nothing to keep while hidden
        Gtk.Widget.do_unmap(self)

    def _on_tick(self, _widget, clock: Gdk.FrameClock) -> bool:
        now = clock.get_frame_time()
        self._start_us = self._start_us or now
        frame = int((now - self._start_us) * FPS / 1_000_000) % FRAMES
        if frame != self._frame:
            self._frame = frame
            self.queue_draw()
        return GLib.SOURCE_CONTINUE

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        if self._sheet is None:
            return
        width, height = self.get_width(), self.get_height()
        scale = max(width / FRAME_W, height / FRAME_H)  # cover the widget, like a photo
        column, row = self._frame % COLUMNS, self._frame // COLUMNS
        x = (width - FRAME_W * scale) / 2 - column * FRAME_W * scale
        y = (height - FRAME_H * scale) / 2 - row * FRAME_H * scale
        snapshot.push_clip(Graphene.Rect().init(0, 0, width, height))
        snapshot.append_texture(
            self._sheet,
            Graphene.Rect().init(x, y, self._sheet.get_width() * scale, self._sheet.get_height() * scale),
        )
        snapshot.pop()


class PlayingPopup(Gtk.Revealer):
    """A small card saying which episode is starting, over the window's lower right corner.

    Doesn't block the window. Hidden when playback ends, or with its close button."""

    def __init__(self):
        super().__init__(
            transition_type=Gtk.RevealerTransitionType.CROSSFADE,
            halign=Gtk.Align.END,
            valign=Gtk.Align.END,
            margin_end=12,
            margin_bottom=12,
            can_target=False,
        )
        self._title = Gtk.Label(label="Playing episode…", xalign=0, css_classes=["heading"])
        self._episode = Gtk.Label(
            xalign=0, wrap=True, max_width_chars=1, hexpand=True, css_classes=["dim-label", "caption"]
        )
        close = Gtk.Button(
            icon_name="window-close-symbolic",
            valign=Gtk.Align.START,
            css_classes=["flat", "circular"],
            tooltip_text="Hide",
        )
        close.connect("clicked", lambda *_: self.dismiss())
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        labels.append(self._title)
        labels.append(self._episode)
        caption = Gtk.Box(spacing=6, margin_start=4)
        caption.append(labels)
        caption.append(close)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, css_classes=["playing-card"])
        card.append(TheatreAnimation())
        card.append(caption)
        self.set_child(card)

    def present(self, label: str) -> None:
        """Show the card for `label` (the show and episode, as the controller names them)."""
        self._episode.set_label(label)
        self.set_can_target(True)
        self.set_reveal_child(True)

    def dismiss(self) -> None:
        self.set_reveal_child(False)
        self.set_can_target(False)  # hidden: clicks reach what's under it

    @property
    def showing(self) -> bool:
        return self.get_reveal_child()

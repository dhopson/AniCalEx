"""A looping animation drawn from one sprite sheet, for the mascot's animated scenes."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources.abc import Traversable

from gi.repository import Gdk, GLib, Graphene, Gtk


@dataclass(frozen=True)
class SpriteSheet:
    """`frames` frames of `frame_w` x `frame_h`, `columns` to a row, played at `fps`.
    `natural_width` is how wide to show it by default; the sheet is drawn larger for HiDPI screens.
    `focus_y` says what stays in view when the height is cropped: 0 keeps the top, 1 the bottom."""

    path: Traversable
    label: str  # for screen readers
    frames: int
    columns: int
    frame_w: int
    frame_h: int
    fps: int
    natural_width: int
    focus_y: float = 0.5

    @property
    def natural_height(self) -> int:
        return round(self.natural_width * self.frame_h / self.frame_w)


class SpriteAnimation(Gtk.Widget):
    """Plays a sprite sheet on a loop, covering the widget like a photo.

    The sheet is only loaded while the widget is mapped, and frames only advance then.
    With animations turned off in the system settings, the first frame stays still."""

    def __init__(self, sheet: SpriteSheet, **props):
        super().__init__(overflow=Gtk.Overflow.HIDDEN, vexpand=True, **props)
        self.update_property([Gtk.AccessibleProperty.LABEL], [sheet.label])
        self._spec = sheet
        self._sheet: Gdk.Texture | None = None
        self._frame = 0
        self._start_us = 0
        self._tick = 0

    def do_measure(self, orientation: Gtk.Orientation, _for_size: int) -> tuple[int, int, int, int]:
        # No minimum: whoever holds it chooses the size, and the natural size is the default.
        horizontal = orientation is Gtk.Orientation.HORIZONTAL
        return 0, self._spec.natural_width if horizontal else self._spec.natural_height, -1, -1

    def do_map(self) -> None:
        Gtk.Widget.do_map(self)
        try:
            self._sheet = Gdk.Texture.new_from_filename(str(self._spec.path))
        except GLib.Error:
            self._sheet = None  # a broken install: the words around it still say what's happening
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
        frame = int((now - self._start_us) * self._spec.fps / 1_000_000) % self._spec.frames
        if frame != self._frame:
            self._frame = frame
            self.queue_draw()
        return GLib.SOURCE_CONTINUE

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        if self._sheet is None:
            return
        spec = self._spec
        width, height = self.get_width(), self.get_height()
        scale = max(width / spec.frame_w, height / spec.frame_h)  # cover the widget, like a photo
        column, row = self._frame % spec.columns, self._frame // spec.columns
        x = (width - spec.frame_w * scale) / 2 - column * spec.frame_w * scale
        y = (height - spec.frame_h * scale) * spec.focus_y - row * spec.frame_h * scale
        snapshot.push_clip(Graphene.Rect().init(0, 0, width, height))
        snapshot.append_texture(
            self._sheet,
            Graphene.Rect().init(x, y, self._sheet.get_width() * scale, self._sheet.get_height() * scale),
        )
        snapshot.pop()

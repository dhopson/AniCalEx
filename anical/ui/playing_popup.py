"""The "Playing episode" card shown over the window while ani-cli starts an episode:
small in the lower right corner, or large and centered."""

from __future__ import annotations

import importlib.resources

from gi.repository import Gdk, Gtk

from anical.ui.sprite_animation import SpriteAnimation, SpriteSheet

# The animation is one sprite sheet: FRAMES frames of FRAME_W x FRAME_H, COLUMNS to a row,
# played at FPS. Made with the local ComfyUI; see packaging/art/mascot.json.
FRAMES = 48
COLUMNS = 8
FRAME_W, FRAME_H = 480, 332
FPS = 16
SCENE_WIDTH = 288  # small size; the sheet is drawn larger for HiDPI screens
SCENE_HEIGHT = round(SCENE_WIDTH * FRAME_H / FRAME_W)
LARGE_FRACTION = 0.75  # of the window, for the whole large card
CORNER_MARGIN = 12


def large_scene_size(window_width: int, window_height: int, chrome_width: int, chrome_height: int) -> tuple[int, int]:
    """The biggest scene, at the art's own proportions, whose card fits in LARGE_FRACTION of
    the window both ways. Whichever side reaches that limit first decides; a very wide window
    just leaves more room at the sides. `chrome_*` is the card's size around the scene.
    Never smaller than the small card's scene, so that Large can't shrink in a small window."""
    by_width = window_width * LARGE_FRACTION - chrome_width
    by_height = (window_height * LARGE_FRACTION - chrome_height) * FRAME_W / FRAME_H
    width = max(SCENE_WIDTH, int(min(by_width, by_height)))
    return width, round(width * FRAME_H / FRAME_W)


def theatre_path():
    return importlib.resources.files("anical") / "data" / "playing" / "theatre.jpg"


# The mascot in a theatre seat with popcorn, watching an old TV.
THEATRE = SpriteSheet(
    theatre_path(),
    "The mascot eating popcorn in front of a TV",
    FRAMES,
    COLUMNS,
    FRAME_W,
    FRAME_H,
    FPS,
    SCENE_WIDTH,
)


class PlayingPopup(Gtk.Widget):
    """A card saying which episode is starting, in the window's lower right corner or,
    when large, centered and sized from the window.

    Fills the window's overlay so that it can size the card from the window, but only the
    card takes clicks: the window stays usable around it. Hidden when playback ends, or
    with its close button."""

    def __init__(self, large: bool = False):
        super().__init__()
        self._large = large
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
        card.append(SpriteAnimation(THEATRE, css_classes=["playing-scene"]))
        card.append(caption)
        self._card = card
        self._revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.CROSSFADE, child=card, can_target=False
        )
        self._revealer.set_parent(self)

    def do_dispose(self) -> None:
        if self._revealer.get_parent() is self:
            self._revealer.unparent()
        Gtk.Widget.do_dispose(self)

    def do_measure(self, _orientation: Gtk.Orientation, _for_size: int) -> tuple[int, int, int, int]:
        return 0, 0, -1, -1  # the card floats over the window, so it never sizes the window

    def do_contains(self, _x: float, _y: float) -> bool:
        return False  # the card still gets its clicks: children are checked before this

    def do_size_allocate(self, width: int, height: int, _baseline: int) -> None:
        rectangle = Gdk.Rectangle()
        rectangle.x, rectangle.y, rectangle.width, rectangle.height = self.card_rectangle(width, height)
        self._revealer.size_allocate(rectangle, -1)

    def card_rectangle(self, window_width: int, window_height: int) -> tuple[int, int, int, int]:
        """Where the card goes in a window of this size, as (x, y, width, height)."""
        natural_width = self._card.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
        chrome_width = natural_width - SCENE_WIDTH
        if not self._large:
            natural_height = self._card.measure(Gtk.Orientation.VERTICAL, natural_width)[1]
            return (
                window_width - natural_width - CORNER_MARGIN,
                window_height - natural_height - CORNER_MARGIN,
                natural_width,
                natural_height,
            )
        # The caption's height at the small width is the most it needs at any larger one.
        chrome_height = self._card.measure(Gtk.Orientation.VERTICAL, natural_width)[1] - SCENE_HEIGHT
        scene_width, scene_height = large_scene_size(window_width, window_height, chrome_width, chrome_height)
        card_width = scene_width + chrome_width
        card_height = scene_height + self._card.measure(Gtk.Orientation.VERTICAL, card_width)[1] - SCENE_HEIGHT
        return (window_width - card_width) // 2, (window_height - card_height) // 2, card_width, card_height

    def set_large(self, large: bool) -> None:
        if large != self._large:
            self._large = large
            self.queue_allocate()

    def present(self, label: str) -> None:
        """Show the card for `label` (the show and episode, as the controller names them)."""
        self._episode.set_label(label)
        self._revealer.set_can_target(True)
        self._revealer.set_reveal_child(True)

    def dismiss(self) -> None:
        self._revealer.set_reveal_child(False)
        self._revealer.set_can_target(False)  # hidden: clicks reach what's under it

    @property
    def showing(self) -> bool:
        return self._revealer.get_reveal_child()

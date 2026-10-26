"""Cover art widgets, with an initials placeholder when there is no image."""

from __future__ import annotations

from gi.repository import Gdk, GLib, Gtk

_textures: dict[str, Gdk.Texture | None] = {}


def _texture(path: str) -> Gdk.Texture | None:
    if path not in _textures:
        try:
            _textures[path] = Gdk.Texture.new_from_filename(path)
        except GLib.Error:
            _textures[path] = None  # missing or undecodable: fall back to initials
    return _textures[path]


def _initials(title: str) -> str:
    words = [w for w in title.split() if w[:1].isalnum()]
    return "".join(w[0] for w in words[:2]).upper() or "?"


class _FixedPicture(Gtk.Picture):
    """A picture that always measures at exactly width x height.

    Gtk.Picture otherwise asks for the image's own size, which a size
    request can raise but not cap.
    """

    def __init__(self, width: int, height: int, **kwargs):
        super().__init__(**kwargs)
        self._size = (width, height)

    def do_measure(self, orientation, for_size):
        size = self._size[0] if orientation == Gtk.Orientation.HORIZONTAL else self._size[1]
        return size, size, -1, -1


def cover(path: str | None, title: str, width: int, height: int) -> Gtk.Widget:
    texture = _texture(path) if path else None
    if texture is not None:
        picture = _FixedPicture(
            width, height, paintable=texture, content_fit=Gtk.ContentFit.COVER, can_shrink=True,
            valign=Gtk.Align.CENTER, overflow=Gtk.Overflow.HIDDEN, css_classes=["cover"],
        )
        picture.set_alternative_text(f"Cover of {title}")
        return picture
    label = Gtk.Label(
        label=_initials(title), width_request=width, height_request=height, valign=Gtk.Align.CENTER,
        css_classes=["cover-placeholder", "title-4" if width >= 80 else "caption-heading"],
    )
    label.update_property([Gtk.AccessibleProperty.LABEL], [f"No cover for {title}"])
    return label

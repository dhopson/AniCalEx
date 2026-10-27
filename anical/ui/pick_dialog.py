"""Asks which of ani-cli's search results is the show being played."""

from __future__ import annotations

from gi.repository import Adw, GLib, Gtk

from anical.ui.playback_controller import PickRequest


class PickDialog(Adw.Dialog):
    """Asks which of ani-cli's search results is the show being played."""

    def __init__(self, request: PickRequest):
        super().__init__(title="Which Show?", content_width=480, content_height=520)
        self._request = request
        self._answered = False

        group = Adw.PreferencesGroup(
            description=(
                f"ani-cli found several matches for “{GLib.markup_escape_text(request.query)}”. "
                f"Your choice is remembered for {GLib.markup_escape_text(request.anime_title)}."
            )
        )
        for index, title in request.choices:
            row = Adw.ActionRow(title=title, use_markup=False, activatable=True)
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda _r, i=index: self._finish(i))
            group.add(row)
        page = Adw.PreferencesPage()
        page.add(group)

        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        header.pack_start(cancel)
        view = Adw.ToolbarView(content=page)
        view.add_top_bar(header)
        self.set_child(view)
        # Closing by any route (Cancel, Escape) without a choice cancels the playback.
        self.connect("closed", lambda *_: self._finish(None))

    def _finish(self, index: str | None) -> None:
        if self._answered:
            return
        self._answered = True
        self._request.respond(index)
        self.close()

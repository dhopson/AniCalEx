"""Per-show settings for finding a show and its episodes in ani-cli."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, Gtk

from anical.db.playback import PlaybackRepository
from anical.domain.models import Anime
from anical.domain.playback import PlaybackMapping

_MODES = [None, "sub", "dub"]


class PlaybackSettingsDialog(Adw.Dialog):
    """A show's playback settings: search text, chosen match, episode offset, sub or dub."""

    def __init__(
        self, playback: PlaybackRepository, anime: Anime, mapping: PlaybackMapping, on_saved: Callable[[], None]
    ):
        super().__init__(title="Playback Settings", content_width=520, content_height=560)
        self._playback = playback
        self._mapping = mapping
        self._on_saved = on_saved
        self._result_title = mapping.result_title

        self._query = Adw.EntryRow(title="Search ani-cli For", text=mapping.query)
        self._match = Adw.ActionRow(title="Chosen Search Result", use_markup=False)
        self._forget = Gtk.Button(label="Forget", valign=Gtk.Align.CENTER)
        self._forget.connect("clicked", lambda *_: self._set_result(None))
        self._match.add_suffix(self._forget)
        self._set_result(mapping.result_title)

        self._offset = Adw.SpinRow.new_with_range(-10_000, 10_000, 1)
        self._offset.set_title("Episode Offset")
        self._offset.set_subtitle(
            "Added to episode numbers when asking ani-cli, e.g. 12 if ani-cli numbers this season from 13"
        )
        self._offset.set_value(mapping.episode_offset)
        self._mode = Adw.ComboRow(
            title="Audio", model=Gtk.StringList.new(["Default (Preferences)", "Subtitled", "Dubbed"])
        )
        self._mode.set_selected(_MODES.index(mapping.mode) if mapping.mode in _MODES else 0)

        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(
            description=f"How AniCal finds “{anime.title}” in ani-cli. Changing the search forgets the chosen result."
        )
        for row in (self._query, self._match, self._offset, self._mode):
            group.add(row)
        page.add(group)

        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        save = Gtk.Button(label="Save", css_classes=["suggested-action"])
        save.connect("clicked", self._on_save)
        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        header.pack_start(cancel)
        header.pack_end(save)
        view = Adw.ToolbarView(content=page)
        view.add_top_bar(header)
        self.set_child(view)
        self.set_default_widget(save)

    def _set_result(self, title: str | None) -> None:
        self._result_title = title
        self._match.set_subtitle(title or "Not chosen yet: you’ll be asked if the search finds several shows")
        self._forget.set_visible(title is not None)

    def _on_save(self, *_):
        query = self._query.get_text().strip()
        if not query:
            self._query.add_css_class("error")
            return
        result_title = self._result_title if query == self._mapping.query else None
        self._playback.save_mapping(
            PlaybackMapping(
                self._mapping.anime_id,
                query,
                result_title,
                int(self._offset.get_value()),
                _MODES[self._mode.get_selected()],
            )
        )
        self.close()
        self._on_saved()

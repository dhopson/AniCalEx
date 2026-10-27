"""The dialog for adding a show to the library, or editing one."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, Gtk

from anical.db.repository import DuplicateExternalIdError, LibraryRepository
from anical.domain.models import AiringStatus, Anime, AnimeDraft, MediaType, ValidationError

_MEDIA_TYPES = list(MediaType)
_STATUSES = list(AiringStatus)


def _optional_int(text: str, field: str, errors: dict[str, str]) -> int | None:
    text = text.strip()
    if not text:
        return None
    if not text.isdecimal():
        errors[field] = "Enter a whole number"
        return None
    return int(text)


class _TextArea(Gtk.Frame):
    def __init__(self, text: str | None, accessible_name: str):
        self._view = Gtk.TextView(
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
            accepts_tab=False,
            top_margin=8,
            bottom_margin=8,
            left_margin=10,
            right_margin=10,
        )
        self._view.get_buffer().set_text(text or "")
        self._view.update_property([Gtk.AccessibleProperty.LABEL], [accessible_name])
        super().__init__(
            child=Gtk.ScrolledWindow(child=self._view, min_content_height=90, propagate_natural_height=True)
        )

    def get_text(self) -> str:
        buffer = self._view.get_buffer()
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)


class AnimeEditorDialog(Adw.Dialog):
    """Add a new anime (`anime=None`) or edit an existing one."""

    def __init__(self, repository: LibraryRepository, anime: Anime | None, on_saved: Callable[[int], None]):
        super().__init__(title="Edit Anime" if anime else "Add Anime", content_width=560, content_height=680)
        self._repo = repository
        self._anime = anime
        self._on_saved = on_saved

        self._title = Adw.EntryRow(title="Title", text=anime.title if anime else "")
        self._title_en = Adw.EntryRow(title="English Title", text=(anime and anime.title_en) or "")
        self._title_ja = Adw.EntryRow(title="Japanese Title", text=(anime and anime.title_ja) or "")
        self._synonyms = _TextArea("\n".join(anime.synonyms) if anime else "", "Other titles")

        self._media_type = Adw.ComboRow(title="Type", model=Gtk.StringList.new([m.label for m in _MEDIA_TYPES]))
        self._media_type.set_selected(_MEDIA_TYPES.index(anime.media_type if anime else MediaType.TV))
        self._status = Adw.ComboRow(title="Airing Status", model=Gtk.StringList.new([s.label for s in _STATUSES]))
        self._status.set_selected(_STATUSES.index(anime.airing_status if anime else AiringStatus.UNKNOWN))
        self._episodes = Adw.EntryRow(
            title="Total Episodes (blank if unknown)",
            input_purpose=Gtk.InputPurpose.DIGITS,
            text=str(anime.total_episodes) if anime and anime.total_episodes is not None else "",
        )
        self._start = Adw.EntryRow(title="Start Date (YYYY-MM-DD)", text=(anime and anime.start_date) or "")
        self._end = Adw.EntryRow(title="End Date (YYYY-MM-DD)", text=(anime and anime.end_date) or "")
        self._mal_id = Adw.EntryRow(
            title="MyAnimeList ID (optional)",
            input_purpose=Gtk.InputPurpose.DIGITS,
            text=str(anime.mal_id) if anime and anime.mal_id else "",
        )
        self._tags = Adw.EntryRow(title="Tags (comma separated)", text=", ".join(anime.tags) if anime else "")
        self._synopsis = _TextArea(anime and anime.synopsis, "Synopsis")
        self._notes = _TextArea(anime and anime.notes, "Notes")

        self._rows_by_field = {
            "title": self._title,
            "total_episodes": self._episodes,
            "start_date": self._start,
            "end_date": self._end,
            "mal_id": self._mal_id,
        }

        page = Adw.PreferencesPage()
        titles = Adw.PreferencesGroup()
        for row in (self._title, self._title_en, self._title_ja):
            titles.add(row)
        page.add(titles)
        others = Adw.PreferencesGroup(title="Other Titles", description="One per line")
        others.add(self._synonyms)
        page.add(others)
        details = Adw.PreferencesGroup(title="Details")
        for row in (self._media_type, self._status, self._episodes, self._start, self._end, self._mal_id, self._tags):
            details.add(row)
        page.add(details)
        for title, area in (("Synopsis", self._synopsis), ("Notes", self._notes)):
            group = Adw.PreferencesGroup(title=title)
            group.add(area)
            page.add(group)

        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        save = Gtk.Button(label="Save" if anime else "Add", css_classes=["suggested-action"])
        save.connect("clicked", self._on_save)
        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        header.pack_start(cancel)
        header.pack_end(save)
        self._title.connect("entry-activated", self._on_save)

        view = Adw.ToolbarView(content=page)
        view.add_top_bar(header)
        self._toasts = Adw.ToastOverlay(child=view)
        self.set_child(self._toasts)
        self.set_default_widget(save)
        self.set_focus(self._title)

    def _draft(self) -> AnimeDraft:
        errors: dict[str, str] = {}
        draft = AnimeDraft(
            title=self._title.get_text(),
            title_en=self._title_en.get_text(),
            title_ja=self._title_ja.get_text(),
            synonyms=tuple(self._synonyms.get_text().splitlines()),
            synopsis=self._synopsis.get_text(),
            media_type=_MEDIA_TYPES[self._media_type.get_selected()],
            airing_status=_STATUSES[self._status.get_selected()],
            total_episodes=_optional_int(self._episodes.get_text(), "total_episodes", errors),
            start_date=self._start.get_text(),
            end_date=self._end.get_text(),
            notes=self._notes.get_text(),
            tags=tuple(self._tags.get_text().split(",")),
            mal_id=_optional_int(self._mal_id.get_text(), "mal_id", errors),
        )
        if errors:
            raise ValidationError(errors)
        return draft

    def _on_save(self, *_):
        for row in self._rows_by_field.values():
            row.remove_css_class("error")
        try:
            draft = self._draft()
            if self._anime is None:
                anime_id = self._repo.create_anime(draft)
            else:
                anime_id = self._anime.id
                self._repo.update_anime(anime_id, draft)
        except ValidationError as e:
            for field in e.errors:
                if row := self._rows_by_field.get(field):
                    row.add_css_class("error")
            first_field, message = next(iter(e.errors.items()))
            self._show_error(message)
            if row := self._rows_by_field.get(first_field):
                row.grab_focus()
            return
        except DuplicateExternalIdError as e:
            self._mal_id.add_css_class("error")
            self._show_error(f"MyAnimeList ID {e.ext_id} is already used by “{e.title}”")
            return
        self.close()
        self._on_saved(anime_id)

    def _show_error(self, message: str) -> None:
        self._toasts.add_toast(Adw.Toast(title=message, use_markup=False, timeout=4))

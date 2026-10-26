from __future__ import annotations

from pathlib import Path

from gi.repository import Adw, Gio, Gtk

from anical.db.mal_import import ImportSummary
from anical.db.repository import LibraryRepository
from anical.ui.editor import AnimeEditorDialog
from anical.ui.library_page import LibraryPage
from anical.ui.show_page import ShowPage
from anical.ui.sync_controller import SyncController


def sync_message(summary: ImportSummary) -> str:
    total = summary.added + summary.updated + summary.unchanged
    parts = [f"Synced {total} show{'' if total == 1 else 's'} from MyAnimeList"]
    if summary.added:
        parts.append(f"{summary.added} new")
    if summary.skipped:
        parts.append(f"{summary.skipped} skipped (incomplete data)")
    return ", ".join(parts)


class MainWindow(Adw.ApplicationWindow):
    def __init__(
        self, *, application: Adw.Application, repository: LibraryRepository, sync_controller: SyncController
    ):
        super().__init__(application=application, title="AniCal", default_width=960, default_height=720)
        self.set_size_request(360, 294)
        self.repository = repository
        self.sync_controller = sync_controller

        self._nav = Adw.NavigationView()
        self._toasts = Adw.ToastOverlay(child=self._nav)
        self.set_content(self._toasts)

        self.library_page = LibraryPage(self)
        self._nav.add(self.library_page)
        # Watched counts and edits made on a show page show up on return.
        self._nav.connect("popped", lambda *_: self.library_page.reload())

        handlers = [
            sync_controller.connect("synced", lambda _c, summary: self.toast(sync_message(summary))),
            sync_controller.connect("library-changed", lambda *_: self._refresh_visible_show()),
        ]
        self.connect("destroy", lambda *_: [sync_controller.disconnect(h) for h in handlers])

        for name, callback in (
            ("add-anime", self._on_add_anime),
            ("search", self._on_search),
            ("sync", self._on_sync),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

    def show_anime(self, anime_id: int) -> None:
        self._nav.push(ShowPage(self, anime_id))

    def _refresh_visible_show(self) -> None:
        page = self._nav.get_visible_page()
        if isinstance(page, ShowPage):
            page.refresh()

    def close_page(self, page: Adw.NavigationPage) -> None:
        if self._nav.get_visible_page() is page:
            self._nav.pop()

    def toast(self, text: str, button_label: str | None = None, on_button=None) -> None:
        toast = Adw.Toast(title=text, use_markup=False)
        if button_label:
            toast.set_button_label(button_label)
            toast.connect("button-clicked", lambda *_: on_button())
        self._toasts.add_toast(toast)

    def _on_add_anime(self, *_):
        def saved(anime_id: int) -> None:
            self.library_page.reload()
            self._nav.pop_to_page(self.library_page)
            self.show_anime(anime_id)

        AnimeEditorDialog(self.repository, anime=None, on_saved=saved).present(self)

    def _on_sync(self, *_):
        if self.sync_controller.config.ready:
            self.sync_controller.sync()
        else:
            self.activate_action("app.preferences", None)

    def _on_search(self, *_):
        self._nav.pop_to_page(self.library_page)
        self.library_page.toggle_search()


class DatabaseErrorWindow(Adw.ApplicationWindow):
    """Shown instead of the library when the database can't be opened or upgraded."""

    def __init__(self, *, application: Adw.Application, path: Path, error: Exception):
        super().__init__(application=application, title="AniCal", default_width=640, default_height=560)
        open_folder = Gtk.Button(label="Open Data Folder", halign=Gtk.Align.CENTER, css_classes=["pill"])
        open_folder.connect(
            "clicked", lambda *_: Gtk.FileLauncher.new(Gio.File.new_for_path(str(path))).open_containing_folder(self)
        )
        status = Adw.StatusPage(
            icon_name="dialog-error-symbolic",
            title="Couldn’t Open Library",
            description=f"{error}\n\nThe library file was not modified:\n{path}",
            child=open_folder,
        )
        view = Adw.ToolbarView(content=status)
        view.add_top_bar(Adw.HeaderBar())
        self.set_content(view)

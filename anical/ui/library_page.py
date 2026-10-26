from __future__ import annotations

from typing import TYPE_CHECKING

from gi.repository import Adw, GObject, Gio, Gtk

from anical.domain.library import LibraryQuery, SortKey, WatchFilter, matches, sort_key
from anical.domain.models import MAL_LIST_STATUS_LABELS, AiringStatus, AnimeSummary
from anical.ui.cover import cover

if TYPE_CHECKING:
    from anical.ui.window import MainWindow

_STATUSES: list[AiringStatus | None] = [None, *AiringStatus]
_WATCH_FILTERS = list(WatchFilter)
_SORT_KEYS = list(SortKey)
_LIST_STATUSES: list[str | None] = [None, *MAL_LIST_STATUS_LABELS]


def progress_text(summary: AnimeSummary) -> str:
    if summary.total_episodes:
        return f"{summary.watched_count} / {summary.total_episodes}"
    if summary.episode_count:
        return f"{summary.watched_count} watched"  # length unknown: don't imply it's finished
    return "No episodes"


class AnimeRow(Adw.ActionRow):
    def __init__(self, summary: AnimeSummary):
        subtitle = f"{summary.airing_status.label} · {summary.media_type.label}"
        if summary.mal_list_status:
            subtitle += f" · {MAL_LIST_STATUS_LABELS[summary.mal_list_status]}"
        super().__init__(title=summary.title, subtitle=subtitle, use_markup=False, activatable=True)
        self.anime_id = summary.id
        self.summary = summary
        self.add_prefix(cover(summary.cover_path, summary.title, 40, 56))
        self.add_suffix(Gtk.Label(label=progress_text(summary), css_classes=["dim-label", "numeric"]))
        self.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))


class LibraryPage(Adw.NavigationPage):
    def __init__(self, window: MainWindow):
        super().__init__(title="Library", tag="library")
        self._window = window
        self._summaries: list[AnimeSummary] = []

        header = Adw.HeaderBar()
        header.pack_start(Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Add Anime", action_name="win.add-anime"))
        menu = Gio.Menu()
        actions = Gio.Menu()
        actions.append("Sync With MyAnimeList", "win.sync")
        menu.append_section(None, actions)
        app_items = Gio.Menu()
        app_items.append("Preferences", "app.preferences")
        app_items.append("About AniCal", "app.about")
        menu.append_section(None, app_items)
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Main Menu", primary=True))
        search_button = Gtk.ToggleButton(icon_name="system-search-symbolic", tooltip_text="Search")
        header.pack_end(search_button)
        self._spinner = Adw.Spinner(tooltip_text="Syncing with MyAnimeList", visible=False)
        header.pack_end(self._spinner)

        self._banner = Adw.Banner(button_label="Retry")
        self._banner.connect("button-clicked", lambda *_: window.sync_controller.sync())

        self._search_entry = Gtk.SearchEntry(placeholder_text="Search titles and tags", hexpand=True)
        self._search_entry.connect("search-changed", self._on_query_changed)
        self._search_bar = Gtk.SearchBar(child=Adw.Clamp(child=self._search_entry, maximum_size=500))
        self._search_bar.connect_entry(self._search_entry)
        self._search_bar.set_key_capture_widget(window)
        search_button.bind_property(
            "active", self._search_bar, "search-mode-enabled",
            GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE,
        )

        self._status_filter = self._dropdown(["Any Status", *(s.label for s in AiringStatus)], "Airing status")
        self._watch_filter = self._dropdown([f.label for f in _WATCH_FILTERS], "Watch progress")
        self._list_filter = self._dropdown(
            ["Any List", *MAL_LIST_STATUS_LABELS.values()], "MyAnimeList list status"
        )
        self._sort = self._dropdown([f"Sort: {k.label}" for k in _SORT_KEYS], "Sort order")
        self._filter_bar = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE, halign=Gtk.Align.CENTER, max_children_per_line=4,
            column_spacing=6, row_spacing=6, margin_top=6, margin_bottom=6, margin_start=12, margin_end=12,
        )
        for dropdown in (self._status_filter, self._watch_filter, self._list_filter, self._sort):
            self._filter_bar.append(dropdown)

        self._list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"], valign=Gtk.Align.START)
        # Rows are built once per reload; queries only re-filter and re-sort them.
        self._current_query = LibraryQuery()
        self._list.set_filter_func(lambda row: matches(row.summary, self._current_query))
        self._list.set_sort_func(self._compare_rows)
        self._list.connect("row-activated", lambda _list, row: window.show_anime(row.anime_id))
        list_view = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            child=Adw.Clamp(child=self._list, margin_top=12, margin_bottom=24, margin_start=12, margin_end=12),
        )

        empty_buttons = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, halign=Gtk.Align.CENTER)
        empty_buttons.append(
            Gtk.Button(label="Add Anime", action_name="win.add-anime", css_classes=["pill", "suggested-action"])
        )
        empty_buttons.append(
            Gtk.Button(label="Import From MyAnimeList", action_name="app.preferences", css_classes=["pill"])
        )
        empty = Adw.StatusPage(
            icon_name="view-list-bullet-symbolic",
            title="No Anime Yet",
            description="Add a show yourself, or import your MyAnimeList list.",
            child=empty_buttons,
        )
        no_results = Adw.StatusPage(
            icon_name="edit-find-symbolic", title="No Results", description="Try a different search or filter."
        )
        self._stack = Gtk.Stack()
        self._stack.add_named(list_view, "list")
        self._stack.add_named(empty, "empty")
        self._stack.add_named(no_results, "no-results")

        view = Adw.ToolbarView(content=self._stack)
        view.add_top_bar(header)
        view.add_top_bar(self._banner)
        view.add_top_bar(self._search_bar)
        view.add_top_bar(self._filter_bar)
        self.set_child(view)

        window.sync_controller.connect("changed", lambda *_: self._update_sync_state())
        window.sync_controller.connect("library-changed", lambda *_: self.reload())
        self._update_sync_state()
        self.reload()

    def _dropdown(self, labels: list[str], accessible_name: str) -> Gtk.DropDown:
        dropdown = Gtk.DropDown.new_from_strings(labels)
        dropdown.set_tooltip_text(accessible_name)
        dropdown.update_property([Gtk.AccessibleProperty.LABEL], [accessible_name])
        dropdown.connect("notify::selected", self._on_query_changed)
        return dropdown

    def toggle_search(self) -> None:
        self._search_bar.set_search_mode(not self._search_bar.get_search_mode())

    def _update_sync_state(self) -> None:
        controller = self._window.sync_controller
        self._spinner.set_visible(controller.busy)
        error = controller.error
        self._banner.set_title(error or "")
        self._banner.set_revealed(bool(error) and not controller.busy)

    def _query(self) -> LibraryQuery:
        return LibraryQuery(
            text=self._search_entry.get_text(),
            airing_status=_STATUSES[self._status_filter.get_selected()],
            watch_filter=_WATCH_FILTERS[self._watch_filter.get_selected()],
            sort=_SORT_KEYS[self._sort.get_selected()],
            mal_list_status=_LIST_STATUSES[self._list_filter.get_selected()],
        )

    def _on_query_changed(self, *_):
        self._render()

    def _compare_rows(self, a: AnimeRow, b: AnimeRow) -> int:
        key_a = sort_key(a.summary, self._current_query.sort)
        key_b = sort_key(b.summary, self._current_query.sort)
        return (key_a > key_b) - (key_a < key_b)

    def reload(self) -> None:
        """Re-read the library from the database and rebuild the rows."""
        self._summaries = self._window.repository.list_summaries()
        self._list.remove_all()
        for summary in self._summaries:
            self._list.append(AnimeRow(summary))
        self._render()

    def _render(self) -> None:
        self._filter_bar.set_visible(bool(self._summaries))
        has_mal = any(s.mal_list_status for s in self._summaries)
        self._list_filter.get_parent().set_visible(has_mal)  # the FlowBoxChild
        if not has_mal and self._list_filter.get_selected():
            self._list_filter.set_selected(0)  # re-renders via notify::selected
            return
        if not self._summaries:
            self._stack.set_visible_child_name("empty")
            return
        query = self._query()
        sort_changed = query.sort is not self._current_query.sort
        self._current_query = query
        self._list.invalidate_filter()
        if sort_changed:
            self._list.invalidate_sort()
        any_visible = any(matches(s, query) for s in self._summaries)
        self._stack.set_visible_child_name("list" if any_visible else "no-results")

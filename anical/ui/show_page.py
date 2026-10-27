from __future__ import annotations

import time
from typing import TYPE_CHECKING

from gi.repository import Adw, Gio, Gtk

from anical.domain.models import (
    MAL_LIST_STATUS_LABELS,
    OVERRIDABLE_FIELDS,
    Anime,
    Episode,
    EpisodeKind,
    EpisodeList,
    WatchState,
)
from anical.domain.calendar import format_time, local_datetime
from anical.db.schedule import EpisodeAiring
from anical.domain.schedule import AiringSource
from anical.domain.playback import history_for
from anical.playback import read_history
from anical.ui.air_dates import AirDatesDialog
from anical.ui.cover import cover
from anical.ui.editor import AnimeEditorDialog

if TYPE_CHECKING:
    from anical.ui.window import MainWindow

# Above this many episodes, only a window around the viewer's progress is built.
EPISODE_WINDOW = 60


_FIELD_LABELS = {
    "title": "Title",
    "title_en": "English title",
    "title_ja": "Japanese title",
    "synonyms": "Other titles",
    "synopsis": "Synopsis",
    "media_type": "Type",
    "airing_status": "Status",
    "total_episodes": "Episodes",
    "start_date": "Start date",
    "end_date": "End date",
}


def _source_text(anime: Anime) -> str:
    if anime.metadata_source == "mal":
        return "MyAnimeList (added manually first)" if anime.added_manually else "MyAnimeList"
    return "Added manually"


def _date_range(anime: Anime) -> str | None:
    if anime.start_date and anime.end_date:
        return f"{anime.start_date} – {anime.end_date}"
    if anime.start_date:
        return f"From {anime.start_date}"
    if anime.end_date:
        return f"Until {anime.end_date}"
    return None


def _selectable_label(text: str, css_classes: list[str]) -> Gtk.Label:
    # Not focusable: a focused selectable label selects all its text when the page opens.
    return Gtk.Label(label=text, wrap=True, xalign=0, selectable=True, focusable=False, css_classes=css_classes)


def _property_row(title: str, value: str) -> Adw.ActionRow:
    row = Adw.ActionRow(title=title, subtitle=value, use_markup=False)
    row.add_css_class("property")
    return row


def _text_group(title: str, text: str) -> Adw.PreferencesGroup:
    group = Adw.PreferencesGroup(title=title)
    group.add(_selectable_label(text, ["body"]))
    return group


class ShowPage(Adw.NavigationPage):
    def __init__(self, window: MainWindow, anime_id: int):
        super().__init__(title="", tag=f"anime-{anime_id}")
        self._window = window
        self._repo = window.repository
        self._schedule = window.schedule
        self._anime_id = anime_id
        self._anime: Anime | None = None

        actions = Gio.SimpleActionGroup()
        for name, callback in (
            ("edit", self._on_edit),
            ("add-episode", lambda *_: self._add_episode(EpisodeKind.REGULAR)),
            ("add-special", lambda *_: self._add_episode(EpisodeKind.SPECIAL)),
            ("mark-all-watched", lambda *_: self._mark_all(WatchState.WATCHED)),
            ("mark-all-unwatched", lambda *_: self._mark_all(WatchState.UNWATCHED)),
            ("revert", self._on_revert),
            ("set-air-dates", self._on_set_air_dates),
            ("clear-air-dates", self._on_clear_air_dates),
            ("playback-settings", lambda *_: window.open_playback_settings(self._anime_id)),
            ("delete", self._on_delete),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            actions.add_action(action)
        self.insert_action_group("show", actions)
        self._actions = actions

        menu = Gio.Menu()
        episodes = Gio.Menu()
        episodes.append("Mark All Watched", "show.mark-all-watched")
        episodes.append("Mark All Unwatched", "show.mark-all-unwatched")
        menu.append_section(None, episodes)
        adding = Gio.Menu()
        adding.append("Add Episode", "show.add-episode")
        adding.append("Add Special", "show.add-special")
        menu.append_section(None, adding)
        schedule = Gio.Menu()
        schedule.append("Set Air Dates…", "show.set-air-dates")
        schedule.append("Clear My Air Dates", "show.clear-air-dates")
        schedule.append("Playback Settings…", "show.playback-settings")
        menu.append_section(None, schedule)
        danger = Gio.Menu()
        danger.append("Delete From Library", "show.delete")
        menu.append_section(None, danger)

        header = Adw.HeaderBar()
        header.pack_end(Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu, tooltip_text="Show Menu"))
        header.pack_end(Gtk.Button(icon_name="document-edit-symbolic", tooltip_text="Edit", action_name="show.edit"))

        self._content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            child=Adw.Clamp(child=self._content, margin_top=24, margin_bottom=24, margin_start=12, margin_end=12),
        )
        view = Adw.ToolbarView(content=scroller)
        view.add_top_bar(header)
        self.set_child(view)

        self._count_label: Gtk.Label | None = None
        self._show_all_episodes = False
        self.refresh()

    def refresh(self) -> None:
        anime = self._repo.get_anime(self._anime_id)
        if anime is None:
            self._window.close_page(self)
            return
        self._anime = anime
        self.set_title(anime.title)
        while child := self._content.get_first_child():
            self._content.remove(child)

        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.CENTER, hexpand=True)
        titles.append(_selectable_label(anime.title, ["title-1"]))
        alternatives = [t for t in (anime.title_en, anime.title_ja, *anime.synonyms) if t]
        if alternatives:
            titles.append(_selectable_label(" · ".join(alternatives), ["dim-label"]))
        episode_list = self._repo.episodes(self._anime_id)
        airings = self._schedule.episode_airings(self._anime_id)
        if next_episode := self._next_to_play(episode_list, airings):
            play = Gtk.Button(
                label=f"Play {next_episode.label}", halign=Gtk.Align.START, margin_top=6,
                css_classes=["pill", "suggested-action"], tooltip_text="Play the next unwatched episode",
            )
            play.connect("clicked", lambda *_: self._window.play_episode(next_episode.id))
            titles.append(play)
        heading = Gtk.Box(spacing=18)
        heading.append(cover(anime.cover_path, anime.title, 100, 142))
        heading.append(titles)
        self._content.append(heading)

        details = Adw.PreferencesGroup(title="Details")
        details.add(_property_row("Status", anime.airing_status.label))
        details.add(_property_row("Type", anime.media_type.label))
        details.add(_property_row("Episodes", str(anime.total_episodes) if anime.total_episodes is not None else "Unknown"))
        if dates := _date_range(anime):
            details.add(_property_row("Aired", dates))
        if anime.mal_id:
            details.add(_property_row("MyAnimeList ID", str(anime.mal_id)))
        if anime.tags:
            details.add(_property_row("Tags", ", ".join(anime.tags)))
        details.add(_property_row("Source", _source_text(anime)))
        if anime.overridden:
            edited = Adw.ActionRow(
                title="Your Edits",
                subtitle=", ".join(_FIELD_LABELS[f] for f in OVERRIDABLE_FIELDS if f in anime.overridden),
                use_markup=False,
            )
            edited.add_css_class("property")
            revert = Gtk.Button(label="Revert", valign=Gtk.Align.CENTER, action_name="show.revert",
                                tooltip_text="Use MyAnimeList’s values again")
            edited.add_suffix(revert)
            details.add(edited)
        self._content.append(details)

        if anime.mal_list_status:
            mal = Adw.PreferencesGroup(title="MyAnimeList")
            mal.add(_property_row("List", MAL_LIST_STATUS_LABELS[anime.mal_list_status]))
            if anime.mal_episodes_watched is not None:
                count = anime.mal_episodes_watched
                mal.add(_property_row("Progress on MyAnimeList", f"{count} episode{'' if count == 1 else 's'} watched"))
            self._content.append(mal)

        if anime.synopsis:
            self._content.append(_text_group("Synopsis", anime.synopsis))
        if anime.notes:
            self._content.append(_text_group("Notes", anime.notes))
        if history := self._history_group(episode_list):
            self._content.append(history)
        self._content.append(self._episodes_group(episode_list, airings))

    @staticmethod
    def _next_to_play(episode_list: EpisodeList, airings: dict[int, EpisodeAiring]) -> Episode | None:
        """The first unwatched numbered episode that has aired (or has no known date)."""
        now = int(time.time())
        for episode in episode_list.episodes:
            airing = airings.get(episode.id)
            if (
                episode.kind is EpisodeKind.REGULAR
                and episode.state is WatchState.UNWATCHED
                and (airing is None or airing.airs_at <= now)
            ):
                return episode
        return None

    def _history_group(self, episode_list: EpisodeList) -> Adw.PreferencesGroup | None:
        """ani-cli's history records the last *launched* episode, so it is only a hint."""
        mapping = self._window.playback_repository.mapping(self._anime_id)
        entry = history_for(read_history(), mapping.result_title if mapping else None)
        if entry is None or not entry.episode.isdecimal():
            return None
        launched = int(entry.episode) - (mapping.episode_offset if mapping else 0)
        group = Adw.PreferencesGroup(title="ani-cli")
        row = Adw.ActionRow(
            title=f"Last launched in ani-cli: Episode {launched}",
            subtitle="ani-cli records what was started, not what was finished",
        )
        earlier = [
            e.id for e in episode_list.episodes
            if e.kind is EpisodeKind.REGULAR and e.number < launched and e.state is WatchState.UNWATCHED
        ]
        if earlier:
            button = Gtk.Button(label=f"Mark 1–{launched - 1} Watched", valign=Gtk.Align.CENTER)
            button.connect("clicked", lambda *_: self._mark_ids(earlier))
            row.add_suffix(button)
        group.add(row)
        return group

    def _mark_ids(self, episode_ids: list[int]) -> None:
        self._repo.set_watch_state(episode_ids, WatchState.WATCHED)
        self._window.library_changed()

    def _episodes_group(self, episode_list: EpisodeList, airings: dict[int, EpisodeAiring]) -> Adw.PreferencesGroup:
        self._count_label = Gtk.Label(css_classes=["dim-label", "numeric"], valign=Gtk.Align.CENTER)
        group = Adw.PreferencesGroup(title="Episodes", header_suffix=self._count_label)
        self._update_count(episode_list)
        if not episode_list.episodes:
            row = Adw.ActionRow(title="No episodes yet", subtitle="Set the episode count, or add episodes one at a time")
            row.add_suffix(
                Gtk.Button(label="Add Episode", valign=Gtk.Align.CENTER, action_name="show.add-episode")
            )
            group.add(row)
        now = int(time.time())
        episodes = episode_list.episodes
        start, end = 0, len(episodes)
        if not self._show_all_episodes and len(episodes) > EPISODE_WINDOW:
            # Long shows: the stretch around the next unwatched episode, not all of them.
            next_index = next((i for i, e in enumerate(episodes) if e.state is WatchState.UNWATCHED), len(episodes) - 1)
            start = max(0, min(next_index - 10, len(episodes) - EPISODE_WINDOW))
            end = start + EPISODE_WINDOW
        if start > 0:
            group.add(self._expand_row(f"Show {start} Earlier Episodes"))
        for episode in episodes[start:end]:
            group.add(self._episode_row(episode, airings.get(episode.id), now))
        if end < len(episodes):
            group.add(self._expand_row(f"Show {len(episodes) - end} Later Episodes"))
        self._actions.lookup_action("clear-air-dates").set_enabled(self._schedule.has_user_airings(self._anime_id))
        return group

    def _expand_row(self, title: str) -> Adw.ButtonRow:
        row = Adw.ButtonRow(title=title, start_icon_name="view-more-horizontal-symbolic")
        row.connect("activated", lambda *_: self._expand_episodes())
        return row

    def _expand_episodes(self) -> None:
        self._show_all_episodes = True
        self.refresh()

    def _airing_text(self, airing: EpisodeAiring) -> str:
        display = self._window.display
        moment = local_datetime(airing.airs_at, display.timezone)
        text = f"{moment:%a} {moment.day} {moment:%b %Y}, {format_time(moment, display.use_24h)}"
        if airing.estimated:
            text = f"≈ {text} (estimated)"
        if airing.delayed:
            text += " · delayed"
        if airing.source is AiringSource.USER:
            text += " · your date"
        return text

    def _episode_row(self, episode: Episode, airing: EpisodeAiring | None, now: int) -> Adw.ActionRow:
        subtitle = " · ".join(p for p in (episode.title, airing and self._airing_text(airing)) if p)
        row = Adw.ActionRow(title=episode.label, subtitle=subtitle, use_markup=False)
        check = Gtk.CheckButton(active=episode.state is WatchState.WATCHED, valign=Gtk.Align.CENTER, tooltip_text="Watched")
        check.update_property([Gtk.AccessibleProperty.LABEL], [f"{episode.label} watched"])
        check.connect("toggled", self._on_episode_toggled, episode.id)
        row.add_prefix(check)
        row.set_activatable_widget(check)
        if episode.kind is EpisodeKind.REGULAR:
            aired = airing is None or airing.airs_at <= now
            play = Gtk.Button(
                icon_name="media-playback-start-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"],
                tooltip_text="Play" if aired else "Not aired yet", sensitive=aired,
            )
            play.update_property([Gtk.AccessibleProperty.LABEL], [f"Play {episode.label}"])
            play.connect("clicked", lambda *_: self._window.play_episode(episode.id))
            row.add_suffix(play)
        return row

    def _update_count(self, episode_list: EpisodeList) -> None:
        watched = episode_list.watched_count
        if not episode_list.regular_count:
            text = ""
        elif self._anime.total_episodes:
            text = f"{watched} of {self._anime.total_episodes} watched"
        else:
            text = f"{watched} watched"  # length unknown: known episodes aren't the total
        self._count_label.set_label(text)

    def _on_episode_toggled(self, check: Gtk.CheckButton, episode_id: int) -> None:
        state = WatchState.WATCHED if check.get_active() else WatchState.UNWATCHED
        self._repo.set_watch_state([episode_id], state)
        self._update_count(self._repo.episodes(self._anime_id))

    def _mark_all(self, state: WatchState) -> None:
        before = self._repo.episodes(self._anime_id).episodes
        changed = [e for e in before if e.state is not state]
        if not changed:
            return
        self._repo.set_watch_state([e.id for e in changed], state)
        self.refresh()

        def undo() -> None:
            # Every changed episode was in the opposite state before.
            previous = WatchState.UNWATCHED if state is WatchState.WATCHED else WatchState.WATCHED
            self._repo.set_watch_state([e.id for e in changed], previous)
            self.refresh()

        noun = "episode" if len(changed) == 1 else "episodes"
        self._window.toast(f"Marked {len(changed)} {noun} {state.value}", "Undo", undo)

    def _add_episode(self, kind: EpisodeKind) -> None:
        self._repo.add_episode(self._anime_id, kind)
        self.refresh()

    def _on_set_air_dates(self, *_):
        episodes = self._repo.episodes(self._anime_id).episodes
        airings = self._schedule.episode_airings(self._anime_id)
        # Start from the first regular episode with no known date.
        first = next((e.number for e in episodes if e.kind is EpisodeKind.REGULAR and e.id not in airings), 1)
        AirDatesDialog(
            self._schedule, self._window.display, self._anime, first, on_saved=self._window.library_changed
        ).present(self._window)

    def _on_clear_air_dates(self, *_):
        count = self._schedule.clear_user_airings(self._anime_id)
        self._window.library_changed()
        self._window.toast(f"Cleared {count} air date{'' if count == 1 else 's'} you set")

    def _on_revert(self, *_):
        self._repo.revert_overrides(self._anime_id)
        self.refresh()
        self._window.toast("Restored MyAnimeList’s details")

    def _on_edit(self, *_):
        AnimeEditorDialog(self._repo, anime=self._anime, on_saved=lambda _id: self.refresh()).present(self._window)

    def _on_delete(self, *_):
        dialog = Adw.AlertDialog(
            heading="Delete From Library?",
            body=f"“{self._anime.title}” and its watch history will be removed. This can’t be undone.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_delete_response)
        dialog.present(self._window)

    def _on_delete_response(self, _dialog, response: str) -> None:
        if response != "delete":
            return
        title = self._anime.title
        self._repo.delete_anime(self._anime_id)
        self._window.close_page(self)
        self._window.toast(f"Deleted “{title}”")

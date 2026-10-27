"""First-run setup wizard, guided by Koyomi, AniCal's mascot.

Four steps, each skippable, with Back available until Finish:
1. Welcome.
2. A MyAnimeList client ID: what it is, the exact values to register with,
   and a field to paste it.
3. Signing in, or importing a public list by username.
4. Playback: whether ani-cli and mpv (and Git Bash on Windows) are installed,
   with Install buttons that open an install script in a terminal window.

Shown once on first launch when no client ID is set; "Set Up AniCal…" in the
main menu opens it again.
"""

from __future__ import annotations

import importlib.resources
import re
from collections.abc import Callable
from pathlib import Path

from gi.repository import Adw, Gdk, GLib, Gtk

from anical.auth.mal_oauth import REDIRECT_URI, OAuthError
from anical.db.settings import SETUP_SEEN, SettingsRepository
from anical.platform import System, current
from anical.platform.terminal import TerminalError, open_install_terminal
from anical.sync.mal_sync import ACCOUNT, USERNAME
from anical.ui.playback_controller import PlaybackController
from anical.ui.sync_controller import SyncController

MAL_API_CONFIG_URL = "https://myanimelist.net/apiconfig"
CLIENT_ID = re.compile(r"[0-9a-f]{32}")  # what MAL hands out; only used for a gentle hint
STEPS = ("welcome", "client-id", "sign-in", "playback")
RECHECK_S = 2


POSES = ("welcome", "client-id", "sign-in", "playback", "done")


def mascot_path(pose: str):
    return importlib.resources.files("anical") / "data" / "setup" / f"mascot-{pose}.png"


def mascot(pose: str) -> Gtk.Picture:
    picture = Gtk.Picture.new_for_filename(str(mascot_path(pose)))
    picture.set_content_fit(Gtk.ContentFit.CONTAIN)
    picture.set_size_request(200, 200)
    picture.set_can_shrink(True)
    return picture


def _label(text: str, *classes: str, **props) -> Gtk.Label:
    return Gtk.Label(label=text, wrap=True, xalign=0, css_classes=list(classes), **props)


class _Program:
    """One program the playback step checks for, with its row widgets."""

    def __init__(self, name: str, install_key: str, resolve: Callable[[], str | None], about: str):
        self.name = name
        self.install_key = install_key
        self.resolve = resolve
        self.row = Adw.ActionRow(title=name, use_markup=False)
        self.icon = Gtk.Image()
        self.row.add_prefix(self.icon)
        self.install = Gtk.Button(label="Install…", valign=Gtk.Align.CENTER)
        self.row.add_suffix(self.install)
        self.about = about
        self.found: str | None = None

    def refresh(self) -> None:
        self.found = self.resolve()
        if self.found:
            self.icon.set_from_icon_name("object-select-symbolic")
            self.icon.set_css_classes(["success"])
            self.row.set_subtitle(self.found)
        else:
            self.icon.set_from_icon_name("dialog-warning-symbolic")
            self.icon.set_css_classes(["warning"])
            self.row.set_subtitle(f"Not installed. {self.about}")
        self.install.set_visible(not self.found)


class SetupWizard(Adw.Dialog):
    def __init__(
        self,
        sync_controller: SyncController,
        playback: PlaybackController,
        settings: SettingsRepository,
        runtime_dir: Path,
        on_finished: Callable[[], None] = lambda: None,
    ):
        super().__init__(title="Set Up AniCal", content_width=780, content_height=600)
        self._sync = sync_controller
        self._playback = playback
        self._settings = settings
        self._runtime_dir = runtime_dir
        self._on_finished = on_finished
        self._step = 0
        self._finished = False
        self._login_cancel = None
        self._recheck_id = 0

        self._title = Adw.WindowTitle(title="Set Up AniCal")
        header = Adw.HeaderBar(title_widget=self._title)

        self._stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT, vexpand=True)
        self._bubbles: dict[str, Gtk.Label] = {}
        self._pictures: dict[str, Gtk.Box] = {}
        self._client_entry: Adw.EntryRow | None = None
        self._client_hint: Gtk.Label | None = None
        self._sign_in_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._programs = self._make_programs()
        self._install_status = _label("", "dim-label")
        for name, build in (
            ("welcome", self._welcome_page),
            ("client-id", self._client_id_page),
            ("sign-in", self._sign_in_page),
            ("playback", self._playback_page),
        ):
            self._stack.add_named(self._page(name, build()), name)

        self._back = Gtk.Button(label="Back")
        self._back.connect("clicked", lambda *_: self._go(self._step - 1))
        self._next = Gtk.Button(css_classes=["suggested-action"])
        self._next.connect("clicked", self._on_next)
        self._dots = [Gtk.Box(css_classes=["wizard-step-dot"], valign=Gtk.Align.CENTER) for _ in STEPS]
        dots = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        for dot in self._dots:
            dots.append(dot)
        bar = Gtk.CenterBox(margin_top=10, margin_bottom=10, margin_start=12, margin_end=12)
        bar.set_start_widget(self._back)
        bar.set_center_widget(dots)
        bar.set_end_widget(self._next)

        view = Adw.ToolbarView(content=self._stack)
        view.add_top_bar(header)
        view.add_bottom_bar(bar)
        self.set_child(view)

        self._handler = sync_controller.connect("changed", lambda *_: self._refresh_sign_in())
        self.connect("closed", self._on_closed)
        self._refresh_sign_in()
        self._go(0)

    # Layout

    def _page(self, name: str, content: Gtk.Widget) -> Gtk.Widget:
        """Koyomi and her speech bubble on the left, the step's controls on the right."""
        bubble = _label("", "wizard-bubble", max_width_chars=26)
        self._bubbles[name] = bubble
        tail = Gtk.Box(css_classes=["wizard-bubble-tail"], halign=Gtk.Align.START)
        picture_slot = Gtk.Box(halign=Gtk.Align.CENTER)
        self._pictures[name] = picture_slot
        picture_slot.append(mascot(name))  # each step's pose shares its name
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0, valign=Gtk.Align.CENTER, width_request=240)
        side.append(bubble)
        side.append(tail)
        side.append(picture_slot)

        scroller = Gtk.ScrolledWindow(child=content, hscrollbar_policy=Gtk.PolicyType.NEVER, hexpand=True)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        page = Gtk.Box(spacing=24, margin_start=24, margin_end=24, margin_top=12, margin_bottom=12)
        # Clamped: given more height, a wrapping label asks to be wider.
        page.append(Adw.Clamp(child=side, maximum_size=260, tightening_threshold=260, hexpand=False))
        page.append(scroller)
        return page

    def _set_mascot(self, name: str, pose: str) -> None:
        slot = self._pictures[name]
        while child := slot.get_first_child():
            slot.remove(child)
        slot.append(mascot(pose))

    # Steps

    def _welcome_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.CENTER)
        box.append(_label("Welcome to AniCal", "title-1"))
        box.append(
            _label(
                "AniCal keeps track of the anime you watch, shows when new episodes air, "
                "and plays them for you. Three quick things to set up:"
            )
        )
        steps = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        for icon, title, subtitle in (
            ("system-users-symbolic", "A MyAnimeList client ID", "A free key that lets AniCal read MyAnimeList"),
            ("view-list-bullet-symbolic", "Your anime list", "Log in, or import a public list"),
            ("media-playback-start-symbolic", "Playback", "Check for ani-cli and mpv, and install them"),
        ):
            row = Adw.ActionRow(title=title, subtitle=subtitle, use_markup=False)
            row.add_prefix(Gtk.Image(icon_name=icon))
            steps.append(row)
        box.append(steps)
        box.append(_label("You can skip any step, and change everything later in Preferences.", "dim-label"))
        return box

    def _client_id_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.append(_label("Get a MyAnimeList Client ID", "title-2"))
        box.append(
            _label(
                "MyAnimeList only answers registered apps. Registering AniCal is free, and AniCal only ever reads your list."
            )
        )

        box.append(_label("1. Open MyAnimeList's API page and choose Create ID.", "heading"))
        open_page = Gtk.Button(label="Open MyAnimeList…", halign=Gtk.Align.START, css_classes=["pill"])
        open_page.connect(
            "clicked", lambda *_: Gtk.UriLauncher.new(MAL_API_CONFIG_URL).launch(self.get_root(), None, None)
        )
        box.append(open_page)

        box.append(_label("2. Fill in the form. Any App Name works, but these two must be exact:", "heading"))
        fields = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        for title, value in (("App Type", "other"), ("App Redirect URL", REDIRECT_URI)):
            row = Adw.ActionRow(title=title, subtitle=value, subtitle_selectable=True, use_markup=False)
            copy = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Copy")
            copy.add_css_class("flat")
            copy.connect(
                "clicked",
                lambda _b, v=value: Gdk.Display.get_default().get_clipboard().set_content(Gdk.ContentProvider.new_for_value(v)),
            )
            row.add_suffix(copy)
            fields.append(row)
        box.append(fields)

        box.append(_label("3. Copy the Client ID it shows you, and paste it here:", "heading"))
        entry_list = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        self._client_entry = Adw.EntryRow(title="Client ID", text=self._sync.config.client_id or "")
        self._client_entry.connect("changed", lambda *_: self._update_client_hint())
        entry_list.append(self._client_entry)
        box.append(entry_list)
        self._client_hint = _label("", "dim-label")
        box.append(self._client_hint)
        self._update_client_hint()
        return box

    def _update_client_hint(self) -> None:
        text = self._client_entry.get_text().strip()
        if not text:
            self._client_hint.set_label("")
        elif CLIENT_ID.fullmatch(text.lower()):
            self._client_hint.set_label("That looks like a client ID.")
        else:
            self._client_hint.set_label("Client IDs are usually 32 letters and digits. Check you copied the Client ID.")
        self._update_buttons()

    def _sign_in_page(self) -> Gtk.Widget:
        return self._sign_in_box

    def _refresh_sign_in(self) -> None:
        box = self._sign_in_box
        while child := box.get_first_child():
            box.remove(child)
        config = self._sync.config
        box.append(_label("Bring In Your List", "title-2"))
        if not config.client_id:
            box.append(_label("This needs a MyAnimeList client ID, from the step before."))
            back = Gtk.Button(label="Add a Client ID", halign=Gtk.Align.START, css_classes=["pill"])
            back.connect("clicked", lambda *_: self._go(1))
            box.append(back)
            box.append(_label("Or skip this, and keep your library on this computer only.", "dim-label"))
        elif config.mode == ACCOUNT:
            box.append(self._done_row(f"Logged in as {config.username}", "Your list is being imported."))
        elif config.mode == USERNAME:
            box.append(self._done_row(f"Importing {config.username}’s public list", "Your list is being imported."))
        else:
            box.append(_label("Log in to import your list, including private entries. AniCal never changes it."))
            login = Gtk.Button(label="Log In with MyAnimeList…", halign=Gtk.Align.START, css_classes=["pill", "suggested-action"])
            login.connect("clicked", self._on_login)
            box.append(login)
            box.append(_label("Or import a public list by username:", "heading", margin_top=12))
            user_list = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
            username = Adw.EntryRow(title="MyAnimeList Username", show_apply_button=True)
            username.connect("apply", self._on_username)
            user_list.append(username)
            box.append(user_list)
        self._sign_in_status = _label("", "dim-label")
        box.append(self._sign_in_status)
        self._update_bubbles()
        self._update_buttons()

    def _done_row(self, title: str, subtitle: str) -> Gtk.Widget:
        rows = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        row = Adw.ActionRow(title=title, subtitle=subtitle, use_markup=False)
        row.add_prefix(Gtk.Image(icon_name="object-select-symbolic", css_classes=["success"]))
        rows.append(row)
        return rows

    def _on_login(self, button: Gtk.Button) -> None:
        try:
            url, cancelled = self._sync.begin_login(self._on_login_finished)
        except OAuthError as e:
            self._sign_in_status.set_label(str(e))
            return
        self._login_cancel = cancelled
        button.set_sensitive(False)
        self._sign_in_status.set_label("Waiting for you to log in and allow AniCal in your browser…")
        Gtk.UriLauncher.new(url).launch(self.get_root(), None, None)

    def _on_login_finished(self, error: str | None) -> None:
        self._login_cancel = None
        if error:
            self._refresh_sign_in()
            self._sign_in_status.set_label(error)

    def _on_username(self, row: Adw.EntryRow) -> None:
        if name := row.get_text().strip():
            self._sync.use_public_list(name)
            self._sync.sync()

    def _make_programs(self) -> list[_Program]:
        playback = self._playback
        programs = [
            _Program("ani-cli", "ani-cli", playback.resolve_executable, "It finds and streams episodes."),
            _Program("mpv", "mpv", playback.resolve_player, "The video player ani-cli uses."),
        ]
        if current() is System.WINDOWS:
            programs.insert(
                0, _Program("Git Bash", "git", playback.resolve_bash, "Part of Git for Windows; runs ani-cli.")
            )
        for program in programs:
            program.install.connect("clicked", lambda _b, p=program: self._install([p]))
        return programs

    def _playback_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.append(_label("Set Up Playback", "title-2"))
        box.append(_label("AniCal plays episodes with ani-cli, which streams them in mpv."))
        rows = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        for program in self._programs:
            rows.append(program.row)
        box.append(rows)
        self._install_note = _label(
            "Install opens a terminal window that explains each step and asks before installing anything. "
            "This page checks again by itself.",
            "dim-label",
        )
        box.append(self._install_note)
        box.append(self._install_status)
        box.append(
            _label(
                "Prefer a streaming site or your own files? Preferences → Playback → Play With → Link or Command.",
                "dim-label",
                margin_top=12,
            )
        )
        return box

    def _install(self, programs: list[_Program]) -> None:
        try:
            open_install_terminal(self._runtime_dir / "setup", [p.install_key for p in programs])
        except (TerminalError, OSError) as e:
            self._install_status.set_label(f"Couldn’t open a terminal: {e}")
            return
        self._install_status.set_label("Follow the steps in the terminal window.")

    def _recheck(self) -> bool:
        for program in self._programs:
            program.refresh()
        all_found = all(p.found for p in self._programs)
        self._set_mascot("playback", "done" if all_found else "playback")
        self._install_note.set_visible(not all_found)
        if all_found:
            self._install_status.set_label("")
        self._update_bubbles()
        return GLib.SOURCE_CONTINUE

    # Navigation

    def go_to(self, step: int) -> None:
        """Show step `step` (1-based)."""
        self._go(step - 1)

    def _go(self, step: int) -> None:
        if self._finished or not 0 <= step < len(STEPS):
            return
        if self._step == 1 and step != 1:
            self._save_client_id()
        self._step = step
        name = STEPS[step]
        self._stack.set_visible_child_name(name)
        self._title.set_subtitle(f"Step {step + 1} of {len(STEPS)}")
        for index, dot in enumerate(self._dots):
            dot.set_css_classes(
                ["wizard-step-dot", "current" if index == step else "done" if index < step else "todo"]
            )
        if self._recheck_id:
            GLib.source_remove(self._recheck_id)
            self._recheck_id = 0
        if name == "playback":
            self._recheck()
            self._recheck_id = GLib.timeout_add_seconds(RECHECK_S, self._recheck)
        self._update_bubbles()
        self._update_buttons()

    def _save_client_id(self) -> None:
        text = self._client_entry.get_text().strip()
        if text != (self._sync.config.client_id or ""):
            self._sync.set_client_id(text)

    def _on_next(self, *_):
        if self._step == len(STEPS) - 1:
            self._finish()
        else:
            self._go(self._step + 1)

    def _finish(self) -> None:
        self._finished = True
        self._settings.set(SETUP_SEEN, True)
        self.close()
        self._on_finished()

    def _update_buttons(self) -> None:
        if not hasattr(self, "_next"):
            return  # still building
        self._back.set_visible(self._step > 0)
        config = self._sync.config
        name = STEPS[self._step]
        if name == "welcome":
            label = "Let’s Go"
        elif name == "client-id":
            label = "Next" if self._client_entry.get_text().strip() else "Skip"
        elif name == "sign-in":
            label = "Next" if config.mode in (ACCOUNT, USERNAME) else "Skip"
        else:
            label = "Finish"
        self._next.set_label(label)

    def _update_bubbles(self) -> None:
        if not self._bubbles:
            return
        config = self._sync.config
        self._bubbles["welcome"].set_label(
            "Hi, I’m Koyomi! Let’s get AniCal ready. It only takes a few steps, and you can skip any of them."
        )
        self._bubbles["client-id"].set_label(
            "First, MyAnimeList needs a key for AniCal, called a client ID. It’s free and takes about a minute."
        )
        if not config.client_id:
            sign_in = "This step needs the client ID from before. Go back to add it, or skip for now."
        elif config.mode in (ACCOUNT, USERNAME):
            sign_in = "Got it! Your list is on its way."
        else:
            sign_in = "Now let’s bring in your anime list. Log in, or use a public list’s username."
        self._bubbles["sign-in"].set_label(sign_in)
        all_found = self._programs and all(p.found for p in self._programs)
        self._bubbles["playback"].set_label(
            "All set! Press Finish and enjoy your shows."
            if all_found
            else "Last one! AniCal plays episodes with ani-cli and mpv. Let’s make sure they’re installed."
        )

    def _on_closed(self, *_):
        self._settings.set(SETUP_SEEN, True)
        if self._recheck_id:
            GLib.source_remove(self._recheck_id)
            self._recheck_id = 0
        if self._login_cancel is not None:
            self._login_cancel.set()
        if self._step == 1:
            self._save_client_id()
        if self._handler:
            self._sync.disconnect(self._handler)
            self._handler = 0  # "closed" can come twice (Finish, then a forced close)


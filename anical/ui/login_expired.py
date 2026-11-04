"""The popup shown when the saved MyAnimeList login stops working: the mascot, hard at
work at an old computer, what AniCal will log in with, and a button to log in again.
It's the size of the setup wizard.

A login only needs a client ID from AniCal, so that's what can be added or corrected here.
The password goes to MyAnimeList's own site in the browser, and AniCal never sees it."""

from __future__ import annotations

import importlib.resources

from gi.repository import Adw, Gtk

from anical.auth.mal_oauth import REDIRECT_URI, OAuthError
from anical.sync.mal_sync import ACCOUNT
from anical.ui.client_id import UNUSUAL_CLIENT_ID, looks_like_client_id, open_registration_page
from anical.ui.setup_wizard import WIZARD_HEIGHT, WIZARD_WIDTH
from anical.ui.sprite_animation import SpriteAnimation, SpriteSheet
from anical.ui.sync_controller import SyncController

# Made with the local ComfyUI; see packaging/art/mascot.json.
TYPING = SpriteSheet(
    importlib.resources.files("anical") / "data" / "login" / "typing.jpg",
    "The mascot typing at an old computer",
    frames=48,
    columns=8,
    frame_w=624,
    frame_h=264,
    fps=16,
    natural_width=WIZARD_WIDTH,
    focus_y=0,  # the words below can need the room: keep her head and hands in view
)


class LoginExpiredDialog(Adw.Dialog):
    """Asks the user to log in to MyAnimeList again, showing what it will use, and
    collecting the client ID if there isn't one. Closes itself once the login works."""

    def __init__(self, sync_controller: SyncController):
        super().__init__(title="Log In Again", content_width=WIZARD_WIDTH, content_height=WIZARD_HEIGHT)
        self._sync = sync_controller
        self._login_cancel = None
        config = sync_controller.config

        text = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
            margin_top=14,
            margin_bottom=6,
            margin_start=24,
            margin_end=24,
        )
        heading = "Your MyAnimeList Login Has Expired" if config.client_id else "AniCal Needs a Client ID to Log In"
        text.append(Gtk.Label(label=heading, xalign=0, wrap=True, css_classes=["title-2"]))
        text.append(
            Gtk.Label(
                label="Log in to keep your list up to date. "
                "You’ll enter your password on MyAnimeList’s own site, so AniCal never sees it.",
                xalign=0,
                wrap=True,
            )
        )
        text.append(self._details(config.client_id, config.username))
        self._status = Gtk.Label(xalign=0, wrap=True, visible=False, css_classes=["dim-label"])
        text.append(self._status)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(SpriteAnimation(TYPING))
        body.append(text)

        not_now = Gtk.Button(label="Not Now")
        not_now.connect("clicked", lambda *_: self.close())
        self._log_in = Gtk.Button(label="Log In with MyAnimeList…", css_classes=["suggested-action"])
        self._log_in.connect("clicked", self._on_log_in)
        bar = Gtk.CenterBox(margin_top=10, margin_bottom=10, margin_start=12, margin_end=12)
        bar.set_start_widget(not_now)
        bar.set_end_widget(self._log_in)

        view = Adw.ToolbarView(content=body)
        view.add_top_bar(Adw.HeaderBar())
        view.add_bottom_bar(bar)
        self.set_child(view)
        self.set_default_widget(self._log_in)
        # Not the field when it's filled in: it opens fully selected, and a stray key would replace the ID.
        self.set_focus(self._log_in if config.client_id else self._client_entry)

        self._handler = sync_controller.connect("changed", lambda *_: self._on_sync_changed())
        self.connect("closed", self._on_closed)
        self._on_client_id_edited()

    def _details(self, client_id: str, username: str | None) -> Gtk.Widget:
        """What the login will use: the client ID, which can be edited, and the account."""
        rows = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE, margin_top=6)
        self._client_entry = Adw.EntryRow(title="Client ID", text=client_id, activates_default=True)
        self._client_entry.connect("changed", lambda *_: self._on_client_id_edited())
        rows.append(self._client_entry)
        self._register = Adw.ActionRow(
            title="Get a Free Client ID",
            subtitle=f"Register an app (type “other”, redirect URL {REDIRECT_URI}), and paste its client ID above.",
            use_markup=False,
        )
        register = Gtk.Button(label="Register…", valign=Gtk.Align.CENTER)
        register.connect("clicked", lambda *_: open_registration_page(self))
        self._register.add_suffix(register)
        rows.append(self._register)
        # MyAnimeList's site decides which account the new login uses, so this is who it was.
        rows.append(
            Adw.ActionRow(
                title="Account",
                subtitle=username or "You’ll choose it on MyAnimeList",
                use_markup=False,
            )
        )
        return rows

    def _say(self, text: str) -> None:
        self._status.set_label(text)
        self._status.set_visible(bool(text))

    @property
    def _waiting(self) -> bool:
        return self._login_cancel is not None

    def _on_client_id_edited(self) -> None:
        text = self._client_entry.get_text().strip()
        self._register.set_visible(not text)
        self._log_in.set_sensitive(bool(text) and not self._waiting)
        if not self._waiting:
            self._say(UNUSUAL_CLIENT_ID if text and not looks_like_client_id(text) else "")

    def _save_client_id(self) -> None:
        text = self._client_entry.get_text().strip()
        if text and text != self._sync.config.client_id:
            self._sync.set_client_id(text)

    def _on_sync_changed(self) -> None:
        config = self._sync.config
        # Only for an account: other changes, like saving a client ID, must leave this open.
        if config.mode == ACCOUNT and not self._sync.login_expired:
            self.close()  # logged in, or a refresh found the login working after all

    def _on_log_in(self, *_) -> None:
        self._save_client_id()
        try:
            url, self._login_cancel = self._sync.begin_login(self._on_login_finished)
        except OAuthError as e:
            self._say(str(e))
            return
        self._client_entry.set_sensitive(False)
        self._log_in.set_sensitive(False)
        self._say("Waiting for you to log in and allow AniCal in your browser…")
        Gtk.UriLauncher.new(url).launch(self.get_root(), None, None)

    def _on_login_finished(self, error: str | None) -> None:
        self._login_cancel = None
        self._client_entry.set_sensitive(True)
        self._on_client_id_edited()
        if error:
            self._say(error)

    def _on_closed(self, *_) -> None:
        if self._login_cancel is not None:
            self._login_cancel.set()
        # Stop listening first: saving emits "changed", and closing again from inside a close aborts.
        if self._handler:
            self._sync.disconnect(self._handler)
            self._handler = 0  # "closed" can come twice
        self._save_client_id()  # typed in, then Not Now: it's still the right ID for next time

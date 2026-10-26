# AniCal

AniCal is a GNOME desktop app for tracking anime. It keeps a local library of shows and which episodes you've watched, imports your MyAnimeList list, shows when new episodes air in a calendar, and plays episodes through your existing [`ani-cli`](https://github.com/pystardust/ani-cli) installation.

**Status: 0.1.0, all planned phases complete.**

- **Local library.** You can add, edit and delete shows by hand. Episodes can be marked watched one at a time or all at once, with undo. The library can be searched, filtered and sorted. All of this works offline, with no account.
- **MyAnimeList import.** You can log in through your browser (OAuth with PKCE), or import a public list by username. Imports run when the app starts if the last successful sync was more than 24 hours ago, then once a day while the app runs, and on demand with <kbd>Ctrl</kbd>+<kbd>R</kbd>.
  - Imports bring in cover art and MAL list statuses.
  - Your edits to imported shows are kept as overrides, which you can revert.
  - MAL progress fills in watched episodes, but it never un-watches anything and never outranks what you marked locally.
  - AniCal never writes anything back to MAL.
- **Airing calendar.** Day, week and month views, plus an "Unwatched" list of episodes that have aired but you haven't marked watched.
  - Times come from [AniList](https://anilist.co), looked up by MAL ID for shows that are airing, upcoming or recently finished. When AniList has nothing for a show, AniCal estimates weekly times from MAL's broadcast slot and labels them as estimates.
  - You can set air dates yourself from a show's menu, either for one episode or weekly. Your dates override both sources.
  - Episodes are marked:
    - **Upcoming** before air time.
    - **Just aired** right after.
    - **Aired** once a configurable delay has passed, meaning the episode is probably watchable. Airing doesn't guarantee an episode is available.
    - **Delayed** when the source moved it later.
  - Shows that are airing but have no known date are listed rather than placed on a guessed date.
  - Times are stored in UTC and shown in your chosen time zone (default: the system's), with correct DST handling.
  - Optionally, the calendar can use English titles where MAL has them (Preferences → Use English Titles).
- **Playback.** Play buttons on the show page and in the calendar run `ani-cli` for that exact episode.
  - The show page also has a "Play Episode N" button for the next unwatched episode that has aired.
  - **Finding the show:** if ani-cli's search finds several shows, AniCal asks which one is right and remembers the answer. Per show, you can change the search text, forget the chosen match, set an episode offset for differently numbered seasons, or choose sub or dub (show menu → Playback Settings…).
  - **Watched tracking:** a small mpv script reports how far playback got. If you play to the end or past 90%, the episode is marked watched, with Undo. If you stop earlier, AniCal asks.
    - Launching an episode never marks it watched.
    - With players other than mpv, progress is unknown, so AniCal asks.
    - Automatic marking can be turned off in Preferences → Playback.
  - **ani-cli's history:** shown as a hint ("Last launched: Episode 5"), because it records what was started, not what was finished. It is never applied automatically. AniCal never writes to it.
  - **Safety:** ani-cli runs without a shell, with the title as a single argument, and only one playback runs at a time. Clear messages cover a missing ani-cli, an episode that isn't released yet, a search that finds nothing (with a button to change the search), and failures.
- **Notifications.** While AniCal runs, you're told once per episode when an episode you follow becomes probably available. Each notification has Play and Open buttons, and more than three at once become a single summary.
  - Estimated times are never announced.
  - Nothing older than 12 hours is announced, so reopening the app after a break doesn't flood you.
  - You can turn notifications off in Preferences → General.
- **Backups.** A rolling set of four weekly backups is kept automatically. Preferences → Data can back up to any file or restore from one; the current library is saved before a restore, and older backups are upgraded.

### Connecting MyAnimeList

1. Register a client ID at <https://myanimelist.net/apiconfig>:
   - App type: *other*
   - Redirect URL: exactly `http://localhost:8765/callback`
2. In AniCal, open **Preferences** (<kbd>Ctrl</kbd>+<kbd>,</kbd>) and paste the client ID into **Client ID**.
3. Either click **Log In**, or enter a public list's username.

The login token is stored in your desktop keyring (Secret Service), never in a file. Requests to MyAnimeList carry only the token or the client ID, plus your username when you import a public list.

AniList requests contain only the MAL and AniList IDs of shows in your library, with no account and no watch history. They are paced to stay inside AniList's rate limit.

## Install (Arch / CachyOS)

```sh
cd packaging
makepkg -si      # builds from this checkout; pulls in python-build and python-installer
```

This installs the `anical` command, a desktop entry, the icon and AppStream metadata, so AniCal appears in GNOME's app grid. To remove it: `sudo pacman -R anical`. Your library stays in `~/.local/share/anical/`.

## Requirements

- Python ≥ 3.12
- For playback: [`ani-cli`](https://github.com/pystardust/ani-cli) (tested with 5.1.6) and mpv
- GTK 4, libadwaita ≥ 1.7, and PyGObject, all from your distribution:
  - Arch / CachyOS: `pacman -S python-gobject gtk4 libadwaita`
  - Fedora: `dnf install python3-gobject gtk4 libadwaita`
  - Debian / Ubuntu: `apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1`

## Development setup

```sh
python3 -m venv --system-site-packages .venv   # system site packages expose the distro's `gi`
.venv/bin/pip install -e '.[dev]'
.venv/bin/anical                               # or: .venv/bin/python -m anical
.venv/bin/python -m pytest
```

If you set `ANICAL_DATA_DIR=/some/dir`, the app uses a separate library there, which is useful when testing.

UI smoke tests (`tests/test_ui_smoke.py`) build the real widgets and are skipped when no display is available. The other tests are headless.

## Data

| What | Where |
|---|---|
| Library database (SQLite) | `$XDG_DATA_HOME/anical/anical.db` (usually `~/.local/share/anical/`) |
| Pre-upgrade backups | `anical.db.pre-migration-<timestamp>.bak` in the same folder |
| Automatic weekly backups (last 4) | `~/.local/share/anical/backups/` |
| Cover images (re-downloadable) | `$XDG_CACHE_HOME/anical/covers/` |
| MyAnimeList login token | desktop keyring, item "AniCal MyAnimeList login" |

Each schema migration runs in its own transaction. If the database already holds data, it is copied before any upgrade. If the file is unreadable, or was written by a newer version of AniCal, the app shows an error and leaves the file untouched.

Backups are complete SQLite files, so any one of them can be restored from Preferences → Data. Before a restore, a copy of the current library is saved as `backups/anical-before-restore-*.db`.

## Privacy

Everything stays on your computer unless one of these applies:

| Service | What is sent | When |
|---|---|---|
| MyAnimeList | Your login token or the client ID; your username for a public list | Refreshes |
| AniList | MAL and AniList IDs of shows in your library | Refreshes |
| MyAnimeList's image server | Requests for cover images | New covers only |
| ani-cli | The search text and episode number | When you press Play (ani-cli then contacts its own sources) |

Watch history is never sent anywhere.

## Layout

```
anical/
  app.py            Adw.Application and start-up
  domain/           typed models and pure rules (validation, search, calendar ranges, schedule projection); no GTK
  db/               connection, migrations/NNN_*.sql, repositories, MAL list import (all SQL lives here)
  net/              tiny stdlib HTTP client behind a protocol (faked in tests)
  providers/        MyAnimeList and AniList API clients with response validation
  auth/             MAL OAuth (PKCE plain, loopback receiver) and keyring token storage
  sync/             worker-thread fetchers (MAL list, AniList schedules) and cover cache
  playback/         ani-cli picker (ANI_CLI_MENU) and mpv progress script, installed at runtime
  data/icons/       app icon (full colour and symbolic)
packaging/          PKGBUILD, desktop entry, AppStream metadata
  ui/               window, calendar and library views, show page, dialogs, preferences, refresh controller
tests/              pytest
```

## License

MIT. See `LICENSE`. AniCal runs ani-cli as a separate program and includes none of its code.

Keyboard shortcuts (the full list is under <kbd>Ctrl</kbd>+<kbd>?</kbd>):

| Shortcut | Action |
|---|---|
| <kbd>Ctrl</kbd>+<kbd>N</kbd> | Add a show |
| <kbd>Ctrl</kbd>+<kbd>F</kbd> | Search (or just start typing in the library) |
| <kbd>Ctrl</kbd>+<kbd>R</kbd> | Refresh |
| <kbd>Ctrl</kbd>+<kbd>1</kbd> / <kbd>2</kbd> | Switch to Calendar / Library |
| <kbd>Ctrl</kbd>+<kbd>Page Up</kbd> / <kbd>Page Down</kbd> | Move the calendar back / forward |
| <kbd>Ctrl</kbd>+<kbd>Home</kbd> | Jump to today |
| <kbd>Ctrl</kbd>+<kbd>,</kbd> | Preferences |
| <kbd>Ctrl</kbd>+<kbd>Q</kbd> | Quit |

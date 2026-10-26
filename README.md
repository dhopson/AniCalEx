# AniCal

AniCal is a GNOME desktop app for tracking anime. It keeps a local library of shows and which episodes you've watched, and imports your MyAnimeList list. Later phases add an airing calendar and episode playback through [`ani-cli`](https://github.com/pystardust/ani-cli).

**Status: Phase 2, MyAnimeList import.**

- **Local library.** You can add, edit and delete shows by hand. Episodes can be marked watched one at a time or all at once, with undo. The library can be searched, filtered and sorted. All of this works offline, with no account.
- **MyAnimeList import.** You can log in through your browser (OAuth with PKCE), or import a public list by username. Imports run when the app starts if the last successful sync was more than 24 hours ago, then once a day while the app runs, and on demand with <kbd>Ctrl</kbd>+<kbd>R</kbd>.
  - Imports bring in cover art and MAL list statuses.
  - Your edits to imported shows are kept as overrides, which you can revert.
  - MAL progress fills in watched episodes, but it never un-watches anything and never outranks what you marked locally.
  - AniCal never writes anything back to MAL.

### Connecting MyAnimeList

1. Register a client ID at <https://myanimelist.net/apiconfig>:
   - App type: *other*
   - Redirect URL: exactly `http://localhost:8765/callback`
2. In AniCal, open **Preferences** (<kbd>Ctrl</kbd>+<kbd>,</kbd>) and paste the client ID into **Client ID**.
3. Either click **Log In**, or enter a public list's username.

The login token is stored in your desktop keyring (Secret Service), never in a file. Requests to MyAnimeList carry only the token or the client ID, plus your username when you import a public list.

## Requirements

- Python ≥ 3.12
- GTK 4, libadwaita ≥ 1.5, and PyGObject, all from your distribution:
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
| Cover images (re-downloadable) | `$XDG_CACHE_HOME/anical/covers/` |
| MyAnimeList login token | desktop keyring, item "AniCal MyAnimeList login" |

Each schema migration runs in its own transaction. If the database already holds data, it is copied before any upgrade. If the file is unreadable, or was written by a newer version of AniCal, the app shows an error and leaves the file untouched.

To back up your library by hand while the app is closed, copy `anical.db`. With the app running, use `sqlite3 ~/.local/share/anical/anical.db ".backup anical-backup.db"`.

## Layout

```
anical/
  app.py            Adw.Application and start-up
  domain/           typed models and pure rules (validation, search, filter, sort, refresh timing); no GTK
  db/               connection, migrations/NNN_*.sql, repositories, MAL list import (all SQL lives here)
  net/              tiny stdlib HTTP client behind a protocol (faked in tests)
  providers/        MyAnimeList API client and response validation
  auth/             MAL OAuth (PKCE plain, loopback receiver) and keyring token storage
  sync/             worker-thread sync service and cover cache
  ui/               window, pages, editor, preferences, sync controller
tests/              pytest
```

Keyboard shortcuts: <kbd>Ctrl</kbd>+<kbd>N</kbd> adds a show, <kbd>Ctrl</kbd>+<kbd>F</kbd> searches (you can also just start typing in the library), <kbd>Ctrl</kbd>+<kbd>R</kbd> syncs, <kbd>Ctrl</kbd>+<kbd>,</kbd> opens Preferences, and <kbd>Ctrl</kbd>+<kbd>Q</kbd> quits.

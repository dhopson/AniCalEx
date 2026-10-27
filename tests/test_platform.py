import sys
import time
from pathlib import Path

import pytest

from anical.auth.mal_oauth import Token, TokenStoreError
from anical.platform import System
from anical.platform.paths import app_paths
from anical.platform.secrets import DpapiTokenStore, KeychainTokenStore
from anical.platform.timezone import resolve

ZONES = frozenset({"UTC", "Asia/Tokyo", "Europe/Berlin", "America/New_York"})
TOKEN = Token("a" * 900, "r" * 900, 1_800_000_000)


# Paths


def test_override_puts_everything_under_one_folder():
    paths = app_paths(System.WINDOWS, {"ANICAL_DATA_DIR": "/tmp/x"}, Path("/home/u"))
    assert (paths.data, paths.cache, paths.runtime) == (Path("/tmp/x"), Path("/tmp/x/cache"), Path("/tmp/x/run"))


def test_windows_paths():
    env = {"LOCALAPPDATA": r"C:\Users\First Last\AppData\Local", "TEMP": r"C:\Temp"}
    paths = app_paths(System.WINDOWS, env, Path("/unused"))
    assert paths.data == Path(env["LOCALAPPDATA"]) / "AniCal"
    assert paths.cache == paths.data / "cache"
    assert paths.runtime == Path(r"C:\Temp") / "AniCal"


def test_macos_paths():
    paths = app_paths(System.MACOS, {"TMPDIR": "/var/folders/t/"}, Path("/Users/u"))
    assert paths.data == Path("/Users/u/Library/Application Support/AniCal")
    assert paths.cache == Path("/Users/u/Library/Caches/AniCal")
    assert paths.runtime == Path("/var/folders/t/AniCal")


def test_linux_paths_are_unchanged():
    gi = pytest.importorskip("gi")
    from gi.repository import GLib

    assert gi
    paths = app_paths(System.LINUX, {}, Path("/unused"))
    assert paths.data == Path(GLib.get_user_data_dir()) / "anical"
    assert paths.runtime == Path(GLib.get_user_runtime_dir()) / "anical"


# Time zone


@pytest.mark.parametrize(
    "candidates, expected",
    [
        (["Europe/Berlin"], "Europe/Berlin"),  # Linux: already IANA
        (["/usr/share/zoneinfo/Asia/Tokyo"], "Asia/Tokyo"),  # macOS: a path
        (["/var/db/timezone/zoneinfo/Asia/Tokyo"], "Asia/Tokyo"),  # macOS /etc/localtime target
        (["Tokyo Standard Time"], "Asia/Tokyo"),  # Windows name
        ([":America/New_York"], "America/New_York"),  # $TZ syntax
        ([None, "", "Nowhere/Special", "UTC"], "UTC"),
        (["Nowhere/Special"], "UTC"),
    ],
)
def test_resolve_system_timezone(candidates, expected):
    assert resolve(candidates, ZONES, {"Tokyo Standard Time": "Asia/Tokyo"}) == expected


# Token storage


def fake_dpapi():
    key = 0x5A
    return (lambda data: b"sealed:" + bytes(b ^ key for b in data), lambda data: bytes(b ^ key for b in data[7:]))


def test_dpapi_store_round_trip_and_clear(tmp_path):
    protect, unprotect = fake_dpapi()
    store = DpapiTokenStore(tmp_path / "AniCal" / "mal-login.bin", protect, unprotect)
    assert store.load() is None
    store.save(TOKEN)  # far longer than Credential Manager's 1,280 characters
    assert (tmp_path / "AniCal" / "mal-login.bin").read_bytes().startswith(b"sealed:")
    assert b"aaaa" not in (tmp_path / "AniCal" / "mal-login.bin").read_bytes()
    assert store.load() == TOKEN
    store.clear()
    store.clear()  # already gone: fine
    assert store.load() is None


def test_dpapi_store_treats_an_unreadable_file_as_logged_out(tmp_path):
    def refuse(_data):
        raise OSError("The data is invalid")  # what DPAPI says for another user's file

    protect, _ = fake_dpapi()
    path = tmp_path / "mal-login.bin"
    DpapiTokenStore(path, protect, refuse).save(TOKEN)
    assert DpapiTokenStore(path, protect, refuse).load() is None


class FakeKeyring:
    class PasswordDeleteError(Exception):
        pass

    def __init__(self):
        self.items = {}

    def get_password(self, service, account):
        return self.items.get((service, account))

    def set_password(self, service, account, secret):
        self.items[(service, account)] = secret

    def delete_password(self, service, account):
        if self.items.pop((service, account), None) is None:
            raise self.PasswordDeleteError("not found")


def test_keychain_store_round_trip_and_clear():
    store = KeychainTokenStore(FakeKeyring())
    assert store.load() is None
    store.save(TOKEN)
    assert store.load() == TOKEN
    store.clear()
    store.clear()
    assert store.load() is None


def test_keychain_errors_become_token_store_errors():
    class Broken(FakeKeyring):
        def get_password(self, service, account):
            raise RuntimeError("Keychain locked")

    with pytest.raises(TokenStoreError):
        KeychainTokenStore(Broken()).load()


# Finding ani-cli, mpv and Git Bash (D23)

from anical.platform.anicli import find_ani_cli, find_bash, find_player


def touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")
    return path


def nowhere(_name):
    return None


def test_windows_finds_scoops_ani_cli_script_and_mpv(tmp_path):
    script = touch(tmp_path / "scoop" / "apps" / "ani-cli" / "current" / "ani-cli")
    mpv = touch(tmp_path / "scoop" / "apps" / "mpv" / "current" / "mpv.exe")
    assert find_ani_cli(None, System.WINDOWS, tmp_path, nowhere) == str(script)
    assert find_player(None, System.WINDOWS, tmp_path, nowhere) == str(mpv)


def test_macos_falls_back_to_homebrew_when_path_is_bare(tmp_path, monkeypatch):
    import anical.platform.anicli as anicli

    brew = tmp_path / "opt" / "homebrew" / "bin"
    touch(brew / "ani-cli")
    touch(brew / "mpv")
    monkeypatch.setattr(anicli, "_HOMEBREW", (brew,))
    assert find_ani_cli(None, System.MACOS, tmp_path, nowhere) == str(brew / "ani-cli")
    assert find_player(None, System.MACOS, tmp_path, nowhere) == str(brew / "mpv")
    assert find_ani_cli(None, System.LINUX, tmp_path, nowhere) is None  # Linux only looks on PATH


def test_a_setting_wins_and_must_exist(tmp_path):
    custom = touch(tmp_path / "my tools" / "ani-cli")
    assert find_ani_cli(str(custom), System.WINDOWS, tmp_path, nowhere) == str(custom)
    assert find_ani_cli(str(tmp_path / "missing"), System.LINUX, tmp_path, lambda n: "/usr/bin/ani-cli") is None
    assert find_player("mpv.com", System.WINDOWS, tmp_path, lambda n: f"C:/bin/{n}") == "C:/bin/mpv.com"


def test_git_bash_search_order(tmp_path):
    program_files = tmp_path / "Program Files"
    default = touch(program_files / "Git" / "bin" / "bash.exe")
    env = {"ProgramFiles": str(program_files), "LOCALAPPDATA": str(tmp_path / "Local")}
    assert find_bash(None, tmp_path, env, list) == str(default)
    registered = touch(tmp_path / "D" / "Git" / "bin" / "bash.exe")
    assert find_bash(None, tmp_path, env, lambda: [tmp_path / "D" / "Git"]) == str(registered)
    assert find_bash(str(tmp_path / "nope.exe"), tmp_path, env, list) is None


# Notifications through a helper program (Windows route, D24)

FAKE_NOTIFY = """
import json, sys
print(json.dumps({"event": "ready"}), flush=True)
for line in sys.stdin:
    message = json.loads(line)
    # "Click" the notification's first button, then the notification itself.
    print(json.dumps({"event": "button", "key": message["key"], "action": "0"}), flush=True)
    print(json.dumps({"event": "clicked", "key": message["key"]}), flush=True)
"""


@pytest.mark.skipif(sys.platform == "win32", reason="the fake helper is a script with a shebang")
def test_helper_sender_routes_clicks_to_app_actions(tmp_path):
    gi = pytest.importorskip("gi")
    from gi.repository import GLib

    from anical.platform.notify import Button, HelperNotifierSender, Notice

    assert gi
    program = tmp_path / "anical-notify"
    program.write_text(f"#!{sys.executable}\n{FAKE_NOTIFY}")
    program.chmod(0o755)

    class FakeApp:
        def __init__(self):
            self.activated = []

        def activate_action(self, name, target):
            self.activated.append((name, target.get_int64() if target is not None else None))

    app = FakeApp()
    sender = HelperNotifierSender(app, program)
    assert sender.ready.wait(10)
    sender(
        "available-7", Notice("Show: Episode 3", "body", "app.show-anime", 5, (Button("Play", "app.play-episode", 7),))
    )
    deadline = time.monotonic() + 10
    while len(app.activated) < 2 and time.monotonic() < deadline:
        GLib.MainContext.default().iteration(False)
    assert app.activated == [("play-episode", 7), ("show-anime", 5)]


# Install scripts in a terminal (setup wizard)

from anical.platform.terminal import TerminalError, install_script, terminal_command


def test_install_scripts_are_copied_executable(tmp_path):
    for system, name in (
        (System.LINUX, "install-linux.sh"),
        (System.MACOS, "install-macos.command"),
        (System.WINDOWS, "install-windows.ps1"),
    ):
        script = install_script(tmp_path / "setup dir", system)
        assert script.name == name and script.stat().st_size > 0
        if sys.platform != "win32":
            assert script.stat().st_mode & 0o111


def test_terminal_commands_per_system(tmp_path):
    script = install_script(tmp_path, System.LINUX)
    found = {"gnome-terminal", "xterm"}
    assert terminal_command(script, ["mpv"], System.LINUX, lambda n: n if n in found else None) == [
        "gnome-terminal",
        "--",
        "bash",
        str(script),
        "mpv",
    ]
    with pytest.raises(TerminalError):
        terminal_command(script, ["mpv"], System.LINUX, lambda n: None)

    ps1 = install_script(tmp_path, System.WINDOWS)
    command = terminal_command(ps1, ["git", "ani-cli"], System.WINDOWS)
    assert command[0] == "powershell.exe" and command[-3:] == [str(ps1), "git", "ani-cli"]

    mac = install_script(tmp_path / "Application Support", System.MACOS)
    command = terminal_command(mac, ["ani-cli", "mpv"], System.MACOS)
    assert command[:3] == ["open", "-a", "Terminal"]
    launcher = Path(command[3])
    assert launcher.name.endswith(".command") and str(mac) in launcher.read_text()  # quoted path with a space

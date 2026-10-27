# PyInstaller spec for the Windows and macOS builds: AniCal plus its two
# playback helpers, anical-pick and anical-mpv, sharing one runtime folder
# (D23). Run from the repository root:
#   pyinstaller --noconfirm --distpath dist --workpath build packaging/pyinstaller/anical.spec
# On macOS, set ANICAL_ICNS to the .icns file made by packaging/macos/make-icns.sh.
import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_data_files

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
sys.path.insert(0, ROOT)
from anical import APP_ID, __version__  # noqa: E402

MACOS = sys.platform == "darwin"
WINDOWS = sys.platform == "win32"

datas = collect_data_files("anical") + collect_data_files("certifi")
binaries = []
# Found at run time, not by import analysis: zoneinfo loads tzdata lazily,
# keyring finds its backends through entry points.
hiddenimports = ["certifi", "tzdata"]
datas += collect_data_files("tzdata")
packages = []
if MACOS:
    packages += ["keyring", "desktop_notifier", "rubicon"]
if WINDOWS:
    packages += ["tzlocal"]
for package in packages:
    d, b, h = collect_all(package)
    datas, binaries, hiddenimports = datas + d, binaries + b, hiddenimports + h

app = Analysis(
    [os.path.join(ROOT, "anical", "__main__.py")],
    pathex=[ROOT],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
    hooksconfig={
        "gi": {
            "module-versions": {"Gtk": "4.0", "Gdk": "4.0"},
            "icons": ["Adwaita", "hicolor"],
            "themes": [],
            "languages": ["en_US"],
        },
    },
    excludes=["tkinter"],
)
helper_dir = os.path.join(ROOT, "anical", "playback", "data")
pick = Analysis([os.path.join(helper_dir, "anical-pick.py")], excludes=["tkinter"])
shim = Analysis([os.path.join(helper_dir, "anical-mpv.py")], excludes=["tkinter"])

icon = os.environ.get("ANICAL_ICNS") if MACOS else os.environ.get("ANICAL_ICO")
app_exe = EXE(PYZ(app.pure), app.scripts, [], exclude_binaries=True, name="AniCal", console=False, icon=icon)
# Console programs: ani-cli talks to them over pipes. AniCal starts ani-cli
# without a console window, so none appears (D23).
pick_exe = EXE(PYZ(pick.pure), pick.scripts, [], exclude_binaries=True, name="anical-pick", console=True)
shim_exe = EXE(PYZ(shim.pure), shim.scripts, [], exclude_binaries=True, name="anical-mpv", console=True)
coll = COLLECT(
    app_exe, pick_exe, shim_exe,
    app.binaries, app.datas, pick.binaries, pick.datas, shim.binaries, shim.datas,
    name="AniCal",
)

if MACOS:
    BUNDLE(
        coll,
        name="AniCal.app",
        icon=icon,
        bundle_identifier=APP_ID,
        version=__version__,
        info_plist={
            "CFBundleName": "AniCal",
            "CFBundleDisplayName": "AniCal",
            "CFBundleShortVersionString": __version__,
            "CFBundleVersion": __version__,
            "LSMinimumSystemVersion": "12.0",
            "NSHighResolutionCapable": True,
            "NSHumanReadableCopyright": "Copyright (c) 2026 AniCal contributors. MIT License.",
        },
    )

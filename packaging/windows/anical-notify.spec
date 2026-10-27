# PyInstaller spec for anical-notify, AniCal's Windows toast helper (D24).
# Built with python.org Python, not MSYS2's: desktop-notifier's WinRT modules
# only load there. Run from the repository root:
#   pyinstaller --noconfirm --distpath dist-notify --workpath build-notify packaging/windows/anical-notify.spec
# then copy dist-notify/anical-notify into the AniCal folder.
import os

from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
datas, binaries, hiddenimports = [], [], []
for package in ("desktop_notifier", "winrt"):
    d, b, h = collect_all(package)
    datas, binaries, hiddenimports = datas + d, binaries + b, hiddenimports + h

a = Analysis(
    [os.path.join(ROOT, "anical", "platform", "data", "anical-notify.py")],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
    excludes=["tkinter"],
)
# A console program: AniCal starts it with CREATE_NO_WINDOW and talks over pipes.
exe = EXE(PYZ(a.pure), a.scripts, [], exclude_binaries=True, name="anical-notify", console=True)
COLLECT(exe, a.binaries, a.datas, name="anical-notify")

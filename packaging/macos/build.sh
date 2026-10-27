#!/bin/bash
# Builds AniCal for macOS on this Mac: AniCal.app (signed ad hoc) and a .dmg,
# then starts a copy of the app in self-test mode. The same steps as the macos
# build job in .github/workflows/macos.yml. Run from anywhere:
#
#   packaging/macos/build.sh [--arch arm64|x86_64] [--skip-selftest] [--screenshots]
#
# --arch         defaults to this Mac's. An Apple Silicon Mac can also build for
#                Intel, through a second, Intel Homebrew in /usr/local run under
#                Rosetta (untested). An Intel Mac can only build for Intel.
# --screenshots  also opens each setup wizard step and Preferences, and saves a
#                screenshot and report of each.
#
# Needs Homebrew; installs the GTK stack from it if missing. Writes:
#   dist/macos-ARCH/AniCal.app
#   out/AniCal-VERSION-macos-ARCH.dmg
#   out/macos-ARCH-selftest/          (report.json, report.log, screenshot.png)
set -euo pipefail

arch="$(uname -m)"
selftest=1
screenshots=0
while [ $# -gt 0 ]; do
    case "$1" in
        --arch) arch="${2:?--arch needs arm64 or x86_64}"; shift 2 ;;
        --skip-selftest) selftest=0; shift ;;
        --screenshots) screenshots=1; shift ;;
        -h|--help) awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$0"; exit 0 ;;
        *) echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
    esac
done

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31mError: %s\033[0m\n' "$*" >&2; exit 1; }

case "$arch" in
    arm64) brew=/opt/homebrew/bin/brew ;;
    x86_64) brew=/usr/local/bin/brew ;;
    *) die "--arch must be arm64 or x86_64, not $arch" ;;
esac

# Under Rosetta, uname reports x86_64, so the second run doesn't come back here.
if [ "$arch" != "$(uname -m)" ]; then
    [ "$arch" = x86_64 ] || die "An Intel Mac can't build for Apple Silicon."
    [ -x "$brew" ] || die "Building for Intel needs Intel Homebrew in /usr/local. Install it with:
  arch -x86_64 /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
    say "Building for Intel under Rosetta"
    again="--arch x86_64"
    [ "$selftest" = 1 ] || again="$again --skip-selftest"
    [ "$screenshots" = 0 ] || again="$again --screenshots"
    exec arch -x86_64 /bin/bash "$0" $again
fi

root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"
work="build/macos-$arch"
dist="dist/macos-$arch"
version="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' anical/__init__.py)"
[ -n "$version" ] || die "Couldn't read __version__ from anical/__init__.py"

[ -x "$brew" ] || die "Homebrew isn't installed at $brew. Install it from https://brew.sh"
eval "$("$brew" shellenv)"  # its rsvg-convert and python first on PATH

say "GTK stack from Homebrew"
formulae="gtk4 libadwaita pygobject3 adwaita-icon-theme librsvg"
missing=""
for formula in $formulae; do
    brew list --versions "$formula" >/dev/null || missing="$missing $formula"
done
if [ -n "$missing" ]; then
    brew install $missing
else
    echo "Already installed:$(printf ' %s' $formulae)"
fi

say "Python environment ($work/venv)"
python=""
for candidate in "$(brew --prefix)"/opt/python@3.*/bin/python3.[0-9]*; do
    if [ -x "$candidate" ] && "$candidate" -c "import gi" 2>/dev/null; then python="$candidate"; fi
done
[ -n "$python" ] || die "No Homebrew Python can import gi. Try: brew reinstall pygobject3"
if [ ! -x "$work/venv/bin/python" ] || ! "$work/venv/bin/python" -c "import gi" 2>/dev/null; then
    rm -rf "$work/venv"
    "$python" -m venv --system-site-packages "$work/venv"
fi
# Installing "." builds in build/lib; stale files there would end up in the venv.
rm -rf build/lib build/bdist.* ./*.egg-info
"$work/venv/bin/pip" install --quiet --upgrade pip pyinstaller tzdata .

say "Icon"
packaging/macos/make-icns.sh "$work/AniCal.icns"

say "Freezing AniCal $version for $arch"
ANICAL_ICNS="$root/$work/AniCal.icns" "$work/venv/bin/pyinstaller" --noconfirm --log-level WARN \
    --distpath "$dist" --workpath "$work/pyinstaller" packaging/pyinstaller/anical.spec
codesign --force --deep --sign - "$dist/AniCal.app"
codesign --verify --verbose "$dist/AniCal.app"

say "Disk image"
dmg="out/AniCal-$version-macos-$arch.dmg"
mkdir -p out
rm -rf "$work/dmg" "$dmg"
mkdir -p "$work/dmg"
ditto "$dist/AniCal.app" "$work/dmg/AniCal.app"
ln -s /Applications "$work/dmg/Applications"
hdiutil create -volname AniCal -srcfolder "$work/dmg" -ov -format UDZO "$dmg"

if [ "$selftest" = 1 ]; then
    # A moved copy, as after installing. Homebrew stays in place here (CI hides it),
    # so a missing library could go unnoticed; the CI self-test catches that.
    say "Self-test (a window opens for a few seconds)"
    rm -rf "$work/selftest"
    mkdir -p "$work/selftest"
    ditto "$dist/AniCal.app" "$work/selftest/AniCal.app"
    app="$work/selftest/AniCal.app/Contents/MacOS/AniCal"
    "$work/venv/bin/python" packaging/ci/run_selftest.py "out/macos-$arch-selftest" -- "$app"
    if [ "$screenshots" = 1 ]; then
        say "Setup wizard and Preferences screenshots"
        for step in 1 2 3 4; do
            ANICAL_SELFTEST_WIZARD=$step "$work/venv/bin/python" packaging/ci/run_selftest.py \
                "out/macos-$arch-wizard-$step" -- "$app" || echo "Step $step's self-test failed; carrying on."
        done
        ANICAL_SELFTEST_DIALOG=preferences "$work/venv/bin/python" packaging/ci/run_selftest.py \
            "out/macos-$arch-preferences" -- "$app" || echo "The Preferences self-test failed."
    fi
fi

say "Done"
echo "App:        $dist/AniCal.app"
echo "Disk image: $dmg"
[ "$selftest" = 1 ] && echo "Self-test:  out/macos-$arch-selftest/"
exit 0

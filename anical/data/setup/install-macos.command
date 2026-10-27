#!/bin/bash
# AniCal setup: installs ani-cli and mpv on macOS with Homebrew.
#
# AniCal's setup wizard opens this in Terminal. It says what it will do and
# asks before each step. ani-cli comes from its own Homebrew tap, as its
# install instructions describe. Usage: install-macos.command [ani-cli] [mpv]
set -u

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ask() {
    local answer
    read -r -p "$1 [Y/n] " answer
    [[ -z $answer || $answer =~ ^[Yy] ]]
}
finish() {
    echo
    read -r -p "Press Enter, then close this window. AniCal checks again by itself. " _
    exit "${1:-0}"
}

wanted=("$@")
[ ${#wanted[@]} -eq 0 ] && wanted=(ani-cli mpv)

say "AniCal setup: ${wanted[*]}"

# Homebrew lives in /opt/homebrew (Apple Silicon) or /usr/local (Intel).
for prefix in /opt/homebrew /usr/local; do
    [ -x "$prefix/bin/brew" ] && eval "$("$prefix/bin/brew" shellenv)" && break
done
if ! command -v brew >/dev/null; then
    say "Homebrew isn't installed. It's the usual way to install command-line tools on a Mac."
    echo "Its official installer comes from https://brew.sh and asks for your password."
    if ! ask "Install Homebrew now?"; then
        echo "Install it from https://brew.sh, then use Install in AniCal again."
        finish 1
    fi
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" || finish 1
    for prefix in /opt/homebrew /usr/local; do
        [ -x "$prefix/bin/brew" ] && eval "$("$prefix/bin/brew" shellenv)" && break
    done
fi

failed=0
for program in "${wanted[@]}"; do
    if found=$(command -v "$program"); then
        say "$program is already installed: $found"
        continue
    fi
    say "Installing $program"
    case $program in
        mpv)
            echo "Will run: brew install mpv"
            if ask "Continue?"; then brew install mpv || failed=1; else failed=1; fi
            ;;
        ani-cli)
            echo "ani-cli isn't in Homebrew's main collection. Will run:"
            echo "  brew tap pystardust/ani-cli https://github.com/pystardust/ani-cli.git"
            echo "  brew trust pystardust/ani-cli"
            echo "  brew install ani-cli"
            if ask "Continue?"; then
                brew tap pystardust/ani-cli https://github.com/pystardust/ani-cli.git &&
                    { brew trust pystardust/ani-cli 2>/dev/null || true; } &&
                    brew install ani-cli || failed=1
            else
                failed=1
            fi
            ;;
        *)
            echo "Don't know how to install $program."
            failed=1
            ;;
    esac
done

if [ $failed -eq 0 ]; then
    say "Done."
else
    say "Something wasn't installed. See the messages above, or ani-cli's instructions:"
    echo "https://github.com/pystardust/ani-cli"
fi
finish $failed

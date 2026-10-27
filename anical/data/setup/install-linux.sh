#!/usr/bin/env bash
# AniCal setup: installs ani-cli and mpv on Linux.
#
# AniCal's setup wizard opens this in a terminal window. It says what it will
# do and asks before each step; installing system packages asks for your
# password through sudo. Usage: install-linux.sh [ani-cli] [mpv]
set -u

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ask() {
    local answer
    read -r -p "$1 [Y/n] " answer
    [[ -z $answer || $answer =~ ^[Yy] ]]
}
finish() {
    echo
    read -r -p "Press Enter to close this window. AniCal checks again by itself. " _
    exit "${1:-0}"
}

wanted=("$@")
[ ${#wanted[@]} -eq 0 ] && wanted=(ani-cli mpv)

say "AniCal setup: ${wanted[*]}"

# Which package manager this system uses, and whether it has a package.
if command -v pacman >/dev/null; then
    install=(sudo pacman -S --needed)
    has_package() { pacman -Si "$1" >/dev/null 2>&1; }
elif command -v dnf >/dev/null; then
    install=(sudo dnf install)
    has_package() { dnf info "$1" >/dev/null 2>&1; }
elif command -v apt-get >/dev/null; then
    install=(sudo apt-get install)
    has_package() { apt-cache show "$1" >/dev/null 2>&1; }
elif command -v zypper >/dev/null; then
    install=(sudo zypper install)
    has_package() { zypper info "$1" 2>/dev/null | grep -q '^Name'; }
else
    say "No supported package manager found (pacman, dnf, apt or zypper)."
    echo "Install ani-cli and mpv by hand: https://github.com/pystardust/ani-cli"
    finish 1
fi

from_package() {
    echo "Will run: ${install[*]} $*"
    ask "Install $* with your package manager?" || return 1
    "${install[@]}" "$@"
}

# ani-cli's documented install from source: copy its script to /usr/local/bin.
ani_cli_from_source() {
    say "Your distribution doesn't package ani-cli, so it comes from its GitHub repository."
    echo "This clones https://github.com/pystardust/ani-cli and copies the ani-cli script to /usr/local/bin."
    ask "Continue?" || return 1
    command -v git >/dev/null || from_package git || return 1
    command -v curl >/dev/null || from_package curl || return 1
    local work
    work=$(mktemp -d) || return 1
    git clone --depth 1 https://github.com/pystardust/ani-cli.git "$work/ani-cli" &&
        sudo install -m 755 "$work/ani-cli/ani-cli" /usr/local/bin/ani-cli
    local status=$?
    rm -rf "$work"
    return $status
}

failed=0
for program in "${wanted[@]}"; do
    if found=$(command -v "$program"); then
        say "$program is already installed: $found"
        continue
    fi
    say "Installing $program"
    case $program in
        mpv)
            from_package mpv || failed=1
            ;;
        ani-cli)
            if has_package ani-cli; then
                from_package ani-cli || failed=1
            else
                ani_cli_from_source || failed=1
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

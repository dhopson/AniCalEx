#!/bin/bash
# Makes the draft GitHub Release for a v* tag from every system's downloads, plus
# SHA256SUMS.txt. release.yml runs it once linux, macos and windows have all
# passed for the tag:
# - the tag must match anical.__version__ (release.yml also checks this first,
#   before building);
# - no release yet: it's created as a draft, to check and publish by hand;
# - a draft already there (a re-run): its files are replaced;
# - a release that is already published is never changed.
#
# Usage, from the repository root:
#   packaging/ci/release_draft.sh --check TAG     only check the tag
#   packaging/ci/release_draft.sh TAG FILE...
# Needs GH_TOKEN (contents: write) and GITHUB_REPOSITORY, except with --check.
set -euo pipefail

check_only=false
if [ "${1:-}" = --check ]; then
    check_only=true
    shift
fi
tag="${1:?usage: release_draft.sh [--check] TAG [FILE...]}"
shift

version="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' anical/__init__.py)"
if [ "$tag" != "v$version" ]; then
    echo "Tag $tag doesn't match the app's version $version (anical/__init__.py)." >&2
    exit 1
fi
if $check_only; then
    echo "Tag $tag matches the app's version."
    exit 0
fi

[ $# -gt 0 ] || { echo "No files to release." >&2; exit 1; }
repo="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is not set}"

# Checksums of the downloads, by file name, as users will see them.
sums="$(mktemp -d)/SHA256SUMS.txt"
for file in "$@"; do
    (cd "$(dirname "$file")" && sha256sum "$(basename "$file")")
done | sort -k2 > "$sums"
cat "$sums"

# gh finds draft releases by tag too. "missing" when there is no release yet.
draft="$(gh release view "$tag" --repo "$repo" --json isDraft --jq .isDraft 2>/dev/null || echo missing)"
case "$draft" in
    false)
        echo "Release $tag is already published; leaving it unchanged."
        exit 0
        ;;
    missing)
        gh release create "$tag" --repo "$repo" --draft --verify-tag \
            --title "AniCal $version" \
            --notes "Downloads for Linux (Arch package), macOS (Apple Silicon and Intel) and Windows x64, with their checksums in SHA256SUMS.txt. The macOS and Windows builds are unsigned: see the README's install sections." \
            "$@" "$sums"
        echo "Created draft release $tag."
        ;;
    true)
        gh release upload "$tag" "$@" "$sums" --repo "$repo" --clobber
        echo "Replaced the files on draft release $tag."
        ;;
esac

#!/bin/bash
# Adds one platform's downloads to the GitHub Release for a v* tag. Each
# platform's workflow (linux.yml, macos.yml, windows.yml) calls this when it
# finishes, so their downloads meet in one release:
# - the tag must match anical.__version__;
# - the first workflow to finish creates the release, as a draft to check and
#   publish by hand; the others add to it;
# - a release that is already published is never changed.
#
# Usage, from the repository root: packaging/ci/release_upload.sh TAG FILE...
# Needs GH_TOKEN (contents: write) and GITHUB_REPOSITORY.
set -euo pipefail

tag="${1:?usage: release_upload.sh TAG FILE...}"
shift
[ $# -gt 0 ] || { echo "No files to upload." >&2; exit 1; }
repo="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is not set}"

version="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' anical/__init__.py)"
if [ "$tag" != "v$version" ]; then
    echo "Tag $tag doesn't match the app's version $version (anical/__init__.py)." >&2
    exit 1
fi

# gh finds draft releases by tag too. "missing" when there is no release yet.
draft="$(gh release view "$tag" --repo "$repo" --json isDraft --jq .isDraft 2>/dev/null || echo missing)"
case "$draft" in
    false)
        echo "Release $tag is already published; leaving it unchanged."
        exit 0
        ;;
    missing)
        # The platforms finish minutes apart, so two creating it at once is unlikely;
        # if another one just did, creating fails and the upload below adds to it.
        gh release create "$tag" --repo "$repo" --draft --verify-tag \
            --title "AniCal $version" \
            --notes "Downloads for Linux (Arch package), macOS (Apple Silicon and Intel) and Windows x64. The macOS and Windows builds are unsigned: see the README's install sections." ||
            echo "Couldn't create the release; adding to the one another workflow made."
        ;;
esac

printf 'Uploading to %s:\n' "$tag"
printf '  %s\n' "$@"
gh release upload "$tag" "$@" --repo "$repo" --clobber

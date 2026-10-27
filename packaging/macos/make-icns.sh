#!/bin/sh
# Builds AniCal.icns from the app's SVG icon. Needs rsvg-convert (librsvg) and
# iconutil (macOS). Usage: packaging/macos/make-icns.sh OUT.icns
set -eu
out="$1"
svg="$(dirname "$0")/../../anical/data/icons/hicolor/scalable/apps/io.github.dhopson.AniCal.svg"
set_dir="$(mktemp -d)/AniCal.iconset"
mkdir -p "$set_dir"
for size in 16 32 128 256 512; do
    rsvg-convert -w "$size" -h "$size" "$svg" -o "$set_dir/icon_${size}x${size}.png"
    double=$((size * 2))
    rsvg-convert -w "$double" -h "$double" "$svg" -o "$set_dir/icon_${size}x${size}@2x.png"
done
iconutil -c icns "$set_dir" -o "$out"

#!/bin/bash
# Build Prism.app from this repo. Usage: ./macos/build.sh [output path]
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
OUT="${1:-$HOME/Desktop/Prism.app}"

rm -rf "$OUT"
osacompile -o "$OUT" "$HERE/droplet.applescript"
cp "$HERE/icon.icns" "$OUT/Contents/Resources/droplet.icns"
cp "$ROOT/engine/"*.py "$OUT/Contents/Resources/"
rm -rf "$OUT/Contents/Resources/data"
cp -R "$ROOT/engine/data" "$OUT/Contents/Resources/data"

PL="$OUT/Contents/Info.plist"
for kv in "CFBundleName Prism" "CFBundleDisplayName Prism" \
          "CFBundleIdentifier uk.co.prism.optimiser"; do
  k="${kv%% *}"; v="${kv#* }"
  /usr/libexec/PlistBuddy -c "Set :$k $v" "$PL" 2>/dev/null || \
  /usr/libexec/PlistBuddy -c "Add :$k string $v" "$PL"
done

# Editing a bundle invalidates its signature; FinderInfo blocks re-signing.
xattr -cr "$OUT" 2>/dev/null || true
xattr -d com.apple.FinderInfo "$OUT" 2>/dev/null || true
codesign --force --deep --sign - "$OUT"
codesign --verify --deep "$OUT" && echo "Built $OUT"

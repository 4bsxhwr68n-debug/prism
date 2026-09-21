#!/bin/bash
# Build Prism.app from this repo. Usage: ./macos/build.sh [output path]
#
# Signs with a Developer ID if one is available, ad-hoc if not. Ad-hoc is only
# good enough for a machine that built the app itself: since macOS 15 Gatekeeper
# blocks an ad-hoc signed app that arrived over the internet, and there is no
# longer a right-click bypass. Set PRISM_SIGN_IDENTITY to force a specific
# identity, or leave it unset to use the first Developer ID Application cert in
# the keychain.
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
# NSAppleEventsUsageDescription is what macOS shows when the app first asks to
# talk to the Finder. Without it the prompt never appears and the reveal fails.
for kv in "CFBundleName Prism" "CFBundleDisplayName Prism" \
          "CFBundleIdentifier uk.co.prism.optimiser" \
          "NSAppleEventsUsageDescription Prism reveals your converted files in the Finder."; do
  k="${kv%% *}"; v="${kv#* }"
  /usr/libexec/PlistBuddy -c "Set :$k $v" "$PL" 2>/dev/null || \
  /usr/libexec/PlistBuddy -c "Add :$k string $v" "$PL"
done

# Editing a bundle invalidates its signature; FinderInfo blocks re-signing.
xattr -cr "$OUT" 2>/dev/null || true
xattr -d com.apple.FinderInfo "$OUT" 2>/dev/null || true

IDENTITY="${PRISM_SIGN_IDENTITY:-$(security find-identity -v -p codesigning 2>/dev/null \
  | grep 'Developer ID Application' | head -1 | sed 's/.*"\(.*\)"/\1/')}"

if [ -n "$IDENTITY" ]; then
  # --options runtime is the hardened runtime, which notarisation requires.
  # --timestamp gets a trusted timestamp, without which the signature expires
  # with the certificate instead of outliving it.
  codesign --force --timestamp --options runtime \
           --entitlements "$HERE/entitlements.plist" \
           --sign "$IDENTITY" "$OUT"
  codesign --verify --strict --verbose=2 "$OUT" 2>&1 | sed 's/^/  /'
  echo "Built $OUT"
  echo "  signed: $IDENTITY"
  echo "  next:   ./macos/notarise.sh \"$OUT\""
else
  codesign --force --deep --sign - "$OUT"
  codesign --verify --deep "$OUT"
  echo "Built $OUT"
  echo "  signed: ad-hoc (no Developer ID in the keychain)"
  echo "  WARNING: fine on this Mac, but macOS 15+ blocks this build after a"
  echo "           download and offers no right-click bypass. Ship a notarised"
  echo "           build: see macos/NOTARISING.md"
fi

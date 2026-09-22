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

# Checked BEFORE anything is built or removed. A stale engine embedded in a
# fresh bundle produces an app that runs, looks right and is yesterday's, and a
# gate that fires halfway through leaves the app half updated.
ENGINE="${PRISM_ENGINE_BIN:-$HERE/dist/prism-engine}"
if [ -x "$ENGINE" ]; then
  for f in "$ROOT/engine/"*.py; do
    if [ "$f" -nt "$ENGINE" ]; then
      echo "REFUSING: $ENGINE is older than $(basename "$f")."
      echo "          Rebuild it first: ./macos/build-engine.sh"
      exit 1
    fi
  done
fi

# Built and signed in a scratch directory, then moved into place. macOS stamps
# com.apple.provenance on an app it has launched, that attribute cannot be
# stripped, and codesign refuses a bundle carrying it: "resource fork, Finder
# information, or similar detritus not allowed". Rebuilding over a copy you
# have already run therefore produces an unsignable app, which is the normal
# case for a Desktop build.
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
BUILT="$STAGE/$(basename "$OUT")"

osacompile -o "$BUILT" "$HERE/droplet.applescript"
cp "$HERE/icon.icns" "$BUILT/Contents/Resources/droplet.icns"
cp "$ROOT/engine/"*.py "$BUILT/Contents/Resources/"
rm -rf "$BUILT/Contents/Resources/data"
cp -R "$ROOT/engine/data" "$BUILT/Contents/Resources/data"

# The bundled engine carries its own interpreter, so the app needs no Python on
# the Mac. Built separately by macos/build-engine.sh because it needs
# PyInstaller. Without it the droplet falls back to /usr/bin/python3, which only
# works on a Mac with Xcode or the Command Line Tools installed.
if [ -x "$ENGINE" ]; then
  cp "$ENGINE" "$BUILT/Contents/Resources/prism-engine"
  BUNDLED=yes
else
  BUNDLED=no
fi

PL="$BUILT/Contents/Info.plist"
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
xattr -cr "$BUILT" 2>/dev/null || true
xattr -d com.apple.FinderInfo "$BUILT" 2>/dev/null || true

IDENTITY="${PRISM_SIGN_IDENTITY:-$(security find-identity -v -p codesigning 2>/dev/null \
  | grep 'Developer ID Application' | head -1 | sed 's/.*"\(.*\)"/\1/')}"

if [ -n "$IDENTITY" ]; then
  # --options runtime is the hardened runtime, which notarisation requires.
  # --timestamp gets a trusted timestamp, without which the signature expires
  # with the certificate instead of outliving it.
  if [ "$BUNDLED" = yes ]; then
    codesign --force --timestamp --options runtime \
             --entitlements "$HERE/entitlements.plist" \
             --sign "$IDENTITY" "$BUILT/Contents/Resources/prism-engine"
  fi
  codesign --force --timestamp --options runtime \
           --entitlements "$HERE/entitlements.plist" \
           --sign "$IDENTITY" "$BUILT"
  codesign --verify --strict --verbose=2 "$BUILT" 2>&1 | sed 's/^/  /'
  # A valid signature is not a working app. The hardened runtime refuses to
  # dlopen a library whose Team ID differs from the process, which is exactly
  # what a PyInstaller bundle does to itself at startup. That failure passes
  # codesign --verify, passes spctl, notarises cleanly, and does not run.
  if [ "$BUNDLED" = yes ]; then
    env -i HOME="$HOME" PATH=/usr/bin:/bin \
        "$BUILT/Contents/Resources/prism-engine" --engine --list > /dev/null 2>&1 \
      || { echo "REFUSING TO SHIP: the signed engine does not run."; \
           env -i HOME="$HOME" PATH=/usr/bin:/bin \
               "$BUILT/Contents/Resources/prism-engine" --engine --list 2>&1 | head -5; \
           exit 1; }
  fi
  rm -rf "$OUT"
  ditto "$BUILT" "$OUT"
  echo "Built $OUT"
  echo "  engine: $([ "$BUNDLED" = yes ] && echo "bundled, no Python needed" || echo "NOT bundled, needs Xcode CLT on the user's Mac")"
  echo "  signed: $IDENTITY"
  echo "  next:   ./macos/notarise.sh \"$OUT\""
else
  codesign --force --deep --sign - "$BUILT"
  codesign --verify --deep "$BUILT"
  rm -rf "$OUT"
  ditto "$BUILT" "$OUT"
  echo "Built $OUT"
  echo "  engine: $([ "$BUNDLED" = yes ] && echo "bundled, no Python needed" || echo "NOT bundled, needs Xcode CLT on the user's Mac")"
  echo "  signed: ad-hoc (no Developer ID in the keychain)"
  echo "  WARNING: fine on this Mac, but macOS 15+ blocks this build after a"
  echo "           download and offers no right-click bypass. Ship a notarised"
  echo "           build: see macos/NOTARISING.md"
fi

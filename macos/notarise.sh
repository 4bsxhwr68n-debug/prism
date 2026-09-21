#!/bin/bash
# Notarise a signed Prism.app and staple the ticket to it.
# Usage: ./macos/notarise.sh [app path] [keychain profile]
#
# Apple's service checks the app for malware and issues a ticket. Stapling
# writes that ticket into the bundle, so the app opens on a Mac that has never
# seen it and is offline. Without this, macOS 15+ blocks the download outright.
#
# One-time credential setup is in macos/NOTARISING.md.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
APP="${1:-$HOME/Desktop/Prism.app}"
PROFILE="${2:-${PRISM_NOTARY_PROFILE:-prism-notary}}"

[ -d "$APP" ] || { echo "No app at $APP"; exit 1; }

# Refuse an ad-hoc signature early. Apple would reject it too, but only after a
# round trip, and the rejection reason is less obvious than this line.
if ! codesign -dvv "$APP" 2>&1 | grep -q 'Authority=Developer ID Application'; then
  echo "Not signed with a Developer ID, so notarisation would be rejected."
  echo "Check: security find-identity -v -p codesigning"
  exit 1
fi
if ! codesign -d --entitlements - "$APP" 2>/dev/null | grep -q 'apple-events'; then
  echo "Warning: entitlements missing. Revealing in the Finder will fail."
fi
codesign --verify --strict "$APP" || { echo "Signature does not verify."; exit 1; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
ZIP="$TMP/$(basename "${APP%.app}").zip"
# ditto, not zip: plain zip mangles bundle metadata and breaks the signature
ditto -c -k --sequesterRsrc --keepParent "$APP" "$ZIP"

echo "Submitting $(basename "$APP") to Apple. This usually takes a few minutes."
START=$(date +%s)
if ! xcrun notarytool submit "$ZIP" --keychain-profile "$PROFILE" --wait 2>&1 | tee "$TMP/out"; then
  echo "Submission failed. Credentials set up? See macos/NOTARISING.md"
  exit 1
fi
ELAPSED=$(( $(date +%s) - START ))

ID=$(grep -o '  id: [0-9a-f-]*' "$TMP/out" | head -1 | awk '{print $2}')
if ! grep -q 'status: Accepted' "$TMP/out"; then
  echo
  echo "Apple rejected it after ${ELAPSED}s. Their reasons:"
  xcrun notarytool log "$ID" --keychain-profile "$PROFILE" 2>&1 | sed 's/^/  /'
  exit 1
fi
echo "Accepted after ${ELAPSED}s (submission $ID)."

xcrun stapler staple "$APP"
xcrun stapler validate "$APP"
# The real test: what Gatekeeper says about it, as a user's Mac would.
spctl -a -vvv -t install "$APP" 2>&1 | sed 's/^/  /'
echo "Notarised and stapled: $APP"

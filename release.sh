#!/bin/bash
# Build the release artefacts. Usage: ./release.sh <version> [output dir]
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
VER="${1:?usage: ./release.sh v1.0.0 [outdir]}"
OUT="${2:-$HERE/dist}"
mkdir -p "$OUT"
TMP="$(mktemp -d)"

"$HERE/macos/build.sh" "$TMP/Prism.app"
# Notarise before zipping, so the ticket is stapled inside the bundle the user
# downloads. Skipped without a Developer ID, which still produces a working
# build for anyone who cloned the repo, just one macOS 15+ will block.
if codesign -dvv "$TMP/Prism.app" 2>&1 | grep -q 'Authority=Developer ID Application'; then
  "$HERE/macos/notarise.sh" "$TMP/Prism.app"
else
  echo "  not notarised: no Developer ID. See macos/NOTARISING.md"
fi
# ditto, not zip: plain zip mangles bundle metadata and breaks the signature
ditto -c -k --sequesterRsrc --keepParent "$TMP/Prism.app" "$OUT/Prism-$VER-macOS.zip"
"$HERE/windows/build.sh" "$OUT/Prism-$VER-Windows-script.zip"
"$HERE/linux/build.sh"   "$OUT/Prism-$VER-Linux-script.tar.gz"
cp "$HERE/docs/COLOUR.md" "$OUT/Prism-$VER-colour-and-painting.md" >/dev/null

rm -rf "$TMP"
echo "Built:"
ls -lh "$OUT" | awk 'NR>1{print "  "$9"  "$5}'

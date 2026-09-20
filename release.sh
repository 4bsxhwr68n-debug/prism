#!/bin/bash
# Build the release artefacts. Usage: ./release.sh <version> [output dir]
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
VER="${1:?usage: ./release.sh v1.0.0 [outdir]}"
OUT="${2:-$HERE/dist}"
mkdir -p "$OUT"
TMP="$(mktemp -d)"

"$HERE/macos/build.sh" "$TMP/Prism.app" >/dev/null
# ditto, not zip: plain zip mangles bundle metadata and breaks the signature
ditto -c -k --sequesterRsrc --keepParent "$TMP/Prism.app" "$OUT/Prism-$VER-macOS.zip"
"$HERE/windows/build.sh" "$OUT/Prism-$VER-Windows.zip" >/dev/null

rm -rf "$TMP"
echo "Built:"
ls -lh "$OUT" | awk 'NR>1{print "  "$9"  "$5}'

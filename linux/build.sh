#!/bin/bash
# Package the Linux build from this repo. Usage: ./linux/build.sh [output.tar.gz]
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
OUT="${1:-$HOME/Prism-Linux.tar.gz}"
TMP="$(mktemp -d)"; D="$TMP/prism"

mkdir -p "$D/engine"
cp "$ROOT/engine/"*.py "$D/engine/"
cp -R "$ROOT/engine/data" "$D/engine/data"
cp "$HERE/prism.sh" "$D/prism.sh"; chmod +x "$D/prism.sh"
cp "$ROOT/README.md" "$D/README.md"
cp "$ROOT/docs/COLOUR.md" "$D/COLOUR.md" 2>/dev/null || true

rm -f "$OUT"
tar -czf "$OUT" -C "$TMP" prism
rm -rf "$TMP"
echo "Built $OUT"

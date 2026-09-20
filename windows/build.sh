#!/bin/bash
# Package the Windows build from this repo. Usage: ./windows/build.sh [output zip]
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
OUT="${1:-$HOME/Desktop/Prism (Windows).zip}"
TMP="$(mktemp -d)"; D="$TMP/Prism (Windows)"

mkdir -p "$D/engine"
cp "$ROOT/engine/"*.py "$D/engine/"
cp -R "$ROOT/engine/data" "$D/engine/data"
cp "$HERE/Prism.bat" "$D/Prism.bat"
cp "$ROOT/README.md" "$D/README.txt"

rm -f "$OUT"
( cd "$TMP" && zip -qr "$OUT" "Prism (Windows)" -x '*.DS_Store' '*__pycache__*' )
rm -rf "$TMP"
echo "Built $OUT"

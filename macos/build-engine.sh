#!/bin/bash
# Build the self-contained engine binary that Prism.app carries.
# Usage: ./macos/build-engine.sh
#
# Why this exists: /usr/bin/python3 on macOS is the xcode-select shim, not
# Python. It is byte-identical to /usr/bin/git and /usr/bin/clang, and on a Mac
# without Xcode or the Command Line Tools it prompts to install developer tools
# rather than running anything. Bundling an interpreter is the only way the app
# works on a normal Mac.
#
# Output: macos/dist/prism-engine, which macos/build.sh embeds automatically.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
VENV="${PRISM_BUILD_VENV:-$HERE/.venv}"

[ -d "$VENV" ] || "${PRISM_PYTHON:-python3}" -m venv "$VENV"
"$VENV/bin/pip" -q install --upgrade pip pyinstaller

rm -rf "$HERE/dist" "$HERE/build" "$HERE/spec"
mkdir -p "$HERE/build"
"$VENV/bin/pyinstaller" --onefile --console --name prism-engine \
  --distpath "$HERE/dist" --workpath "$HERE/build" --specpath "$HERE/spec" \
  --paths "$ROOT/engine" \
  --add-data "$ROOT/engine/data:data" \
  --hidden-import gui --hidden-import optimise3mf --hidden-import mixer \
  "$ROOT/linux/prism_linux.py" > "$HERE/build/pyinstaller.log" 2>&1 \
  || { tail -20 "$HERE/build/pyinstaller.log"; exit 1; }

BIN="$HERE/dist/prism-engine"
# Prove it is self-contained before anyone ships it. A binary that quietly links
# the build machine's Homebrew Python works here and nowhere else.
BAD=$(otool -L "$BIN" | tail -n +2 | grep -v '/usr/lib/\|/System/' || true)
[ -z "$BAD" ] || { echo "Links outside the system:"; echo "$BAD"; exit 1; }
env -i HOME="$HOME" PATH=/usr/bin:/bin "$BIN" --engine --list > /dev/null \
  || { echo "Engine failed with the developer tools off the PATH."; exit 1; }

echo "Built $BIN"
echo "  $(lipo -info "$BIN" | sed 's/.*architecture: //')  $(du -h "$BIN" | cut -f1)"

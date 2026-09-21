#!/bin/bash
# Prism launcher for Linux.
#   ./prism.sh              open the app window
#   ./prism.sh a.3mf b.3mf  convert straight away, console journey
# Needs python3 and, for the file dialog, one of zenity, kdialog or yad.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
ENGINE="$HERE/engine"
[ -d "$ENGINE" ] || ENGINE="$HERE"

PY="$(command -v python3 || true)"
if [ -z "$PY" ]; then
  echo "python3 is required but was not found."
  echo "Install it with your package manager, for example:"
  echo "  sudo apt install python3        (Debian, Ubuntu)"
  echo "  sudo dnf install python3        (Fedora)"
  exit 1
fi

if [ "$#" -eq 0 ]; then
  if ! command -v zenity >/dev/null && ! command -v kdialog >/dev/null \
     && ! command -v yad >/dev/null; then
    echo "Note: no file dialog found. Install zenity, kdialog or yad to choose"
    echo "files in the window, or pass them on the command line instead."
  fi
  exec "$PY" "$ENGINE/gui.py"
fi
exec "$PY" "$ENGINE/optimise3mf.py" --interactive "$@"

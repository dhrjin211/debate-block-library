#!/bin/zsh
# Double-click to start the app. The first run sets everything up (about a minute).
cd -- "${0:A:h}" || exit 1
if [[ ! -x .venv/bin/python ]]; then
  PY=""
  for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v $candidate >/dev/null 2>&1 && $candidate -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
      PY=$candidate; break
    fi
  done
  if [[ -z $PY ]]; then
    print "The app needs Python. Opening the download page..."
    print "Install Python, then double-click Start again."
    open https://www.python.org/downloads/
    read '?Press Return to close.'
    exit 1
  fi
  print "Setting up the app for the first time (about a minute)..."
  if ! $PY -m venv .venv || ! .venv/bin/python -m pip install --quiet --disable-pip-version-check -r requirements.txt; then
    rm -rf .venv
    print "Setup didn't finish. Check your internet connection and try again."
    read '?Press Return to close.'
    exit 1
  fi
fi
.venv/bin/python start.py --open
if (( $? != 0 )); then
  read '?Press Return to close.'
fi

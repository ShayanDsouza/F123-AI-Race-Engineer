#!/usr/bin/env bash
# Sim vs Reality - start the dashboard (macOS / Linux)
set -e
cd "$(dirname "$0")"

PY=python3
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "Python 3 was not found. Install it from https://www.python.org/downloads/ (or 'brew install python') and run this again."
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "First run: creating a private Python environment and installing dependencies..."
  echo "This takes a few minutes and only happens once."
  "$PY" -m venv .venv
  .venv/bin/python -m pip install --upgrade pip >/dev/null
  .venv/bin/python -m pip install -r requirements.txt
fi

if [ "$(uname)" = "Darwin" ]; then
  echo "If macOS asks whether Python may accept incoming network connections, click Allow."
fi
exec .venv/bin/python -m backend "$@"

#!/usr/bin/env bash
# Flower Terminal - Linux/macOS launcher: ./start.sh  then open http://127.0.0.1:8000
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -r requirements.txt
fi
exec .venv/bin/python -m backend

#!/usr/bin/env bash
# Flower health check: tests your installation and live data, writes check-report.txt.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -r requirements.txt
fi
exec .venv/bin/python -m backend.check --live --report check-report.txt "$@"

#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "Python virtualenv not found at $VENV_PYTHON"
  echo "Create it with: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

exec "$VENV_PYTHON" -m uvicorn main:app \
  --app-dir "$PROJECT_DIR" \
  --host 127.0.0.1 \
  --port 8000 \
  --reload

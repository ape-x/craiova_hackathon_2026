#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ! -x "$project_dir/backend/.venv/bin/python" ]]; then
  echo "Create backend/.venv with Python 3.12 and install backend/requirements.lock first. See README.md." >&2
  exit 1
fi
if [[ ! -d "$project_dir/frontend/node_modules" ]]; then
  echo "Run npm ci in frontend first. See README.md." >&2
  exit 1
fi
cleanup() {
  kill "${api_pid:-}" "${web_pid:-}" 2>/dev/null || true
  wait "${api_pid:-}" "${web_pid:-}" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
(cd "$project_dir/backend" && exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload --no-access-log --timeout-graceful-shutdown 5) &
api_pid=$!
(cd "$project_dir/frontend" && exec node_modules/.bin/vite --host 127.0.0.1 --port 5173 --strictPort) &
web_pid=$!
# macOS ships Bash 3, without wait -n. Stop both services when either exits.
while kill -0 "$api_pid" 2>/dev/null && kill -0 "$web_pid" 2>/dev/null; do
  sleep 1
done
if kill -0 "$api_pid" 2>/dev/null; then
  wait "$web_pid"
else
  wait "$api_pid"
fi

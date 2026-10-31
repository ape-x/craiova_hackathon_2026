#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Tests also block sockets and isolate the import-time database in conftest.py.
# Empty values override any configured .env credentials, never print them.
export OPENAI_API_KEY="" FR24_API_KEY="" GOOGLE_MAPS_API_KEY="" ENVIRONMENT="test"
(cd "$project_dir/backend" && .venv/bin/python -m pytest -q)
(cd "$project_dir/frontend" && npm test && npm run build)

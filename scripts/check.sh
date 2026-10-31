#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Tests also block sockets and isolate the import-time database in conftest.py.
# Empty values override any configured .env credentials, never print them.
export OPENAI_API_KEY="" FR24_API_KEY="" GOOGLE_MAPS_API_KEY="" ENVIRONMENT="test"
export GOOGLE_MAPS_SERVER_API_KEY="" BOOKING_DEMAND_API_TOKEN="" BOOKING_AFFILIATE_ID=""
export LITEAPI_API_KEY="" LITEAPI_ENVIRONMENT="sandbox" HOTEL_OFFERS_PROVIDER="auto"
export DUFFEL_ACCESS_TOKEN="" DUFFEL_ENVIRONMENT="sandbox"
export DISTRIBUSION_API_KEY="" DISTRIBUSION_ENVIRONMENT="sandbox" DISTRIBUSION_OFFERS_ENABLED="false"
export APL_API_KEY=""
export FLIGHT_OFFERS_PROVIDER="auto" OPERATION_MODE="simulation"
(cd "$project_dir/backend" && .venv/bin/python -m pytest -q)
(cd "$project_dir/frontend" && npm test && npm run build)

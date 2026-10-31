# Implementation contract

Working contract; backend owns the authoritative typed schema and reports adjustments before consumers change.

## Layout / ownership
- backend/app/{models,seed,solver,store,service,main,config}.py + backend/tests: backend agent.
- backend/app/providers/* + provider tests + frontend/src/liveVoice.ts: integration agent.
- frontend/src/scene/*: scene agent.
- frontend shell, API client, styles, mobile passenger experience, packaging, deployment docs: root.

## HTTP
- GET /api/health — liveness
- GET /api/config — provider readiness and public configuration, no secrets
- GET /api/world — complete authoritative World
- GET /api/stream — SSE world updates, reconnectable; cookie auth if configured
- POST /api/events — typed scenario event; expected_version, idempotency_key, type, target_id, value optional
- POST /api/commands — text, expected_version, idempotency_key; AI parse and plan when configured, explicit 503 otherwise
- POST /api/replan — expected_version, idempotency_key, mode: ai | solver; solver mode visibly marked deterministic
- POST /api/offers/{offer_id}/accept — passenger-scoped token and idempotency_key
- POST /api/offers/{offer_id}/decline — same passenger-scoped credentials; held offers only; releases all segments atomically and sets the group waiting
- GET /api/passenger/{token} — only that group's offer and itinerary
- POST /api/passenger/{token}/constraints — deadline / accessible / budget changes, invalidates impacted offer
- POST /api/reset — fresh seeded scenario; demo operator action
- POST /api/voice/session — server-mediated WebRTC handshake, contract determined from official docs

## Rendering interface
Scene receives world plus selected entity and mode: airport | region | passenger. Scene renders interactive Three.js geometry from state; not a mockup screenshot. Scene owns only files below scene/ and exports default Scene. Root supplies props adapter if needed. Map Google 3D optional key-backed mode with honest availability labeling.

## Provider interface (plain JSON-compatible dictionaries)
AIProvider(settings): async interpret(text, world) -> {events: [...], strategies: [...], explanation: str, focus_id: str | null}; async choose(world, candidates, request) -> {plan_id: str, explanation: str, provider: str, latency_ms: number}. Raises explicit ProviderError on missing credentials, invalid response or provider failure. Never mutates world. Decisions chooses between dynamically supplied validated candidates; Responses Ultrafast extracts structured commands and strategy weights. Strategy: {id, label, objective: balanced|fastest|lowest_cost, preferred_airport?: str, weights?: dict}.
FlightProvider(settings): async fetch() -> list of observations, timestamp/source preserved, shared polling only. No future seat inventory claims.
VoiceProvider(settings): exact documented handshake, server secrets, short sessions and explicit close.

## Persistence
Atomic version checks + inventory reservations, TTLs, idempotency, stale plan rejection. Support durable SQLite locally, production container persistent volume; multiple process safety through transactions. No in-memory-only source of truth. Model calls outside transaction; application CAS revalidates version.

Concurrent copies of a planning request share a durable owner-scoped claim. While pending, the duplicate receives HTTP503 with `applied:null`, `details:{in_progress:true,retry_after_seconds:2}` and `Retry-After:2`; the client must preserve the original serialized request/key. Payload collisions are HTTP409. Current passenger authorization is required even before replaying a cached passenger mutation, including after scenario reset.

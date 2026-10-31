# Backend operations and guarantees

The backend supports **assisted live operations** and a separate **synthetic recovery simulator**, with optional live AI and aircraft observations. `OPERATION_MODE=live` is the default and starts without passenger or resource inventory. Operators can import passenger manifests, explicitly generate test passengers, search supplier quotes and record evidenced services. Passenger acceptance records a preference; external booking confirmation requires operator references. The application does not issue airline tickets, hotel bookings, vehicle orders or flight instructions. See [live-mode inputs, provenance and limits](live-mode.md).

The simulation allocation flows below apply when `OPERATION_MODE=simulation` is explicitly selected. Its seeded world contains 180 fictional passengers in 72 indivisible parties, and acceptance confirms simulated reservations. Tests cover varied scenarios containing 180–300 passengers. Use separate database volumes for live operations and demonstrations; changing modes archives the previous world before starting the selected mode.

## Run locally

Use Python 3.12. The project does not modify the system Python. From the repository root:

```sh
python3 -m pip install --user uv
python3 -m uv python install 3.12
python3 -m uv venv --python 3.12 backend/.venv
cd backend
python3 -m uv pip sync requirements-test.lock
python3 -m uv pip install -e '.[test]'
test -f .env || cp .env.example .env
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload --timeout-graceful-shutdown 5
```

The conditional copy preserves an existing `.env`. The API is at `http://127.0.0.1:8000/api`. `/api/health` checks the running process; `/api/ready` also queries the authoritative SQLite world. Development-only API documentation is at `/docs`. No provider keys are required for explicit deterministic planning. Missing AI credentials make `/api/commands` return HTTP503 with `applied:false`; the backend never labels a local fallback as AI.

SSE connections are intentionally long-lived. Keep the five-second graceful-shutdown limit, including during development reload; otherwise a reload can wait indefinitely for connected browsers. Verify a timed-out mutation by fetching the current world and retrying its **same idempotency key**, rather than assuming that it failed.

## Architecture

| File | Responsibility |
|---|---|
| `app/models.py` | Strict Pydantic schema; explicit event types; canonical entity IDs |
| `app/seed.py` | Fictional passengers, dated flights, buses, hotel inventory and airports |
| `app/solver.py` | Route enumeration, bounded allocation and independent feasibility checker |
| `app/service.py` | Incidents, planning, complete itinerary holds, confirmation and passenger scope |
| `app/store.py` | SQLite transactions, version checks, expiry, idempotency, audit, polling ledger and telemetry |
| `app/main.py` | HTTP, SSE, authentication, lifecycle, input limits and shared observation polling |
| `app/providers/` | Actual provider contracts; no direct authority to mutate the world |
| `app/transfer_planning.py` | Manual UK airport–hotel transfer receipts, short-lived cache and atomic direction-scoped import |
| `app/recovery_jobs.py`, `app/hotel_recovery.py` | Durable scoped searches and bounded four-leg hotel packages with separately counted source dispatches |
| `app/stay_requirements.py`, `app/hotel_recovery_profile.py` | Validated full-stay constraints and stable, case-bound operator inputs saved with successful hotel plans |
| `app/pricing.py`, `app/initial_position.py`, `app/airport_arrival.py` | Typed GBP/ECB evidence, receipt freshness, first-leg operator positions and exact-flight return allowances |

An AI command is interpreted into typed events and strategies, applied to a private projection, solved against current inventory, and passed to Decisions for selection. The server rechecks the world version and exact feasibility before applying the events and all holds in one transaction. Model calls happen outside that transaction. An unknown ID, malformed response, provider error or stale version prevents partial application. A visual-only or ambiguous interpretation with no events/strategies produces an idempotent explanation and leaves the operational world unchanged.

`bus_b12`, `B12` and surrounding whitespace normalize to the same known entity; normalization never invents a missing ID. Up to 12 events can be combined in one command. Strategies can express arrival, cost and stability weights. When a flight requires diversion, alternatives are enumerated from currently eligible airports rather than a fixed chosen airport.

## Allocation and inventory invariants

- Each party is allocated as a whole. Capacity counts people on transport and `ceil(party size / room_capacity)` rooms in a hotel. Hotel prices are **per room** despite the shared schema field name `cost_per_person`.
- A recovered journey ends at the party's destination. A hotel alone does not count. Every leg is checked against authoritative availability, accessibility, departure/arrival, cost and capacity.
- The first flight needs 30 minutes before departure; connecting flights require 30 minutes and other connections 10 minutes. A gap crossing midnight requires an explicit dated accommodation segment covering that overnight gap. One booked night cannot cover a second unbooked night.
- Shared capacity and the global budget include unaffected confirmed offers. Individual deadlines and group budgets are hard constraints. A budget reduction below confirmed commitments is rejected with the required minimum.
- Physical vehicle services must form a feasible adjacent schedule: no overlap, ten-minute turnaround and matching end/start locations. An explicit return service can connect later trips; there is no implicit vehicle repositioning.
- Closing an airport blocks flights touching it, while ground transfers remain possible. The inbound F201 can be diverted repeatedly: closing its newly chosen arrival airport invalidates dependent passenger connections and reopens airport comparison.
- Only an airborne flight can be diverted. Cancelling an airborne flight or restoring a flight through a closed airport is rejected. Repeating an unchanged arrival preserves existing commitments; returning to the passengers' available destination airport does not create fictitious ground-recovery cases.
- Capacity reductions retain confirmations before temporary holds, preserving whole parties that fit. Availability failures invalidate every dependent journey. Accessibility changes affect only parties requiring accessibility. All other confirmations remain intact.
- Every new plan replaces unaccepted holds. It preserves feasible confirmations; therefore the current strategy cannot freely reassign accepted passengers. Every hold reserves all legs atomically and expires using real UTC time, independent of the fixed scenario clock.
- Acceptance checks the exact reservation set and confirms every segment in one transaction. A partial or expired hold cannot partially confirm. Concurrent retries cannot create additional inventory consumption or duplicate confirmation events.

The allocator enumerates at most 64 complete routes per party, up to four legs, and compares four group orderings per strategy. Recovery count is ranked first within a candidate, then party priority and the strategy objective. AI may choose a feasible lower-recovery candidate to reflect a stated tradeoff; feasibility remains mandatory. **Global optimality is not proved**, and an exception can mean the bounded allocator did not find a shared allocation, not that mathematical impossibility was proved. No airline fare rules, crew legality, runway performance, fuel planning or certified diversion suitability are modeled.

## Persistence, concurrency and events

### Automatic hotel packages and saved inputs

An optional `hotel_recovery` request runs for exactly one linked, explicitly adult-only group. It requires flight, hotel and bus modes, supported UK initial/onward airports, complete declared pickup/drop-off inputs and at least ten job calls. The default eight-call limit is unchanged and is never silently increased. Unsupported accessibility or missing physical inputs stop the request. The flight-only path remains available without this option.

The bounded pipeline requests one onward flight date (the checkout date in Europe/London), obtains hotel rates and examines one cheapest complete-price property. It validates the exact property's location, postal address, local stay policy and requested occupancy. Outbound and return APL quotes, two future Google driving routes and one ECB reference are separate durable steps; no combined callback conceals supplier calls. The conservative ten-call total also includes Responses query selection, the flight search, hotel rates, exact property metadata and Decisions. At most three source requests run together, and every already-dispatched outcome is recorded before a failed or cancelled job ends. No unknown dispatch is automatically repeated.

The typed `hotel_stay_requirement` requires one actual room quote covering the whole requested stay and room count; waiting or a hotel-only route cannot satisfy recovery. Return pickup is checkout plus a ten-minute application allowance. The declared airport processing buffer and exact onward flight are validated against the sourced drive. Candidates contain four resources: initial private transfer, hotel, return private transfer and onward flight. They compare at most three fitting vehicle options per direction. Incomplete pricing, missing links, source-policy exclusions and unaffordable packages stop visibly without a hidden second-property search.

All assembly happens in a projected world. The final transaction rechecks version/scope, current sources and independent feasibility, then applies only chosen resources/nodes, the selected group's stay requirement, profile and complete proposal. Out-of-scope offers and confirmed records remain fixed. No supplier hold or booking is created. Automatic transfers reserve their quote ordinals in the same persistent sequence domain as manual searches, so old/new revision ordering remains comparable.

After a successful complete plan, `hotel_recovery_profile` stores stable operator inputs with a version and case/group scope fingerprint; it contains no job IDs, rotating timestamps or saved fares. The same profile is installed before transfer fingerprints in projection and commit. Failed, cancelled, unknown, stale or rolled-back jobs cannot overwrite it. Voice/text reuse requires one exact linked group, all three modes and at least eleven total calls: one command interpretation plus ten job calls. Default command allowance remains eight. No profile means the typed setup is required; current readiness, deadlines, budget, source access and exact saved dates/points are revalidated before job dispatch. Changed inputs require the typed form, not inferred dates or coordinates.

A real automatic run on API `95a0606` with web `03112f6` completed all ten calls and applied four new resources for two passengers with zero unresolved requirements. Queue-to-apply time was 9.495 seconds (9.16 seconds displayed for the job), a single measurement. The world advanced from 47 to 48, the out-of-scope group was unchanged, and no bookings were recorded. Normal quote expiry later advanced the world to 49. Browser review restored exact saved inputs without supplier calls, cleared both point confirmations and kept the chosen ten-call cap. This verifies the typed automatic source path and saved-input review, not a spoken hotel rerun or browser microphone acceptance. See the [verification report](verification/report.md).

### Manual private-transfer boundary

The authenticated `/api/transfers/planning-quotes` flow supports manual UK
airport-point → hotel and hotel → UK airport directions. The default
`initial_airport_to_hotel` preserves the group's airport origin and creates a
separate `meeting_point` node. A typed,
fingerprinted operator assumption authorizes that point only as the selected
group's first leg; there is no synthetic airport-to-point edge or later
terminal-to-flight shortcut. Exact property metadata and a future Google driving
prediction are sourced before import. A room quote may remain stale; transport
search does not refresh room availability or establish a hotel booking.

`hotel_to_airport` omits `initial_point` and supplies `airport_dropoff`, the exact
`onward_resource_id`, and operator `airport_processing_minutes` (1–720). The
onward flight must be current, available, unbooked, sourced and scoped exclusively
to the group. Its source identity, departure, airport and incident constraints
are revalidated. The return starts at the actual property and can be followed
only by that flight after the declared allowance. `airport_arrival_assumption`
on Resource and Leg preserves the group/flight fingerprints, reported drop-off,
buffer and null departure terminal. It provides no first-leg position exemption.
The allowance is operator input, not airline guidance or guaranteed boarding.
Google's route destination is the declared drop-off coordinate; the IATA airport
node remains a separate reference. Optional APL meeting instructions remain
supplier pickup guidance, not evidence of the departure terminal.

The imported resource uses `transport_mode='private_transfer'`, one eligible
group and `capacity_scope='private_vehicle_quote'`. Capacity is the quoted party,
not supplier fleet stock. Typed `price_evidence` preserves the GBP vehicle price
and ECB EUR estimate in both Resource and Leg; Itinerary/Plan summaries identify
estimated components. The accepted `ecb_reference_v1` policy permits numeric EUR
planning comparisons without promising EUR settlement. Supplier quote time and
expiry stay null; typed app-receipt freshness is 120 seconds. Returns use the
earlier APL/Google receipt deadline, each plus 120 seconds, inclusive at the
boundary; missing/malformed/future Google receipts cannot qualify a new plan.
The UI exposes both UTC receipts and this combined cutoff without cancelling
historical recorded bookings. The independent
validator checks the evidence and current policy, and passenger/Decisions
representations retain the estimate distinction.

`transfer_quote_receipts` records dispatch before external I/O and retains request
identity separately from `transfer_quote_cache`. The source cache holds at most
64 entries for at most 120 seconds and is periodically purged even when traffic
polling is paused. One planning search runs at a time across workers. Completed
search retries replay a retained result; changed payloads conflict. Pending or
unknown dispatches return `applied:null` and never silently repeat provider work.
Expired cache cannot be refreshed with the same key. An import atomically writes
its node/resource, group-and-direction-scoped retirement, audit and idempotency receipt; accepted
import replay survives cache expiry. Newer imported revisions cannot be replaced
by an older cached fare, and unrelated/confirmed records remain protected.

Import alone creates no complete offer, supplier hold or booking. These endpoints
remain manual; the separate automatic package path above validates a complete chain
against newly sourced flight, hotel and transfer evidence. Implementation and
mocked checks are distinct from live evidence in
the [verification report](verification/report.md).

The backup script removes transient transfer source caches and marks dispatched
receipts unknown in the copy. Completed search receipts with no restored cache
report expiration; no historical search automatically resumes. Recorded
operational resource evidence remains distinct from the transient search cache.
Receipts are part of operational history, not an unlimited-retention guarantee.

### Shared operational transactions

SQLite uses WAL, a 15-second busy timeout and `BEGIN IMMEDIATE` for mutations. The operational world, complete inventory changes, audit records and idempotency result commit together. Stale `expected_version` receives HTTP409. Expired holds are released before validation; that expiry is committed even if it makes the requested plan stale. Identical idempotent retries return the original result; reuse with a different payload is rejected. Treat a replayed world as a historical response and fetch the latest world before issuing a new mutation.

Passenger authorization is checked against the current scenario before a cached acceptance or constraint response can be replayed. A reset therefore revokes old passenger tokens even for previously successful requests. After an independent expiry commit, the transaction rechecks authorization and idempotency before continuing, so a racing acceptance cannot apply twice or fail with a duplicate-key insertion.

Planning requests acquire a durable, owner-scoped claim before any provider call. Concurrent copies of the same key/payload do not repeat interpretation or selection: the pending copy receives HTTP503, `applied:null`, `in_progress:true` and `Retry-After:2`. Keep the original request ID and payload when retrying. Completed requests replay their stored result; a changed payload receives HTTP409. Claims are released on completion, failure or cancellation. A crashed worker's claim can be recovered after its lease expires; this does not guarantee exactly-once upstream billing after a crash or unknown provider outcome.

Connections are explicitly closed; the Python SQLite connection context manager alone only handles transaction completion. The database file is set to mode 0600. Use a persistent local disk volume on one host; do not put SQLite WAL on a shared network filesystem. Independent worker processes coordinate through SQLite, including provider poll leases. The deployment defaults to one worker. There is a one-time observation-redaction migration, but no general schema migration framework or operational-history retention job. Audit, previous offers, idempotency snapshots and session records grow over time; a long-running pilot needs storage monitoring, backup/restore rehearsal and retention design.

The world keeps its latest 200 events and six decisions for display; the audit table is separate from that UI window. `/api/audit` returns the latest 200 records. SSE sends a complete current snapshot after reconnect, not a replay of every missed event. Events have source, timestamp and world version. Reset creates fresh passenger tokens and a new scenario ID while retaining monotonically increasing world versions. Define an operational-history retention policy before a long-running deployment.

## Provenance, privacy and authentication

Synthetic inventory and passenger data are authoritative only inside the simulator. Flightradar24 observations live in a separate table and cannot change seats, routes, availability or flight decisions. Observations preserve source and observation/receipt timestamps; live/sandbox status remains explicit. Feed failure preserves the last observations with an error/staleness status. No provider key is returned by `/api/config`; the Maps browser key is intentionally public and must be origin/API restricted.

Observation cache lifetime defaults to 3,600 seconds, configurable through `OBSERVATION_RETENTION_SECONDS` from 60 to 86,400 seconds. Entries expire by the older source/receipt timestamp. Reads and replays immediately exclude expired entries; background cleanup continues while polling is paused. Cleanup resumes on application restart. Only bounded status/freshness metadata remains. World and idempotency storage exclude observations; replays overlay the current separate cache. A one-time migration strips observations from older snapshots. Backups omit the transient observation table. This is a technical cache policy, not a statement of license compliance or secure erasure from previously created backups.

Operator endpoints require the configured bearer token or a signed, revocable HTTP-only session cookie. Production cookies use Secure and SameSite Strict. Passenger bearer links expose only their own fictional group, offer and referenced resources; they must not be shared publicly. Reset revokes access to the current scenario through old passenger links. Passenger acceptance responses contain no operator world.

Provider context omits passenger access tokens, member lists, raw observations and audit history. Imported passenger names, identifiers, evidence and booking references remain in the protected operational database and its mode archives; stored passenger data is not necessarily synthetic. Voice audio is transmitted by the browser to the configured voice provider. Commands and model decisions remain in the operator's audit context. Access logs redact passenger path tokens and query strings; the container disables access logging entirely. Keep reverse-proxy logging disabled or separately redact both frontend passenger URLs and API passenger URLs. Never log request bodies, SDP, cookies, authorization headers or environment values. Keep the database, `.env`, backups and generated credentials out of public/static assets and source control.

Production startup requires a token of at least 32 non-whitespace characters, a valid HTTPS public URL, explicit HTTPS CORS origins and allowed hosts containing the public hostname. Mutation origin checks, request-size limits (including chunked bodies), no-store responses and security headers are enabled. The login limiter is per process/IP; large public deployments need an edge limiter. This is one operator role, not multi-tenant identity management.

## Production process and proxy

The root `compose.production.yaml` supplies HTTPS through Caddy, a private non-root API container and a persistent database volume. Serve frontend and API on the same origin. Keep `/api` unchanged through the proxy, disable SSE buffering and use a read timeout longer than the 15-second heartbeat. Trust forwarded headers only from the proxy; `--forwarded-allow-ips '*'` is appropriate only while the API is inaccessible outside that trusted network. See the verified [FastAPI proxy guide](https://fastapi.tiangolo.com/advanced/behind-a-proxy/) and [Uvicorn settings](https://uvicorn.dev/settings/).

Use `/api/ready` for readiness and `/api/health` for process liveness. Protect the volume and back it up with SQLite's backup API or while the application is stopped; do not copy just the main database file while a live WAL contains committed changes. [Python SQLite documentation](https://docs.python.org/3.12/library/sqlite3.html) describes connection lifecycle and database backup.

## Provider spend and runtime limits

FR24 starts paused for a fresh database. An operator's start/stop choice persists across restarts. A shared persistent lease enforces the interval and reserves worst-case credits before each request: `effective_request_limit = min(FR24_MAX_REQUESTS, floor(FR24_MAX_CREDITS / (FR24_RESULT_LIMIT * 8)))`. A zero limit makes zero calls. Restart, scenario reset or additional workers do not replenish this ledger. Failed/in-flight requests may consume a conservative reservation; the local counter is not a billing statement. Raising limits is a deliberate configuration change.

Default bounds `46.0,43.8,23.0,26.5` use north,south,west,east and include CRA, OTP and SBZ. They cover 7.7 square degrees, below the provider adapter's 25-degree-area limit. A five-result response does not guarantee coverage at each airport. Existing `.env` files must be changed explicitly to adopt these defaults.

`POST /api/voice/session` accepts the existing `{sdp}` body plus optional `scope: {incident_id?, group_id?}` with at least one nonblank exact ID. The browser snapshots IDs at Start; the server resolves current state and rejects unknown or nonreciprocal selections before requesting a session. Startup reference facts contain only mode, case/flight/date identity, at most eight group IDs/display labels and a scope-valid saved-hotel-profile boolean, within 6,000 UTF-8 bytes. They omit raw observations, member lists, passenger access tokens, coordinates, prices and call allowances. The profile flag does not assert that dates, current constraints or fares remain usable.

Facts are supplied through the documented [GPT-Live startup history](https://developers.openai.com/api/reference/resources/live/methods/create), as one `developer` message with one `input_text` part. Labels are reference data; the policy forbids silently replacing spoken dates, names or limits. Context supplies no execution authority or synthetic user request. It is a startup snapshot, not a continuous context update. Unscoped sessions keep the prior request payload. Offline tests verify this contract and exact-date rejection; the new context path has not had a paid session or demonstrated improved transcription accuracy.

Voice sessions have a server watchdog and bounded duration (`VOICE_MAX_SECONDS`, default 180 seconds). Provider requests time out and do not silently retry billed model calls. There is no application-wide monetary budget for AI/voice or concurrent operator sessions; use account/project budget controls and operator access. A configured key means configured, not independently verified. Live model/voice/FR24 behavior requires account access and real integration testing; automated tests use explicit provider mocks.

The planning deadline is `2 * REQUEST_TIMEOUT_SECONDS + 30`, default 120 seconds; its request claim lasts 30 seconds longer. Only provider waits yield inside this scope. SQLite application remains synchronous and completes before the claim is released, so timeout handling cannot leave a detached commit in a background thread. The browser's default mutation wait is 105 seconds and Caddy's response-header limit is 120 seconds; the existing unknown-outcome recovery flow preserves the request identity if the client stops waiting first. If increasing provider timeouts, coordinate client/proxy waits or expect that recovery flow. A server deadline prevents scenario application but does not prove that upstream usage was unbilled.

## Verification and latency

From `backend`:

```sh
.venv/bin/python -m pytest -q
.venv/bin/python scripts/benchmark.py --samples 12 --passengers 180 240 300 --output ../docs/benchmark-results.json
```

The suite includes randomized capacities, budgets, accessibility and deadlines across 16 seeds spanning 180–300 passengers; complete destinations and shared capacity are independently checked. It also covers atomic multi-leg acceptance, concurrent last-seat planning, concurrent acceptance retries, stale model results, idempotency, expiry, repeated diversion, surviving confirmations, hotel occupancy, vehicle schedules, read-only commands, two normalized constraints in one command, provider errors, privacy and zero-budget polling.

Each `Decision.timings_ms` contains measured `interpretation_ms`, `solver_ms`, `decisions_ms`, `apply_ms`, `transaction_ms` and `server_total_ms`. `apply_ms` is domain validation/mutation; `transaction_ms` includes the database commit and overlaps apply. `server_total_ms` runs from service entry through durable commit and excludes the subsequent telemetry write, response transmission and browser rendering. Metrics are persisted independently of operational versions and survive SSE/read/idempotent replay. Do not add overlapping timings. Deterministic mode's provider timings are zero because no provider call occurs.

The checked-in benchmark JSON records raw samples, medians, nearest-rank p95, outcomes and environment. On the measured Apple M1 Max, Python 3.12.16, 12 samples per case, deterministic service median/p95 ranged from **40/47 ms** for 180 passengers with the initial cancellation to **166/172 ms** for 300 passengers with CRA closed and six candidate strategies. These are local in-process solver/SQLite measurements under development load, **not AI, voice, browser round-trip or production performance claims**. Re-run on the deployment hardware; measure actual configured model calls separately.

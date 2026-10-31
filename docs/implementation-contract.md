# Live assisted recovery — implementation contract

Updated 31 October 2026. FastAPI owns the validated schema, operational state and allocation checks; React consumes the same versioned state. This contract describes the shipped live assisted workflow, with pending work explicitly separated. See [product scope](product-spec.md), [live data](live-mode.md) and [verification](verification/report.md).

## State and provenance

- `World.mode=live` starts with a real airport reference and no passenger or supplier inventory. The persistent operation is never reset by opening a case or running a search.
- `Airport.kind` is `airport`, `station` or `hotel`. A property or station is a physical node, never an airport/diversion target. Supplier hotel coordinates and their derived local time zone remain attached to the property.
- `PassengerGroup.source` distinguishes imported passengers, operator input and generated planning assumptions. Generated groups cannot receive real external booking confirmations.
- `RecoveryIncident` holds the operator's report, exact flight selection reference and explicitly linked groups. `incident.flight` is transient FR24 context; expiration does not delete the operator's case.
- `Resource` represents a dated, sourced service. Quote-specific price/capacity scope is preserved; geography, road estimates and FR24 observations do not manufacture inventory.
- Live offer states distinguish `proposed`, passenger `accepted` and externally `confirmed`. Local reservations coordinate proposals inside the app; they are not supplier holds.

## HTTP surface

Operator endpoints require operator authentication. Mutations use `expected_version` and `idempotency_key` where specified by their typed request; passenger actions use the group's current token. Source lookups do not accept browser-supplied prices or provider credentials.

| Endpoint | Contract |
|---|---|
| `GET /api/health`, `GET /api/ready` | Liveness and database readiness |
| `GET /api/config` | Public configuration and configured-provider flags; not a claim of verified account access |
| `GET /api/world`, `GET /api/stream` | Current operational world and authenticated SSE updates |
| `GET /api/audit` | Operator audit history |
| `POST /api/flights/lookup` | Flight number and explicit first-tracked UTC date; return exact FR24 matches, freshness and opaque selection references |
| `POST /api/incidents` | Append operator report for the selected flight and optional explicit group selection |
| `POST /api/incidents/{id}/groups` | Append compatible existing groups; do not move their locations or replace existing bookings |
| `POST /api/incidents/{id}/refresh-flight` | Refresh the same exact flight leg/date; never silently substitute another match |
| `POST /api/incidents/{id}/recovery-jobs` | HTTP202 with `{job,replayed}`; explicit group scope, objective, modes, call limit and instructions |
| `GET /api/incidents/{id}/recovery-jobs`, `GET /api/recovery-jobs/{id}` | Read durable progress; no new supplier dispatch |
| `POST /api/recovery-jobs/{id}/cancel` | Idempotent cancellation request; an already-running external call may finish |
| `POST /api/live/import-passengers` | Validated manifest with evidence and group constraints |
| `POST /api/live/generate-passengers` | Explicit planning assumptions only; creates no transport or hotel inventory |
| `POST /api/live/resources` | Operator-documented dated service with evidence, price, capacity and validity |
| `POST /api/real-data/search` | Reference hotels/routes and supplier hotel offers; no operational import or booking |
| `POST /api/live/hotel-quotes` | Group-specific complete hotel quotes at actual property nodes; sourced stay-policy validation or separately declared property-agreed times |
| `POST /api/live/travel/search`, `POST /api/live/travel/import` | Separate dated supplier search and server-held quote import |
| `GET /api/transfers/status`, `POST /api/transfers/quote` | APL UK price snapshots in GBP per vehicle; no import, booking, duration guarantee or EUR budget conversion |
| `POST /api/live/offers/{id}/confirm` | Explicit operator booking references for every segment of a real passenger group's proposal |
| `GET /api/passenger/{token}` | Only the authorized group's data and itinerary |
| `POST /api/offers/{id}/accept` | Record live passenger preference; no supplier booking |
| `POST /api/offers/{id}/decline` | Decline an eligible proposed/accepted offer and release its local segment records atomically |
| `POST /api/passenger/{token}/constraints` | Update supported group constraints and invalidate the impacted proposal |
| `POST /api/commands` | Validated commands; explicit existing-case recovery intent can queue a scoped durable job and return its receipt |
| `POST /api/replan` | Validated AI/solver operation over sourced inventory; not an unrestricted supplier search |
| `POST /api/voice/session` | Server-mediated GPT-Live session and bounded lifecycle |
| `POST /api/traffic/start`, `POST /api/traffic/stop` | Explicit shared FR24 collection with persistent request/credit budget |

Legacy scenario events and `/api/reset` remain implementation support for the internal simulation mode. Synthetic reset is rejected in live; it is not an operator step in the live recovery workflow.

## Durable recovery jobs

Start input: `group_ids` (1–24), `objective` (`balanced`, `fastest`, `lowest_cost`), requested `modes`, `max_provider_calls` (3–26), `instructions`, expected world version and idempotency key. Every selected group must already belong to the case; externally confirmed groups require separate operator handling.

Job statuses: `queued`, `running`, `completed`, `blocked`, `failed`, `cancelled`, `needs_review`, `unknown_outcome`. Stages: validation, query planning, supplier searches, evaluation, Decisions selection and atomic application. The ledger records each actual dispatch, outcome, quote count and affected group selection. Jobs are separate from `World` and survive reopening the case.

Current automatic sourcing covers flight offers. Responses selects eligible dated queries; independent supplier searches can run concurrently. Deterministic code builds and checks complete itineraries; Decisions selects among valid candidates. Existing complete sourced ground/hotel links can participate. Missing stay inputs, ground connections, fare currency or dated transport information produce explicit gaps.

`result` records plan/decision references, proposed and exception passenger counts, offer/service references, source counts and optional waiting intervals. These are historical result counts at `applied_version`; the frontend separately checks which offers remain active. `quote_not_usable` and `no_flight_offers` are source/search exclusions, not automatically unresolved passenger requirements. Overnight-wait notes remain informational unless an explicit applicable requirement says otherwise.

The live solver supports up to six itinerary legs, including physical hotel transfers. It validates elapsed time and dates; it does not apply the internal simulation's epoch-day rule as a mandatory live hotel requirement. Waiting summaries use the actual location's time zone when known and disclose unknown zones. There is currently no structured mandatory-rest policy in the group schema; free-form instructions do not establish a new validated constraint. A hotel alone is never a recovered journey.

## Manual hotel import and stay policy

The hotel-import request shape is unchanged. With `stay_times_confirmed=false`, the server fetches metadata for the exact property even when the rate already contains coordinates, bounded to ten properties with concurrency three. Property-coordinate IANA time zones and the pinned time-zone data anchor the supplied check-in window and checkout cutoff to the quoted dates. A bounded check-in window may span midnight; absent `checkin_end`, only the exact published start is automatically accepted.

Missing, unsupported or conflicting policy times, special check-in instructions, source/property identity or freshness mismatches, and ambiguous/nonexistent daylight-saving boundaries exclude the property with a reason. Successful provenance contains `stay_times_source='supplier_policy'`, `stay_times_confirmed=false` and `stay_policy_validation` with source, property ID, time zone and dated bounds. `stay_times_confirmed=true` remains a separate `operator_confirmed` declaration that the stay times were agreed with the property; it is not a supplier booking.

The response preserves `skipped_incomplete_pricing` and adds `skipped_stay_policy` and `hotel_exclusions[{hotel_id,code,message}]`. If every option is excluded, HTTP422 leaves the operation unchanged. No policy result implies a shuttle, physical transfer or reserved room. This automatic policy path is implemented; live operational import and a complete hotel-plus-transfer itinerary remain separate verification gates.

## Mutation, retry and budget boundaries

SQLite is authoritative for versions, allocations, the job ledger and idempotency. Provider calls occur outside world transactions; a compare-and-apply step rechecks source freshness and current constraints before committing. Scoped jobs preserve all out-of-scope allocations, including proposed and accepted ones.

Replay of a job-start key returns its current job without another provider call. Unknown outcomes require review and an explicit new attempt, not an automatic billed retry. Cancellation prevents subsequent dispatch/application; it cannot undo a dispatched provider call. This is not an upstream exactly-once billing guarantee.

Existing request claims can return HTTP503 with `applied:null`, `details.in_progress=true` and `Retry-After`. Clients retain the original serialized payload/key while the result is uncertain. Definitive conflicts unlock editing after refresh; changed payloads receive new keys. Passenger authorization is checked before cached replay, and stale/revoked tokens cannot expose a prior operation.

The planner's EUR budget checks use documented complete costs. Missing mandatory hotel fees or unverified currency conversion prevent a fully calculated budget. APL GBP quotes remain outside allocatable EUR resources. The pure ECB adapter can produce a dated reference estimate and an explicitly requested operator allowance, but neither is a supplier EUR price or guaranteed settlement. It is not integrated into the current planner.

## Rendering and source separation

`Scene` receives `world`, selection and camera mode; Google Maps provides geographic rendering when available. The native Three.js scene and authored GLBs retain their illustrative provenance. Live scheduled inventory is not drawn as tracked passenger/vehicle motion. FR24 observations, physical location nodes and their source timestamps remain distinct from proposals.

Google TRANSIT currently has a pure request/response adapter and offline tests only. Stops and schedules do not establish seat availability; incomplete fares remain unknown, and missing walking durations do not pass a walking limit. There is no connected public-transit inventory or automatic booking path.

Passenger itineraries expand validated supplier flight segments and connection waits. Source-local clocks include explicit UTC offsets; missing local clocks use labeled UTC. Source endpoints must reconcile with the epoch and authoritative resource/offer-leg times using departure-floor and arrival-ceiling minute bounds. Invalid calendar dates or inconsistent provenance use the explicitly labeled Europe/Bucharest operations-time summary instead. The passenger arrival summary uses the same validated final segment.

## AI and voice status

The scoped voice/text-to-job bridge is implemented. The current request must name an existing case ID or an unambiguous flight, with a date when needed to distinguish cases. Only already-linked groups are eligible; externally confirmed groups are protected. A recovery intent does not invoke a global replan. A queued receipt includes `incident_id`, `recovery_job`, `command_interpretation_calls` and `recovery_total_call_limit`; the UI opens the matching case and polls the existing job panel.

The default command allowance is eight calls total: one interpretation plus at most seven job calls. A stated limit of four permits one interpretation plus three job calls; a limit of three or less returns clarification without a job. The manual form's default allowance remains eight job calls. Parsed intent is cached, job creation and the command receipt are atomic, and unknown/crashed interpretation is never automatically reissued. A voice acknowledgement confirms only the recorded request, not a completed plan or booking.

The real Responses → supplier searches → Decisions case-form workflow has live evidence. The scoped command bridge has offline coverage; its live runtime check is still pending. Existing voice transport/lifecycle has a separate bounded provider check using synthetic silence.

Model/provider errors remain explicit. The app does not execute arbitrary model code, invent supplier stock or substitute a scripted AI result. Full microphone, delegation, playback and interruption testing remains a separate acceptance gate.

## Data handling and internal modes

Provider secrets remain server-side; the Maps browser key is explicitly public and requires separate origin/API restrictions. Temporary FR24 context is stripped from portable exports, idempotency snapshots and operational backups. Public verification records omit raw provider payloads, private case/job/group references, passenger tokens, account contacts and credentials.

The sole active application is the persistent local instance on port 8080. Internal simulation fixtures and their reset semantics are retained for regression tests, not as a fallback for missing live supply. Source modules and schemas remain the authority if this summary lags a subsequent version.

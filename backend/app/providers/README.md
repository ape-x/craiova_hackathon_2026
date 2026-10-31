# Real provider adapters

Verified against official documentation on 2026-10-31. Mock HTTP tests validate
request schemas and failure handling; they **do not prove account access or live
latency**. No external call is made until a configured operator invokes it.

Bounded real-account checks on 2026-10-31 passed Sol Ultrafast interpretation
and Decisions selection; measured individual requests took 3,178.2 ms and
858.6 ms respectively. They used a visual-focus request and two locally validated
candidates, without opening a microphone or mutating the operational world.
See the [verification report](../../../docs/verification/report.md) and its
sanitized evidence. These samples do not establish full-scenario latency or
voice access. A separate single synthetic-silence WebRTC check subsequently
verified GPT-Live transport, sideband and final closure/usage; see
[voice-live-check.json](../../../docs/verification/voice-live-check.json).
Actual microphone/delegation/playback and FR24 observations remain unverified;
this repository supplies no credentials.

- `AIProvider.interpret`: `POST /v1/responses`, `gpt-6.1-sol`,
  `service_tier: ultrafast`, strict JSON schema, bounded output and timeout.
- `AIProvider.choose`: `POST /v1/decisions`, `gpt-6-luna`, dynamic choice IDs from
  validated solver candidates. Explanation reports solver facts; Decisions does
  not return a written rationale. No failure is replaced by a scripted AI result.
- `VoiceProvider.create`: JSON `POST /v1/live/sessions`, `gpt-live-1`, WebRTC,
  client delegation. The browser collects transcripts separately from delegation
  metadata and runs the same authenticated, versioned command workflow. It sends
  verified results through `session.commentary.append` with the original ID.
  This is application-orchestrated Decisions, **not native Live–Decisions
  delegation**. Native delegation is a different fixed session mode and is not
  claimed as implemented.
- `FlightProvider.fetch`: FR24 `/api/live/flight-positions/full`; a bounded region,
  maximum 20 returned observations (default 5), shared poll interval >=15 seconds,
  no retries, conservative per-process request/credit limits. Only the backend
  polling loop should call it. Keep polling paused until the operator starts it.

Settings (keys remain server-side):

```dotenv
OPENAI_API_KEY=
OPENAI_MODEL=gpt-6.1-sol
OPENAI_DECISION_MODEL=gpt-6-luna
OPENAI_VOICE_MODEL=gpt-live-1
OPENAI_SERVICE_TIER=ultrafast
REQUEST_TIMEOUT_SECONDS=45
VOICE_MAX_SECONDS=180
FR24_API_KEY=
FR24_ENVIRONMENT=live
FR24_BOUNDS=46.0,43.8,23.0,26.5
FR24_RESULT_LIMIT=5
FLIGHT_POLL_SECONDS=30
FR24_MAX_REQUESTS=80
FR24_MAX_CREDITS=4000
OBSERVATION_RETENTION_SECONDS=3600
```

For each new deployment, perform authorized smoke calls with its owning
account: successful Sol structured command, Decisions choice, microphone
connection and full graceful close, and an FR24 observation with source timestamp.
Check model permissions, project credits and rate limits independently. Credential
presence only means configured. Models and service tier never silently downgrade.

The browser closes voice after the configured duration, asks `session.close`, and
waits for `session.closed`. A server sideband watchdog independently enforces the
same duration and closes active sessions on graceful server shutdown. If sideband
attachment fails after initialization, the error explicitly warns that final usage
is unconfirmed; it never retries initialization. Unexpected server process death
or network failure still cannot guarantee receipt of final usage. Configure account
spending limits too; the watchdog is not an organization-wide spending cap.
The browser also bounds HTTP/SDP setup to 60 seconds and thinking/commentary
appends to 480 UTF-8 bytes; setup timeout stops microphone capture and explicitly
reports when remote finalization is unconfirmed.
The provider adapter's counters are conservative in-process safeguards, not the
account balance. The application also maintains a shared SQLite request/credit
reservation ledger that persists across restarts and scenario resets. Errors
retain a worst-case credit reservation because the upstream charge is unknown.
The separate observation cache defaults to one hour; reads, replay responses and
background cleanup enforce its technical retention policy even while polling is
paused. Operational snapshots and backups omit raw feed observations. Verify the
account's data license separately; this cache policy is not a compliance claim.
Sandbox data is labeled SANDBOX. Timestamps are preserved; stale observations do
not become fresh merely because they were fetched again. FR24 supplies no future
schedules, booking inventory or cancellation inference.

Default bounds `46.0,43.8,23.0,26.5` include CRA, OTP and SBZ. Bounds are ordered
north,south,west,east; the configured response limit can still exclude some local
aircraft from a given response. Existing `.env` files retain their explicit values.

Official references:

- https://developers.openai.com/api/docs/guides/structured-outputs
- https://developers.openai.com/api/docs/guides/ultrafast-mode
- https://developers.openai.com/api/docs/guides/decisions
- https://developers.openai.com/api/docs/guides/voice-webrtc
- https://developers.openai.com/api/docs/guides/live-delegation
- https://developers.openai.com/api/docs/guides/decisions-voice
- https://developers.openai.com/api/docs/guides/voice-server-controls
- https://fr24api.flightradar24.com/docs/sandbox-environment
- https://fr24api.flightradar24.com/docs/credit-overview
- https://fr24api.flightradar24.com/docs/faq
- https://fr24api.flightradar24.com/docs/storage-rules

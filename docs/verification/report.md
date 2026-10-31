# Implementation verification — 31 October 2026

This report separates executed tests, bounded real-provider checks and remaining acceptance gates. The application is a synthetic operations simulator. Real Sol Ultrafast interpretation, Decisions selection, GPT-Live transport/closure with synthetic silence and Google geographic rendering have passed. User microphone/delegation, observed traffic, surveyed terminal geometry and photographic fidelity remain open.

## Free-form scenario authoring — 2026-10-31

The release supports new airports, synthetic flights/buses/hotels and passenger batches, plus composable schedule, route, capacity, cost and group constraints. The voice/text pipeline carries current input separately from bounded prior requests and verified outcomes; clarification replies persist in the UI. Failed or uncertain requests are retained as explicitly unconfirmed context, not executed outcomes. Timestamped voice delegation tests cover delayed fragments, whole compound requests, duplicate events and stale turn isolation.

**179 backend tests and 89 frontend tests passed on an isolated snapshot of the exact staged release**, excluding concurrent unrelated work. TypeScript, production build and changed-file formatting passed. Checks include atomic rollback, server-generated groups/tokens, catalog aliases, temporal continuity, confirmed reservation protection, bounded authoring, multi-airport candidate shortlisting, typed single-plan approval and input-size limits. Public catalog data inclusion was checked in both wheel and Docker builds.

A real browser → Responses Ultrafast → solver → Decisions → temporary SQLite run created LIS, a 36-seat charter and four indivisible three-person families after a separate clarification turn. Server time was **4,162.96 ms** (Responses 3,578 ms; solver 19.58 ms; Decisions 551.81 ms). A subsequent free-form request delayed the flight 90 minutes and reduced capacity to nine: **2,693.81 ms** total, with three intact families offered transport and one family explicitly without a solution. These are individual observed samples, not latency guarantees. The operational Docker database was not changed by these tests. [Sanitized evidence](freeform-live-check.json).

This test exposed an actual Decisions HTTP400 for a single choice. The adapter now asks the model to approve or reject the sole validated plan using two typed boolean choices; only literal true applies it. Multiple plans use dynamic plan IDs. Ranking input contains compact authoritative metrics, with hard API input limits checked before transmission. The full validated itineraries remain server-side. [Official Decisions contract](https://developers.openai.com/api/reference/resources/decisions/methods/create).

The regional scene now fits newly authored destinations and shows all known airport/flight endpoints. A longitude-wrap regression was corrected and real LIS/LTN/CRA ordering tested; long flight routes remain visible above the schematic ground. The expanded view is explicitly labelled schematic. `freeform-network-live.jpg` shows the actual rendered Lisbon scenario after the capacity reduction.

These checks exercised real AI through text commands and tested voice transport/delegation boundaries with fixtures. **They do not claim a new live microphone/audio end-to-end test.** Weather physics, crew, fuel and ATC models remain outside the supported operations; unsupported effects require explicit clarification instead of silently choosing a preset.

## Geographic 3D object correction — 2026-10-31

The frontend passed **63 tests** and strict TypeScript/production build checks, including nine keyed-layer lifecycle tests and nine placement regressions. The unchanged backend previously passed 104 tests. Docker rebuilt and replaced only the web service; the running API and scenario database were preserved. The user's held offers expired normally (world version 29 to 30); no scenario reset or operational mutation was performed by this check.

The Google view now mounts the whole simulated fleet, participating airport terminals and one-to-five-person family assets without requiring a selected resource. Native and geographic views share the stand, bus-bay and family layout. Camera targets use terrain-relative altitude for the simulation and absolute altitude for observations. Exported Y-up GLBs use the documented 270-degree tilt conversion; models retain their DOM identity while poses change. Individual vehicle, passenger and terminal layers can be shown or hidden.

Authorized Chrome inspection on the rebuilt Docker release found **96 model elements** (20 simulated vehicles, four terminals and 72 family groups), with no map warnings. The actual Google renderer visibly displayed upright aircraft, buses, terminal glazing/roof details and family figures. Selecting B12 through the resource panel focused the bus and showed its unavailable status and red ring. `google-models-live.jpg` records the revised CRA commercial-apron registration and visible geometry, with Google attribution. The screenshot excludes voice content and passenger access links. The registration is an authored presentation layout, not a surveyed terminal replica; some photographic vegetation/buildings can still overlap synthetic objects.

## Publication verification

The release prepared for the GitHub repository passed **149 tests: 104 backend and 45 frontend**. Backend tests explicitly blank provider credentials before collection, use an isolated temporary database for the default app, and block DNS/TCP connections. Real configured developer credentials cannot be used accidentally by the test suite. CI installs pinned test dependencies and applies job timeouts.

New request-boundary checks reject malformed or oversized voice offers and client model overrides before provider initialization. Validation errors do not echo submitted tokens, SDP or unknown property names; nonfinite numeric input produces a JSON 422 response instead of a serialization error. Frontend regressions keep the displayed diversion airport and submitted airport synchronized after live availability changes.

Strict TypeScript, the production Vite build and frontend formatting passed. Both Docker images rebuilt successfully, and both services became healthy. The full world snapshot hash and version (25) matched before and after restart; no scenario reset was performed. The geographic HUD was adjusted to clear the legend and Google compass. Chrome inspection of the rebuilt release confirmed real geographic imagery, source-labeled overlays, visible Google attribution and separated HUD/compass controls; see `release-publication.jpg`. The current user's AI-selected plan was preserved; this check did not create another plan or reset the scenario.

The publication candidate scan found no configured provider credentials or recognizable API-key/private-key patterns. Environment files, databases and private Cloud Billing screenshots remain excluded from Git. Application screenshots, original scene assets and generated design references are included with their provenance.

## Historical executed checks

The following **129 passing tests (95 backend + 34 frontend)** and build/container results are historical evidence from the earlier verification runs; the publication verification above supersedes their test counts.

- **95 backend tests passed**, including concurrency, idempotency, atomic multi-leg acceptance, cascaded disruptions, capacity preservation, complete destinations, multi-day accommodation, randomized 180–300 passenger cases, auth and passenger isolation.
- **34 frontend tests passed**: request recovery, GPT-Live resource cleanup/delegation, Google overlay provenance, model descriptors and asynchronous cleanup.
- Strict TypeScript, including unused declarations/parameters; production Vite build; Prettier check.
- Docker images built with Python 3.12 and Node 22. FastAPI runs non-root with a persistent volume; Caddy serves the production frontend and proxies the API. `/api/ready` returned ready with a connected database.
- Local and production Compose configurations parsed successfully. HTTPS deployment on a public domain has not been performed.
- SQLite backup integrity, private file creation, no-overwrite behavior and observation-cache exclusion covered by packaging tests.
- Raw FR24 observations are excluded from portable audit exports and persistent idempotency snapshots. Configurable cache expiry runs even while the feed is paused.

## Browser → API → durable state → passenger

The complete circuit was exercised in both Vite development and the Docker-served release at `http://localhost:8080`.

1. Calculate the initial recovery: 120 passengers, 48 complete party itineraries, three candidates, selected cost €16,178. This is the explicitly labeled local solver, not a model response.
2. Open G001 through its actual QR/link. The four-person accessible party received B12 CRA→OTP followed by F211 OTP→LTN, total €540. Accepting on the passenger page confirmed every segment. The operator received the same version through SSE.
3. Withdraw B12 through the interface and replan. The old confirmed journey was invalidated; G001 automatically received a new direct F110 offer, total €720, which was accepted with its own idempotent request.
4. Restart the actual API container. The scenario, version and confirmed offer survived unchanged. Four confirmed passengers remained confirmed. See `container-report.json`.
5. Close CRA through the interface. Six candidates compared OTP/SBZ diversion alternatives. The selected bounded plan covered 140 of 180 passengers and explicitly left 26 groups/40 passengers unresolved. No impossible solution was fabricated.
6. In development, edit only G002 accessibility from its passenger page. The next-day deadline remained **13:00 +1d (2220 minutes)** on the authoritative operator screen.
7. Reset the exported test scenario to a fresh version. The old passenger URL then displayed “Passenger link not found”, confirming access revocation. This describes the end of that historical test, not the current world state.
8. At that time, unconfigured natural-language commands returned an explicit provider-access error without changing operational state. Geographic view without a key displayed its configuration error; it did not load substitute imagery. Credentials and subsequent real-provider checks are recorded below.

The phone layout was inspected at a 390×844 viewport and captured through the release tab. Desktop inspection used the browser's normal 1280×720 viewport.

## Continuation audit and corrections

The updated Docker release passed the targeted HTTP checks recorded in `release-audit.json`: replaying an acceptance with a reset-revoked token returns 404; a repeated diversion to the same airport preserves a confirmed transfer. The release was reset to clean version 18 afterward as part of that historical verification. The current user scenario was left untouched during this documentation update.

Fifteen additional backend regressions cover passenger authorization before cached replay, acceptance/expiry races, repeated and impossible flight diversions, return to the original arrival airport, and durable in-flight request claims across workers. Concurrent copies of one AI request now share one provider operation; a pending copy receives 503 with `applied:null` and preserves its request ID for recovery. Upstream exactly-once billing after a timeout is not guaranteed.

Frontend corrections bound voice HTTP/SDP startup to 60 seconds with microphone cleanup, preserve Unicode within the voice context limit, and catch both synchronous and asynchronous Google camera failures. The initial recovery button fits without scrolling at 1280×720; resource selection and return to overview were exercised with no browser errors in the fresh development tab.

The native airport now uses materials at a physical scale, outdoor reflections, clearer glazing and contact shadows. Compare `scene-before-refinement.jpg` and `scene-after-refinement.jpg`; `release-refined-airport.jpg` shows the actual Docker-served result. No mockup raster was substituted for the interactive scene.

Google documents worldwide 3D terrain but limits 3D buildings and vegetation to its mapped coverage areas. Exact terminal surface coverage was not established from the public documentation. A valid browser key and airport photogrammetry are independent requirements; a successful map render does not prove detailed terminal geometry. [Official coverage documentation](https://developers.google.com/maps/documentation/javascript/3d/coverage)

## Authorized real-provider checks

[openai-live-check.json](openai-live-check.json) records a bounded check on 31 October 2026 at 10:48–10:49 UTC against `https://api.openai.com`. Authentication and the model-list request succeeded. Sol, Luna and GPT-Live were listed; listing GPT-Live does not verify a voice session.

- **Sol Ultrafast interpretation passed:** the real Responses call reported `gpt-6.1-sol` and `ultrafast`. A visual-focus request over one synthetic group and four resources returned the expected focus, zero events and zero strategies; schema and entity-reference validation passed. Measured latency: **3,178.2 ms**.
- **Decisions selection passed:** the real `gpt-6-luna` call selected from two locally validated candidates and chose the earlier arrival. The selected candidate covered four passengers for €720, arriving at minute 1080. Measured latency: **858.6 ms**.
- These were bounded account/model checks. **No microphone session was opened and no operational world mutation was performed.** They are individual latency samples, not a full-scenario benchmark or end-to-end voice verification.

[voice-live-check.json](voice-live-check.json) records a separate **single successful GPT-Live WebRTC session** using locally generated silence and the existing `VoiceProvider`. HTTP 201, the requested `gpt-live-1` model, peer connection, data channel and server sideband were confirmed. After 5.003 connected seconds the client sent `session.close`; both channels received `session.closed`, the watchdog reached `closed`, and final provider usage was confirmed as **15.0 seconds**. There were no retries, microphone access, ambient recordings, speaker playback, delegated backend work or world access. This proves one real transport/lifecycle check; it does not verify spoken commands or the end-to-end microphone experience.

The rebuilt container also returned JSON 422 for a deliberately malformed voice offer and nonfinite passenger-constraint input, without reflecting submitted sentinel values. These boundary checks did not open provider sessions or mutate the world.

A Google Maps API key was configured with the account owner's authorization and stored in the ignored private `backend/.env`, whose permissions were checked as `0600`. `/api/config` subsequently reported `ai_configured:true`, `voice_configured:true` and `fr24_configured:false`. Google configuration is a derived nonempty-key status, not a separate boolean API field. No credential values are included in this report or supplied by the repository.

The initial real Chrome **Geographic 3D** attempt failed with the console error **`Google Maps BillingNotEnabledMapError`**. Maps JavaScript API is enabled, and the current key has no restrictions. Neither billing settings nor key restrictions had been changed at the time of that attempt. The subsequent billing upgrade and first successful render are recorded below; detailed terminal photogrammetry, GLB alignment and visual fidelity remain unverified. Key restrictions are also still an outstanding deployment check.

Google Cloud billing was activated with the account owner's explicit authorization of usage-based billing without an automatic spending cap. Google confirmed a successful account upgrade. No budget cap or billing alert was configured, and existing key restrictions and credentials were left unchanged. Private account-console screenshots, project identifiers and payment details are excluded from the public repository.

After activation, Chrome **Geographic 3D successfully rendered real Google satellite imagery and terrain at CRA**, together with labeled simulation routes and markers. The visually inspected result is captured in `google-geographic-live.jpg`. This validates a first real geographic render; detailed terminal photogrammetry and GLB alignment remain unverified. No operational scenario changes were made during the check. The FR24 feed remained off with zero observations.

The account appeared active after the upgrade, while an account-level billing warning remained under review. **Billing health is not confirmed** by the successful map render; account administration remains the owner's responsibility.

The live failure exposed an initialization-order bug: an overlay error could hide the provider failure. Maps overlays and camera effects now wait for rendering readiness; fatal initialization failures remain terminal despite late readiness events. Overlay diagnostics use fixed stage/type labels without raw provider messages or credential-bearing URLs. **35 targeted Maps/voice tests passed**, including eight new ordering, cleanup and sanitization tests; strict TypeScript and changed-file Prettier checks passed. The web Docker image rebuilt successfully and only the web container was recreated for this correction. These checks supplement the historical full-suite results above; the backend suite was not rerun for this frontend-only change.

Before the billing upgrade, a fresh Chrome attempt on the corrected release again logged `BillingNotEnabledMapError` at **2026-10-31T11:05:35.295Z**. The UI correctly reported that no rendered scene was confirmed, with billing/API/key guidance, instead of the misleading overlay error. It did not infer the exact billing cause from callbacks that do not supply it. The current scenario was not reset; the app was returned to its simulation view.

The user plans to supply FR24 access later. No successful real FR24 observation is claimed.

## Timing evidence

`../benchmark-results.json` contains 72 raw deterministic samples on Apple M1 Max/Python 3.12.16. Median/p95 server time: about **40/47 ms** for 180 passengers initially and **166/172 ms** for 300 passengers with CRA closed. These exclude OpenAI calls, audio, browser/network time and rendering.

The container SSE snapshot crossed Caddy in 11.68 ms in one local observation. The measured solver/application stages and restart result are in `container-report.json`; a single sample is not a production latency guarantee. The UI reports measured decisions, separate mode samples and overlapping stage timings without adding them together.

## Visual evidence

- `release-publication.jpg`: rebuilt publication release with real geographic imagery and the preserved user's scenario.
- `release-airport.jpg`: actual running release with calculated recovery.
- `release-passenger-mobile.jpg`: actual passenger confirmation at phone width.
- `release-bus-impact.jpg`: current consequences of the bus failure after the airport closure (32 initially affected, 31 with replacement offers, 1 unresolved at capture).
- `release-regional-diversion.jpg`: six evaluated diversion strategies and 140 passengers covered.
- `cascade-session.json`: exported operating snapshot after natural offer expiry, with passenger access tokens and raw observations verified absent. Versions intentionally differ between snapshots.
- `google-map-provider-error.jpg`: corrected release showing the actual unsuccessful geographic initialization.
- `google-geographic-live.jpg`: first successful Chrome render of real Google satellite imagery/terrain at CRA with labeled simulation routes and markers.
- `../mockups/*.png` are generated design references, not screenshots of implemented software.

See [evidence publication notes](README.md) for the boundary between public application evidence and private account-console records.

The local Three.js scene is an interactive architectural illustration with original geometry and CC0 surface materials. It is **not a photographic match** to the reference concepts. Google's real satellite imagery and terrain at CRA have now rendered with source-labeled simulation overlays. Authored GLBs now render upright on Google terrain with the revised CRA apron registration. Surveyed terminal photogrammetry and fidelity to the visual references remain unverified.

## Remaining acceptance gates

- Real GPT-Live microphone session, delegation, playback, interruption and final session close/usage.
- Authorized FR24 observation with original timestamp, account credits and agreed usage/retention; access will be supplied later by the user.
- Determine the cause of Google's billing warning and confirm billing health; configure appropriate key restrictions.
- Verify surveyed terminal photogrammetry and precise alignment against current airport data; authored GLB rendering/orientation now has browser evidence.
- Review visual fidelity on the event display.
- Production host/domain, account budgets and external deployment, if the team chooses to publish.

OpenAI and Google credentials are now configured. The bounded real OpenAI checks above close the interpretation/selection access gate; a configured voice credential still requires an actual microphone session. Google confirmed the billing upgrade and a first real geographic render succeeded, while billing health and detailed geometry remain open. FR24 access is pending. Historical unit tests use explicitly mocked provider transports and do not substitute for these remaining checks.

## Non-failing tool notices

The backend test client emits an upstream Starlette deprecation notice for httpx. The lazy Three.js scene chunk exceeds Vite's configured 900 KB raw threshold (about 265 KB gzip); the passenger screen does not load the 3D scene. These notices did not fail the build. Long-term deployment limits, including heuristic optimality, single-role authentication and audit-storage growth, are recorded in `../backend.md`.

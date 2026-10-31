# Isolated spoken voice check

This is a developer-only page, separate from the production application entry and build. It has been checked offline. **A real spoken end-to-end provider run has not been performed with this harness.**

It imports the existing `LiveVoiceClient` and command-result helpers. A page-local `getUserMedia` override returns a Web Audio stream containing the prerecorded synthetic speech below. The actual microphone capture function is never called. GPT-Live receives the audio over its normal WebRTC media track; no transcript, delegation event, provider response or decision is fabricated.

## Bounds and isolation

- No provider call occurs until the operator checks the consent box and presses **Start one spoken check**.
- The page accepts only `http://127.0.0.1:5177`; its dedicated Vite proxy targets `http://127.0.0.1:18765`.
- Before each start, the API must report development/test mode, that exact public origin, the three requested model names, configured AI/voice, and a fresh `voice-check-…` simulation with 1–20 groups, no offers/reservations/decisions/observations and no active traffic polling. The initial budget must differ from €20,000.
- Each run reserves one backend `/commands` call, even if it fails. No automatic retry or solver fallback exists. At most two manual starts are allowed per page load; the second requires a fresh isolated scenario and another preflight. Reloading the page resets that local count, so it is not an account-wide spending control.
- Client closure is requested after 30 seconds from `session.started`, or 35 seconds from beginning the voice connection, whichever is earlier. The separate API **must** set `VOICE_MAX_SECONDS=35` for its server watchdog. Finalization has an additional 15-second grace period. A missing `session.closed` remains explicitly unconfirmed.
- An already submitted backend command can finish after voice closure; closing voice does not roll back backend work. Read the isolated world before retrying. Never attach this harness to the normal application database.

## Prepare the separate API

Use the existing backend application factory with a **new temporary database path**, bound only to `127.0.0.1:18765`. Set the database override **before importing `app.main`**, since that module also constructs a default application. Do not launch it with the ordinary development database.

Required settings:

```text
ENVIRONMENT=test
OPERATION_MODE=simulation
DATABASE_PATH=<new temporary directory>/voice-check.sqlite3
PUBLIC_BASE_URL=http://127.0.0.1:5177
CORS_ORIGINS=http://127.0.0.1:5177
ALLOWED_HOSTS=127.0.0.1,localhost
VOICE_MAX_SECONDS=35
OPENAI_MODEL=gpt-6.1-sol
OPENAI_DECISION_MODEL=gpt-6-luna
OPENAI_VOICE_MODEL=gpt-live-1
OPENAI_SERVICE_TIER=ultrafast
FR24_API_KEY=
GOOGLE_MAPS_API_KEY=
```

Load the already-authorized OpenAI key on this API server only, using a trusted private environment source. Do not put it in frontend code, a URL, a terminal command argument, an evidence export or Git. The harness neither loads credentials nor starts the server.

Seed that temporary database from `seed_world()` with:

- `scenario_id = "voice-check-20261031"` (or another `voice-check-` identifier).
- Only the first four waiting groups: 10 synthetic passengers.
- Budget €45,000; empty offers, reservations, decisions and observations.
- Existing seeded airports and resource inventory, with polling stopped.

The config endpoint exposes model names but not the service tier or watchdog setting; those two settings must be verified on the isolated server. The browser evidence labels them as requested/configured, not independently provider-verified.

## Run the page manually

From `frontend`, after the separate API is ready:

```sh
npm exec -- vite --config vite.config.voice.ts
```

Open `http://127.0.0.1:5177/voice-check.html` in a visible browser. Check the isolated target first, tick the checkbox, then start once. The page feeds the WAV automatically 500 ms after the first listening state. Use **Enable voice playback** if browser autoplay is blocked. **Close session now** is always available during a run.

Inspect the input transcript, delegated text, actual backend budget/decision, post-result output transcript, remote-audio activity and final usage. The expected outcome is a new AI decision and budget €20,000. A successful transport or spoken acknowledgement by itself does not prove the mutation. Listen to the recorded provider-only output and confirm its meaning matches the authoritative result before marking the check passed.

The page offers a sanitized JSON evidence download. It exports no SDP, API key, session/delegation IDs, network addresses, passenger tokens or complete worlds. Remote audio is held in browser memory for review and released on navigation. Do not commit later output recordings or evidence without reviewing them. Existing synthetic input audio is safe to commit.

This checks prerecorded speech through the production voice client and backend command path. It does **not** verify a physical microphone, room noise, production authentication or the normal application's rendered controls.

## Input provenance

Text:

> În simulator, setează bugetul total la douăzeci de mii de euro și recalculează planul.

Generated locally with macOS `say` (Ioana, 170 words/minute), then converted with `afconvert` to mono 24 kHz signed 16-bit PCM WAV. Duration: 5.534708 seconds. No human recording or third-party audio download.

```sh
say -v Ioana -r 170 -o /tmp/voice-check.aiff 'În simulator, setează bugetul total la douăzeci de mii de euro și recalculează planul.'
afconvert -f WAVE -d LEI16@24000 -c 1 /tmp/voice-check.aiff verification/budget-command.wav
```

SHA-256: `20e4f6d15929ba84f1d55fa84fe006bd329f4d764acd02e6b73eb6fe6af167a8`.

## Offline verification

From `frontend`:

```sh
npm exec -- vitest run src/verification/voiceHarnessSupport.test.ts src/liveVoice.test.ts src/commandContext.test.ts
npm run typecheck
npm exec -- prettier --check src/verification voice-check.html vite.config.voice.ts verification/README.md
```

Tests cover wrong-target rejection, exact-model requirements, the one-command gate, no ambient-capture invocation, media API restoration and evidence privacy. Existing voice/context regression tests exercise the production client. These offline tests do not claim real WebRTC or spoken-provider success.

The transport, client delegation and closure follow the official OpenAI documentation: [WebRTC](https://developers.openai.com/api/docs/guides/voice-webrtc), [delegation](https://developers.openai.com/api/docs/guides/live-delegation), and [session lifecycle](https://developers.openai.com/api/docs/guides/live-conversations).

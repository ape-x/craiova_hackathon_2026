# Verification evidence

[report.md](report.md) records executed checks, bounded real-provider results and remaining acceptance gates. Test counts and screenshots describe specific historical runs; they do not imply that every later change was retested or that external accounts will remain available.

Public evidence includes historical simulation screenshots, sanitized automated checks and later bounded real-provider results. Historical simulation passengers/inventory must not be confused with the current live operation: initial passenger requirements may be planning assumptions, while recent flight, hotel and APL transfer quotes come from their recorded sources. See the dated report and sanitized `automatic-hotel-live-check.json` for the actually verified four-leg flow. Google imagery/terrain has real geographic provenance. A bounded FR24 lookup has been verified; fresh aircraft observations on the live map remain a separate gate. Images in `../mockups/` are design references rather than implemented-screen evidence.

`native-materials-qa.jpg`, `passenger-offer-choice.jpg` and `passenger-offer-declined.jpg` are actual browser captures from the isolated verification checkout. The two passenger screenshots use a 390×844 viewport and synthetic families. They establish the offer-choice and decline screens; the native scene remains an architectural illustration rather than a photographic replica.

`google-materials-qa.jpg` verifies the regenerated terminal in the real Google renderer on a separate test API. It contains Google attribution and synthetic operations, with no passenger links or voice content.

`integrated-google-live.jpg` is the deployed Docker release after integration. It preserves the existing airport closure and B12 incident at world version 30; provider controls are ready, and the expired earlier plan is explicitly marked for refresh. The full frame retains Google attribution and contains no passenger access links or voice transcript.

Private Google Cloud billing-console captures remain local and are excluded by Git and Docker ignore rules. The public report summarizes the outcome without publishing account identifiers, payment details or links to those private files. Provider secrets, private environment files, databases and backups must also remain outside the repository. The browser Maps key is public at runtime and requires referrer/API restrictions; its presence is not permission to publish account-console records.

Earlier evidence establishes bounded real Sol Ultrafast interpretation, Decisions selection, GPT-Live transport/closure and Google geographic rendering at CRA. Later typed source-backed recovery succeeded; the latest synthesized spoken-profile test failed ASR before backend dispatch, as documented in `hotel-voice-live-check.json`. Do not treat the earlier silence test or the successful typed flow as spoken end-to-end acceptance. Real user-microphone/delegation/playback tests, FR24 observations, detailed terminal photogrammetry, precise surveyed alignment and event-display visual fidelity remain open. The authored terminal, fleet and family GLBs now have live upright-rendering evidence in `google-models-live.jpg`. Billing health and deployment-specific access must be checked by the account owner.

- `integration-release-check.json`: integrated release tests and isolated browser flows; no external provider calls.
- `integration-simulation.jpg`: built application on a separate synthetic QA database, local solver selected 120 passengers.
- `integration-live-acceptance.jpg`: isolated QA passenger preference awaiting supplier bookings; the coach is a named test fixture and no supplier transaction occurred.

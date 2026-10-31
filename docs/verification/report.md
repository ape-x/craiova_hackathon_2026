# Implementation verification — 1 November 2026 (Europe/Bucharest)

This report separates executed tests, bounded real-provider checks and remaining acceptance gates. The current product is a live assisted recovery application. Initial passenger requirements may be explicit planning assumptions, but live supply comes from real quotes or documented operator services. Earlier simulation records below are historical test evidence, not current live inventory or supplier bookings.

## Current live assisted release — 1 November 2026

API and frontend `51a0641` are deployed to the sole active application at `http://localhost:8080`. Manual source-backed APL airport–hotel transfers in both directions were verified on API `aad839e` / frontend `8781b5c`, preserving exact supplier GBP, explicit ECB EUR estimates and dated Google driving evidence. A return binds one sourced onward flight, the reported airport drop-off and an explicit operator processing allowance; its departure terminal remains unknown. The two manual imports and onward flight advanced world 44 → 47, with zero confirmed resources/offers and no changes to either passenger group.

The subsequent automatic hotel workflow (`b3afa9e`) and scope-bound reusable input profile (`95a0606`) have passed **1,252 backend tests and 273 frontend tests**, strict TypeScript, the production build and formatting. The automatic core passed GitHub Actions on both branches (runs 84/85); the full UI release passed both branch runs 88/89. Actual source-backed automatic acceptance is recorded below. Backend tests include required hotel stays, counted source calls, independent connection/budget validation, chosen-only atomic application, immutable out-of-scope bookings, profile identity before both transfer fingerprints, exact voice reuse and bounded call allowances. Focused counts overlap the full suite and are not added to it. One initial CI run on `95a0606` failed only because the read-derived live minute advanced during a whole-world test comparison. `ca7eea7` excludes that display clock while retaining every operational field and version assertion; both full branch runs 90/91 passed. Application timing and expiry logic were unchanged.

The scoped voice startup release `e10fa81` passed **1,281 backend tests and 282 frontend tests**, strict TypeScript/production build and formatting; GitHub Actions passed on both branches (main 96, codex 97). Both Docker images were built from the exact committed archive. The API healthcheck and routed readiness passed; the web container was running and served the refreshed application. Post-restart comparison preserved every operational field, version 49, both groups, 24 resources and the saved profile; only read-derived `sim_time` changed. No job was active at restart. Browser inspection retained the completed ten-call hotel result, honest quote expiry, original GBP/ECB estimates and sourced Google road. No voice session or supplier refresh was started for these deployment checks. Voice recognition remains the separate acceptance gap below.

### Romanian dated coach fares with unknown availability

The new CRA → OTP consumer-fare adapter and existing-app UI passed **1,592 backend tests and 375 frontend tests**, strict TypeScript, production build and formatting. Provider tests use sanitized source-shaped HTML and mocked HTTP; authenticated API tests prove unchanged world state and no booking endpoint. Automatic-job tests cover counted BILETERIA/ECB calls, exact-party deduplication, budget exhaustion, independent flight/Decisions preservation, failed/unknown read-only source steps, cancellation, scope drift, durable replay and exclusion from inventory. Frontend tests cover exact date/party response matching, late-response suppression, no automatic retry, original RON and currency-qualified ECB estimates, unknown seats/expiry and historical reference display in blocked or completed jobs.

A fresh unauthenticated GET of the public dated search returned 200 and 173,184 HTML bytes. Offline parsing of that actual response produced **14 direct fares at RON 190 for two adults**, with one connecting row excluded. The late 22:10 service retained the explicitly displayed next-day 01:10 arrival. This captured-source check establishes parser compatibility; it does not by itself verify the deployed UI/API, seat inventory or an automatic live recovery job. The production-path check is recorded after deployment below.

The adapter sends no checkout request and does not return checkout links. Source markup does not independently map checkout stop-occurrence IDs to station IDs; only search/header/visible-stop and date/party consistency is verified. The resulting public fares remain reference observations, not validated checkout itineraries. No capacity, supplier expiry, UTC offset, booking or recovered-passenger count is invented. Original RON and optional EUR estimates remain outside the allocation budget. The completed-flight UI distinguishes these optional reference limitations from unmet mandatory journey requirements; partial and blocked jobs retain visible requirements.

### Bounded active inventory and immutable source history

Release `2771e37` passed **1,478 backend tests and 355 frontend tests**, strict TypeScript, production build and formatting. GitHub Actions passed the exact commit on both branches (main 110, codex 111). Repeated flight, hotel and transfer imports retire superseded or safely removable stale resources into a transactional immutable SQLite archive. Geographic snapshots, original quote facts and group associations remain available after active resource/location limits require cleanup. Existing bookings, active proposals, source dependencies and operational references remain protected; genuinely full protected inventory yields an explicit capacity gap. Offline regressions include more than 150 lifetime revisions and repeated full four-leg jobs at the real resource/location limits, atomic rollback, replay, collision handling and private passenger-history access.

Both images were built from the exact committed archive and deployed to the existing port 8080. A verified private SQLite backup matched before restart; no job was active. API health/readiness passed and all operational fields were preserved: world 49, two groups, 24 resources, zero bookings. Only the read-derived clock changed. The production database contains the archive tables and six immutability triggers; no production resource needed archiving during this check (zero archive rows). Actual archive/compaction behavior is covered by offline database/API tests, not a new supplier run.

The existing browser tab served `/assets/index-CsCbwfTd.js` with no console errors. Opening **View source** in the historical EK5 result read the recorded APL transfer, original **GBP 61.00**, explicit **approximately EUR 73.16** at the dated ECB reference, route, schedule and honest unknown supplier expiry. This GET neither refreshed supply nor restored an expired service. The archive-only UI and token-scoped passenger history were tested offline; this browser check used an existing active-inventory record.

### Operator-report voice probe — one authorized attempt on the current release

At **23:07 UTC on 31 October** (1 November locally), the user-authorized single synthesized Romanian probe targeted the existing EK5 case and saved hotel/transfer/flight configuration on `2771e37`. Sixteen offline guard tests passed before refreshing the read-only preflight. Startup used the same selected-case context builder as the browser. The locally generated utterance lasted **15.104 seconds**; authorization allowed at most 45 voice seconds and 11 backend calls. No microphone, speakers or additional application server were used.

**The airport was not recognized correctly:** `LHR` became **“Le Havre”** in both the delegated and final transcript. The private probe guard rejected the geography before any command POST. The delegated fragment also lacked the final word “total,” which arrived later; the eleven-call amount was already present. The later fragment did not correct the airport. No transcript was repaired, no wrong airport was silently mapped to Heathrow, and no retry occurred.

Both closure channels confirmed **17.0 seconds** of final usage. There were **zero backend interpretation calls, zero supplier calls, zero new jobs and zero bookings**. A read-only comparison preserved every operational field, world 49 and the complete job history. There was no backend receipt, spoken acknowledgement or new itinerary; the existing successful four-leg result in the UI is historical. This run tests the Python client with real transport and scoped startup context, not browser microphone behavior or a successful spoken recovery.

[Sanitized measured evidence](report-voice-live-check.json) records the failed recognition and confirmed consumption separately. The immutable private attempt lock remains consumed. A future independently authorized probe should say the full airport name and retain exact geographic validation; this failed result does not justify changing the recognized location or claiming better accuracy.

Offline investigation reproduced a separate production-parser omission hazard: if interpretation omitted the arrival report, feminine Romanian “pregătite” was not recognized as an unresolved operational report, and the saved-profile time guard missed “treizeci de minute.” The correction recognizes all four Romanian readiness forms and shares existing 30/60-minute number words with duration-edit detection, including optional “de.” It leaves the spending-number vocabulary and literal airport checks unchanged. The exact final ASR is covered with a mocked omitted report and three contradictory/invented Heathrow references: no report, job or operational mutation is allowed, and durable replay after handler recreation does not interpret again. A subsequent ordinary saved-hotel request still queues with ten remaining calls. Independent saved-hotel timing-edit cases also require clarification. The original code reproduced ten failing cases; all **241 focused offline tests** and the full **1,492-test backend suite** passed after correction. The frontend is unchanged from its verified 355-test release. These mocked checks do not turn the failed paid voice probe into a successful spoken result.

GitHub Actions passed the exact correction on both branches (main 113, codex 112). Correction `51a0641` was built into both Docker images from the exact committed archive and deployed to the same application on port 8080. A fresh private backup passed SQLite integrity checking; no job was active before restart. API health/readiness passed and every operational field remained unchanged at world 49. Browser reload preserved the existing historical EK5 result and source controls with no console errors. No new voice or command/provider request was submitted to verify this deployment.

### Live voice/text reports with scoped automatic recovery

Release `cc063dc`, followed by test-fixture correction `ecc9d8b`, connects one explicit passenger-arrival or service-unavailability report to optional automatic recovery. Report, queued job and original command receipt commit together; unexpected database/queue failures roll them back together. Known prerequisites instead produce a truthful recorded-report/search-not-started result. Exact case/group/service resolution rejects ambiguous shared names, mismatched predicates, hidden additional requirements and negated or hypothetical reports. The existing interpretation and total call allowance are durable across retries. Relative readiness such as “in 30 minutes” uses the original command's persisted server timestamp, rounded up to a UTC minute, and records that basis; replay never moves it forward.

The full suite passed **1,419 backend tests and 327 frontend tests**, strict TypeScript, production frontend build and formatting. Both exact-release GitHub Actions runs passed (main 106, codex 107); the initial feature commit also passed both branches (104/105). A repeated full run exposed an older fixture that read the clock independently for each passenger, occasionally generating conflicting group timestamps across a second boundary. The fixture now captures one shared timestamp, with an advancing-clock regression; application validation was correct and unchanged. Existing Starlette deprecation and internal-scene bundle-size warnings remain non-blocking.

Three socket-guarded HTTP/worker acceptance tests cover arrival → scoped replacement flight, an unavailable service → scoped replacement flight, and a supplier timeout → durable unknown outcome without another request. They verify untouched out-of-scope groups/proposals/reservations, exact four-call accounting including interpretation, and replay after completion or handler recreation. These use fake AI/source adapters and establish no new paid-source or spoken-recognition result. Frontend tests verify concise acknowledgements of recorded reports, queued/blocked searches and historical receipts without claiming a booking or current completion.

Both Docker images were built from the exact `ecc9d8b` committed archive. The verified private SQLite backup and pre-restart read matched; no job was active. After restart, API health and routed readiness passed, the web container served the new bundle, and every operational field remained identical: world 49, two groups, 24 resources and zero bookings. Only the read-derived clock changed. Browser inspection showed the preserved EK5 case and historical ten-call hotel result, honest expired quotes and the new frontend bundle, with no console errors. No report, supplier refresh or voice session was submitted for deployment verification. At this deployment stage no new spoken acceptance run had been performed. The separately authorized current-release attempt above subsequently failed airport recognition before reaching the bridge.

### Scoped operator reports and rolling planning dates

Release `d0b46c3` passed **1,338 backend tests and 314 frontend tests**, strict TypeScript, production build and formatting. GitHub Actions passed for the exact commit on both branches (main 101, codex 100). The existing non-blocking Starlette deprecation and internal-scene bundle-size warnings remain. Both Docker images were built from that exact committed archive and deployed to the existing application; no second application server was started. API health and routed readiness passed, with the web container running. A verified private SQLite snapshot preceded restart. Every operational field was preserved, including version 49, two groups, 24 resources and zero bookings; only the read-derived clock changed. No recovery job was active at restart.

The browser rendered the new case arrival-report form with explicit group selection, airport and UTC readiness, and the resource unavailability dialog with its exact service and affected-proposal summary. The latter was opened from the existing APL return resource and correctly showed zero currently dependent proposals after earlier quote expiry. No arrival or breakdown was asserted merely for verification: these forms were inspected without submission, and production report mutation is not claimed as a live end-to-end result. Offline API/UI tests cover scoped mutation, source-history retention, confirmed-booking protection, stale versions, duplicate requests and recovery of unknown outcomes. These typed forms do not search suppliers or replan automatically; the voice/text bridge was added in the later release above.

Other fixes preserve existing proposals when unrelated data is imported, retain a stable epoch while allowing live plans up to seven days from the current server clock, and replace the live passenger deadline selector with an explicit UTC date. The two-week-old-operation regression exercises imports and a complete ten-call hotel workflow using fixtures, not newly purchased source results. Voice correction guidance now asks for a complete corrected command instead of an underspecified reply. This release does not establish a successful new voice recognition result. No new OpenAI session, supplier search, booking, payment or dispatch was made for its deployment verification. Historical resource/location accumulation limits and actual Romanian ground-transport quote coverage remain separate gaps.

### Complete automatic hotel recovery — real source run

The actual case form requested one complete journey for the existing two-adult planning group: **1 November pickup 13:30 UTC, hotel check-in 15:00 UTC, 2 November checkout 06:30 UTC**, one room, two suitcases, minimum rest 480 minutes, Heathrow as the onward airport and a declared 120-minute airport-processing allowance. The group's **EUR 1,000 budget** and **2 November 20:00 UTC deadline** were unchanged. Public T5 short-stay coordinates were explicit initial/drop-off assumptions, not verified passenger positions or terminal assignments.

The real job completed from **21:19:31.745 to 21:19:41.240 UTC**: **9.495 seconds queue-to-application**, with **9.16 seconds** displayed server processing. Exactly **10 of 10 allowed calls** completed: Responses query selection, Nuitee flights, LiteAPI room rates and exact hotel metadata, two APL quotes, two Google driving predictions, ECB and Decisions. The real Decisions selection took approximately **0.99 seconds**. These are single observations, not latency guarantees. No model response or source result was substituted by a preset.

The selected four-resource proposal was:

| Segment | Source result | Planning cost |
| --- | --- | --- |
| Initial airport point → hotel | APL Saloon, pickup 13:30 UTC; Google 3,103 seconds / 29,520 m, rounded arrival 14:22 UTC | GBP 61; approximately EUR 73.16 |
| Park Avenue Bayswater Inn Hyde Park | LiteAPI, one room for two adults, full requested 15:00–06:30 stay validated against supplier policy | EUR 73.00 |
| Hotel → declared Heathrow drop-off | APL Saloon, pickup 06:40 UTC; Google 2,044 seconds / 27,725 m, rounded arrival 07:15 UTC | GBP 57; approximately EUR 68.36 |
| LHR → AMS → OTP | KLM 1004 at 10:00 UTC, then KLM 1375; final arrival 15:00 UTC | EUR 720.34 |

Total: **approximately EUR 934.86**, including **GBP 118** valued at approximately **EUR 141.52** using the **30 October ECB reference of 0.83380 GBP per EUR**. The UI explicitly displayed an estimated plan cost and warned that EUR settlement is not guaranteed. The return allowed 165 minutes from predicted road arrival until departure, including the operator's 120-minute buffer; departure-terminal and pedestrian access remained unverified.

The operation advanced **world 47 → 48**, with exactly four new chosen resources and one complete proposal for **two passengers, zero unresolved within the selected scope**. Every stored field of the other group remained unchanged. Only the selected group's status and provenance changed; the full requested hotel requirement and exact reusable profile were saved. No supplier booking, payment, hold or driver dispatch occurred. Existing superseded quotes for this group are not new available supply.

The actual browser showed all ten completed source/decision steps, source offer counts, the selected property and return pickup, and the active complete proposal. **Review saved inputs** then preserved the exact UTC times, rooms, luggage, coordinates and processing allowance, cleared both point confirmations and retained the cap of ten, without another supplier request. Normal quote expiry subsequently advanced the world to 49; expired prices and proposals were no longer presented as current availability. The sourced road remained separately labelled as a dated prediction.

[Sanitized source and timing evidence](automatic-hotel-live-check.json) excludes raw supplier payloads, private case/group/offer identifiers, account details and access tokens. This verifies the typed browser → actual providers → Decisions → atomic four-leg proposal → saved-input-review path. Spoken-profile reuse and physical browser microphone/audio acceptance remain separate gates.

### Real sourced return and onward flight

The first bounded Nuitee flight search for **2 November, LHR → OTP, two adults** returned ten prices whose arrivals all fell after the selected group's **2 November 20:00 UTC** deadline. No quote was imported and the deadline remained unchanged. Inspection found that the normalizer selected the cheapest ten before checking the group's time window. Release `aad839e` moves exact readiness/deadline filtering before that shortlist, across the supported supplier normalizers. Its CI passed **1,128 backend tests**; both branch runs 80/81 passed.

One corrected real search returned ten offers inside the existing window. The imported **KLM 1004 plus connection**, **2 November 10:00–15:00 UTC**, cost **EUR 720.34 for two adults** and advanced world 45 → 46. This was a supplier quote import, not a booking. The resource label now shows the verified flight number and connection count rather than a long opaque identifier.

One return search received at **20:57:52 UTC** requested **2 November 06:40 UTC**, two adults and two suitcases, from **Park Plaza London, Park Royal** to the declared public Heathrow Terminal 5 short-stay drop-off reference. The operator supplied **120 minutes** for airport processing against the exact 10:00 UTC onward departure. This declared point and buffer do not verify passenger location, terminal assignment, pedestrian access or guaranteed boarding.

APL returned six quotes; the selected Saloon cost **GBP 77.00**, displayed as **approximately EUR 92.35**, using the **30 October ECB reference of 1 EUR = 0.83380 GBP**. Google Routes returned **1,911 seconds**, **30,548 metres**, an encoded road polyline, endpoint offsets **5.7 m / 4.8 m**, and toll/highway warnings. Operational arrival rounds upward to **07:12 UTC**, leaving 48 minutes after the declared 120-minute processing allowance before departure. Both source receipt timestamps remain distinct and jointly bound the local 120-second planning window; neither source supplies a booking hold.

Importing that one Saloon advanced **world 46 → 47**. Comparison with the world-45 snapshot showed exactly two added resources (flight and return), no added nodes, both groups unchanged, all offers unchanged, and **zero confirmed resources/offers**. No booking, payment, hold or driver dispatch occurred. The earlier outbound planning resource was preserved.

Actual browser inspection of the return in Network mode showed one sourced Google road polyline, seven reference/endpoint markers and no operational vehicle/passenger/terminal models. The inspector preserved original GBP, approximate EUR, independent road timestamp and honest **Unbooked · Quote stale** status after expiry. Release `8781b5c` also qualifies APL's generic airport-arrivals meeting guidance: it does not verify hotel pickup instructions or the flight's unknown terminal. Both branch CI runs 82/83 passed. A private world-47 SQLite backup passed integrity checking and contained no transient transfer-quote cache. This manual return check alone does not prove a complete hotel-stay recovery.

### Real private-transfer planning import

One actual browser search on `0478865`, received at **20:09:31 UTC**, requested **1 November 2026 at 14:00 UTC**, **two passengers and two suitcases**, from an explicitly assumed initial point at Heathrow Terminal 5 short-stay parking to the existing sourced **Park Plaza London, Park Royal** property. The selected public pickup reference is not a verified passenger location or pickup-access guarantee. Fresh LiteAPI metadata supplied the destination **628 Western Avenue, London** and property coordinates.

APL returned six vehicle options. The operator selected the **GBP 77.00 Saloon**, displayed as **approximately EUR 92.35** using the **30 October ECB reference, 1 EUR = 0.83380 GBP**. Google Routes returned a prediction for the exact future departure: **2,301 seconds**, **32,383 metres**, with an encoded road polyline. The UI showed **38.4 predicted driving minutes**, pickup/property road offsets of **4.8 m / 5.1 m**, and the highway warning. The operational arrival rounds upward to 39 minutes; this is an estimate, not a guaranteed arrival or an inferred walking connection.

Clicking **Add planning leg** created exactly one meeting-point node and one APL resource, advancing **world 44 → 45**. A read-only comparison with the pre-release backup confirmed both groups unchanged and all existing offers unchanged after normalizing the new optional schema fields. The one search receipt was complete. There were **zero confirmed resources and zero confirmed offers**; no booking, supplier hold or driver dispatch occurred. Only the chosen Saloon was labelled **Planning leg added**; other quote options were labelled **Not selected**.

The imported resource records the requested party only, exact original GBP, typed ECB planning evidence, source road evidence and the explicit initial-position assumption. Supplier quote time/expiry remain unknown. The local 120-second receipt window governs new planning and does not represent a supplier hold. This verifies real UI → source search → reviewed import → durable state; it does **not** verify a complete hotel stay, return transfer, onward-flight connection or automatic hotel-recovery job. Existing hotel/flight quotes were not refreshed by this import.

The initial browser check exposed a map-selection and identifier-overflow defect. Releases `542d437` and `15013b1` corrected the live-world handoff, camera focus, readable endpoint names and crowded unrelated property labels. Actual browser verification then showed the stored Google road across Heathrow and London in both Airport and Network modes, the original GBP and estimated EUR in the inspector, and **Unbooked · Quote stale** beside the separate road receipt timestamp. DOM inspection found one road polyline, seven reference/endpoint/route markers and no operational vehicle, passenger or terminal models. No new quote request was issued for this display check; world version remained 45. A post-import SQLite snapshot passed integrity verification with private permissions and an empty transient transfer-quote cache.

### Real flight case and sourced recovery

The operator looked up FR24 flight **EK5, DXB→LHR**, selected the exact returned leg and created a recovery case. A new, explicitly generated planning group of two adults was linked to that case; this did not turn the group into verified passenger identities.

The real durable job ran from **18:23:47 to 18:23:59 UTC**, approximately **12.1 seconds** in this single observation. Its ledger recorded four provider calls: Responses query selection, two parallel Nuitee flight searches, and Decisions plan selection. The searches returned **20 quotes**. One validated offer was applied for **two passengers proposed, zero unresolved**. The existing group outside the selected case scope remained unchanged.

This establishes one real case-form → provider searches → validated proposal flow. It is not a latency guarantee, automatic booking, seat hold or proof of universal route coverage. Some returned alternatives were rejected by validation; those exclusions do not invalidate the successful selected proposal. The current UI separates exclusions/search outcomes, actual missing requirements and informational overnight waits.

The passenger subsequently accepted the proposal as a preference. The quote later expired normally; no supplier booking was created. This verifies the distinction between passenger acceptance and an external reservation, not continuing availability after expiry.

### Real APL transfer quotes

The configured Airport Pickups London account returned six live vehicle quotes in the actual UI for **Heathrow Terminal 5 → W1K 1LN, 2 November 2026 at 14:00 Europe/London, two passengers and two suitcases**. The amounts were **GBP 76, 88, 112, 88, 117 and 136 per vehicle**, preserving the supplier's currency and price scope. These are a dated verification snapshot, not current offers or recommendations.

The actual UI on `fe47825` preserved those six GBP prices and showed the following explicitly approximate EUR amounts using the **ECB reference dated 30 October 2026, 1 EUR = 0.83380 GBP**:

| Exact GBP per vehicle | Approximate EUR reference estimate |
| --------------------- | ---------------------------------- |
| 76.00                 | 91.15                              |
| 88.00                 | 105.54                             |
| 112.00                | 134.32                             |
| 88.00                 | 105.54                             |
| 117.00                | 140.32                             |
| 136.00                | 163.11                             |

The interface showed the source/rate date and the explicit app policy allowing up to three calendar days, with revalidation before booking and no guaranteed EUR settlement. These are comparison estimates, not supplier EUR prices or a budget allocation. Missing or stale reference data leaves the original GBP quote intact without inventing a conversion.

No booking, driver dispatch or supplier hold occurred. Supplier expiry, structured journey duration and arrival time were not provided by that APL comparison response. Those historical comparison quotes were not allocatable transport resources. The separate `0478865` planning flow above now obtains an exact dated Google road prediction and imports one scoped APL resource with explicitly estimated EUR costs.

### Live quote freshness display

The actual UI showed an old flight quote as **Validity expired** and an old hotel quote as **Quote stale**, with source time and the local **120-second** freshness cutoff labeled in **UTC**. Neutral record headings and unusable-state labels replaced ordinary availability claims. This verifies the deployed `3551f03` correction in the real resource views; it does not extend supplier validity. Recorded bookings remain distinct from quote age, and the local refresh makes no provider requests.

### Real hotel metadata and policy-validated UI import

A live Nuitee rate probe for LHR returned **ten complete hotel quotes** with real property coordinates and the coordinate-derived **Europe/London** time zone. The subsequent actual UI import requested **1–2 November 2026, two adults, one room, check-in 15:00 UTC and checkout 11:00 UTC**. It imported **nine eligible EUR quotes and nine distinct property nodes**. Each imported resource recorded `stay_times_source='supplier_policy'` and `stay_times_confirmed=false`; the operator override was not used.

One property was excluded with a displayed source-policy reason referring to its published **14:00 check-in**. This verifies the automatic property-local policy path, its exclusion display and operational import. It does not establish a room reservation or a transfer to the hotel. That historical import alone did not verify a complete sourced hotel-plus-transfer itinerary.

Selecting Park Plaza London, Park Royal from the network also moved the Google geographic 3D view to the property's sourced coordinates. The property panel identified it as a reference location and kept room availability and transport verification separate.

Google TRANSIT still has a pure adapter with offline tests and no connected product endpoint/UI or solver integration. Transit schedules do not establish seat availability; absent fares remain unknown. The general APL comparison display remains separate from the scoped initial-point → hotel import. Both manual directions supply scoped priced planning legs; the bounded automatic four-leg workflow is implemented in `b3afa9e`, with live acceptance tracked separately above.

### Real scoped text command and passenger itinerary

An actual Romanian text command named **EK5, its date, all linked groups and a maximum of four API calls**. The real receipt/ledger recorded **one interpretation plus three of three allowed job calls**, **ten Nuitee quotes**, and **two passengers proposed, zero unresolved**. The durable job ran for **7.21 seconds, excluding interpretation**; this is one observation, not an end-to-end latency guarantee. The world reached version 43, the original out-of-scope group was preserved, and supplier bookings remained zero.

The passenger UI displayed the sourced itinerary as **Swiss 317: LHR 09:00 local → ZRH 11:55 local**, a **ten-hour connection**, then **Swiss 1886: ZRH 21:55 local → OTP 01:15 local the next day**. Each clock displayed its explicit UTC offset. Source segment times reconciled with the validated plan, and the arrival summary matched the final segment.

This verifies the live text entry through the scoped command bridge, supplier searches and passenger presentation. Spoken-input evidence is recorded separately below; the successful text outcome must not be attributed to that later probe.

At the end of these checks, expired FR24 context was honestly labeled as expired. The shared feed was paused at **81 of 84 permitted requests**, with **3,215 credits reserved**; these are recorded budget counters, not a claim of continuously fresh traffic or final billed usage.

This record deliberately publishes no raw FR24 payload, private case/job/group identifier, passenger token, account contact, credential or private account screenshot. The public flight number, route, aggregate counts, times and quote snapshot above are the bounded verification evidence.

### Synthesized spoken request — partial verification

One bounded GPT-Live session on `fe47825` sent locally synthesized Romanian speech through a standalone aiortc 1.15.0 client to OpenAI using the production voice-provider configuration; its delegated command was posted to the existing application on port 8080. It exercised **audio → real delegation → one backend command → scoped queued receipt → spoken acknowledgement → confirmed closure**. The receipt matched the existing EK5 case and one linked group. Post-receipt output contained nonzero decoded audio and a transcript accurately acknowledging the recorded request and four-call allowance, without claiming an itinerary or booking. The data channel and server watchdog both confirmed final usage of **33 seconds**. No microphone, speakers, new application server, automatic retry or transcript substitution was used; voice usage is separate from the backend-call allowance.

**Exact date fidelity failed.** The intended “31 October 2026” became “30–31 October 2026”. The private probe's guard accepted the `31` substring, and the deployed backend ignored the named date while resolving the unique flight. Matching the intended existing case therefore does not establish correct recognition of the date. The final word “total” also arrived outside the delegated transcript window, although the explicit maximum of four calls was retained.

**Recovery did not complete.** The ledger recorded exactly four backend calls: one command interpretation, one query-planning call, one Nuitee search returning ten quotes, and one Decisions call. The job ended `failed` with the application classification `not_approved`; no plan was applied and the world remained version 44. The public job and step exposed the no-approval message. The executed adapter grouped explicit false, missing and malformed choices together, so this record cannot establish which occurred or invent a model rationale. Its separate `quote_not_usable` route/timing exclusion is solver evidence, not a Decisions explanation.

Subsequent safeguards were **deployed in `0478865` and verified offline**: `260dca6` rejects ambiguous, conflicting or invalid dates attached to the case/flight reference, while preserving unique flight-only references and a separate alternative-travel date; `7b5a457` distinguishes explicit Decisions rejection, refusal and malformed answers. Focused offline suites passed **67 command/date tests and 62 provider tests covering Decisions classification**. The private guard correction passed 15 offline tests and preserved the executed probe source. Those offline checks made no provider calls. The separately authorized later hotel-voice probe below stopped before reaching either backend correction.

This verifies transport, delegation, acknowledgement and closure, not a passed full recovery or exact-date acceptance test. Physical microphone capture, room acoustics, browser `LiveVoiceClient`, audible playback quality and interruption remain unverified by this Python probe. Raw transcripts, audio and operational identifiers remain private.

### Saved hotel profile — single authorized voice probe

A separate, explicitly authorized single probe ran at **21:32 UTC** against API `95a0606` and web `31f49a1`, using the same production GPT-Live configuration and a locally synthesized Romanian utterance. Authorization was capped at **45 seconds of voice** and **11 backend provider calls**, including interpretation. The client planned closure by 38 seconds with a 40-second watchdog. No physical microphone, ambient recording, speaker playback or second application server was used.

**Recognition did not preserve the requested identity.** The intended date **31 October 2026** was transcribed as **“30 și 1 octombrie 2020 și 6”**; the explicitly generated group label **“Planning group 1”** became **“Planin d Ground 1.”** The recognized hotel/transfer/flight request and maximum eleven remained present, but the private acceptance guard rejected the date before any command POST. The transcript was not corrected or replaced to make the test pass.

Both the data channel and provider watchdog confirmed closure and **17.0 seconds** of final usage, within authorization. There were **zero command interpretations, zero supplier calls and zero new jobs**. World 49, the case, group, saved profile and current constraints remained unchanged. No backend receipt, spoken acknowledgement or new four-leg result was observed. One provider error event was counted during termination; its detailed payload was not retained, so this report does not assign it a cause. The one-use attempt lock was preserved and the probe was not repeated.

The exact recognized utterance was then covered by an offline backend regression. Even with a mocked valid case/group/full-hotel interpretation and a matching group label, the actual command router rejects its ambiguous raw date before creating a job or calling a source, preserving all operational fields and the saved profile. This verifies the rejection behavior offline; the paid probe stopped in its own guard and did not exercise that backend path.

[Sanitized probe evidence](hotel-voice-live-check.json) records measured transport/delegation/closure and the recognition failure separately from the successful typed hotel run. This synthetic Romanian code-switching failure does not characterize physical microphone accuracy, browser voice behavior or successful spoken-profile recovery. Investigation also found that voice startup lacked current case/group reference context. The implemented correction snapshots only selected IDs, resolves bounded facts on the server and uses the documented [GPT-Live startup history](https://developers.openai.com/api/reference/resources/live/methods/create). Offline tests cover selection, server authentication, reciprocal membership, context bounds, unchanged unscoped behavior and exact-date rejection. The separately authorized `2771e37` probe above subsequently exercised the scoped context path but still failed airport recognition; improved recognition is not claimed. The coherent release passed **1,281 backend tests and 282 frontend tests**, strict TypeScript/production build and formatting. The existing Starlette/httpx deprecation and large lazy scene chunk warnings remain.

## Historical verification records

The following sections preserve earlier implementation and simulator checks. Their inventories, separate QA instances, account-access state and test totals are historical; they do not replace the current live scope above. Auxiliary instances were stopped, and subsequent operation uses only the principal instance on port 8080.

## Integrated release verification — 2026-10-31

The combined scenario-authoring, configuration-recovery and passenger/scene release passed **291 tests: 193 backend and 98 frontend**, strict TypeScript, the production build and full frontend formatting. Merge review preserved current-command isolation, role-tagged conversational context and separate reserved/retired transcript windows. An additional exact-boundary regression checks that the next utterance can start at the previous window boundary while retired late fragments cannot enter reference context. The bounded real-provider and browser checks below apply to their stated flows; no new microphone test is inferred from this merge.

Both Docker images rebuilt and the local API/web services became healthy with the existing provider configuration and persistent database. A private SQLite backup was saved first. The canonical full-world snapshot matched byte for byte before and after restart at version 30; no operational mutation or reset was performed. Fresh Chrome inspection of the deployed release showed the live-provider controls and 96 Google model elements without a map warning. Selecting the existing unavailable B12 focused its geometry and preserved incident state.

## Passenger decisions, voice ordering and scene materials — 2026-10-31

An isolated checkout passed **181 tests: 114 backend and 67 frontend**, strict TypeScript, the production Vite build and frontend formatting. The isolation keeps this verified change separate from concurrent scenario-authoring work. No live operator world or microphone session was reset or modified.

Passenger decline now releases every reservation in a held itinerary in one transaction. Authorization precedes cached replay, confirmed journeys cannot be declined, and concurrent accept/decline requests produce exactly one outcome. Seven new backend regressions cover these properties, durable replay and replacement-offer key collisions. Three additional tests distinguish closing an airborne flight's departure airport from closing its destination: F201 and its 60 inbound passengers remain unchanged when LTN closes, while a CRA closure still requires diversion.

Browser verification used a separate local API and SQLite database, with OpenAI and FR24 disabled. The operator created 120 passenger offers. At a 390×844 phone viewport, G001 declined a two-segment offer; B12 and F211 each released four seats, the passenger page displayed **Offer declined**, and the operator received version 3 through SSE. G002 then accepted its bus, hotel and flight itinerary together at version 4. Switching boarding-link fragments now immediately clears the previous family's screen and loads fresh passenger state. The UI preserves request IDs and offers an explicit retry when an action's outcome is uncertain. `passenger-offer-choice.jpg` and `passenger-offer-declined.jpg` show the actual phone-width pages without access tokens.

Four voice regressions reproduce delayed transcripts and queued/expired delegation windows. Each delegation now reserves its own timeline interval immediately; a bounded wait accepts delayed text while expired words cannot become a later command. The provider supplies no utterance-complete event, so the two-second maximum wait and 250 ms settling period remain a grouping heuristic. These are mocked protocol checks, not a completed spoken end-to-end test.

A second isolated browser fixture injected an invalid-JSON HTTP 502 for acceptance, followed by a definitive HTTP 409 rejection on retry. The UI disabled conflicting actions during uncertainty, reused exactly the same request ID, then cleared the uncertain state after the definitive rejection. Declining the still-held offer subsequently succeeded and released both reservations. The fixture had blank provider keys and blocked outbound connections; these injected failures are test evidence only.

The native scene now has restrained apron wear, darker concrete, recessed warm interiors, glass canopy panels and steel ribs. The terminal GLB was regenerated with the same footprint and anchor: 1,048,676 bytes, 48 meshes, core PBR, no extensions. Model preflight revalidates cached assets because filenames are stable between exports. `native-materials-qa.jpg` records the rendered native scene. It is still an authored architectural model and does not match the photographic mockup exactly.

The built frontend was also served by a separate test API with the configured browser Maps key. Chrome loaded 96 model elements on Google terrain; the regenerated terminal, glazing, buses and family figures rendered at close range without a map warning. `google-materials-qa.jpg` records that check with attribution. OpenAI and FR24 were disabled, and the test API used its own SQLite file.

**Implementation gap:** the scenario clock is fixed. Journey advancement, rescue after a simulated departure and recovery from a completed transfer are not implemented. Advancing fixture time reveals origin/continuity problems; this is pending implementation, not a missing-credentials issue. Real spoken delegation, FR24 access, surveyed geography and event-display fidelity remain separate acceptance gates.

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

At the time of those early checks, FR24 access had not been supplied. The later authorized EK5 lookup and case creation are recorded in the current live release section; this does not independently verify continuous position tracking on the map.

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

- Successful spoken report → scoped automatic recovery on the current release. The latest authorized synthesized-audio attempt failed recognition before any backend command. Device microphone, playback and interruption also remain untested.
- Continuous FR24 position display with original timestamps and agreed account usage/retention; the authorized flight lookup/case flow is already verified above.
- Verified Romanian ground-transport availability and complete physical journeys. The CRA → OTP public-fare adapter now preserves dated adult-party prices, exact stops and original RON/estimated EUR evidence; consumer fare observations alone do not verify seats, luggage or booking expiry. Other Romanian routes remain outside implemented coverage.
- Replanning already confirmed bookings or completed physical journeys remains unsupported. The repeated-operation inventory/history implementation and its offline capacity tests are recorded above.
- Determine the cause of Google's billing warning and confirm billing health; configure appropriate key restrictions.
- Verify surveyed terminal photogrammetry and precise alignment against current airport data; authored GLB rendering/orientation now has browser evidence.
- Review visual fidelity on the event display.
- Production host/domain, account budgets and external deployment, if the team chooses to publish.

The real FR24 lookup, Nuitee case-form/text recovery and complete ten-call hotel/transfer/flight source run close only their stated access/flow gates. A configured voice credential and transport check do not establish successful spoken report/recovery or device-microphone behavior. Google geographic rendering has evidence, while account administration, detailed geometry and photographic fidelity remain separate. Historical unit tests use explicitly mocked provider transports and do not substitute for remaining real checks.

## Non-failing tool notices

The backend test client emits an upstream Starlette deprecation notice for httpx. The lazy Three.js scene chunk exceeds Vite's configured 900 KB raw threshold (about 265 KB gzip); the passenger screen does not load the 3D scene. These notices did not fail the build. Long-term deployment limits, including heuristic optimality, single-role authentication and audit-storage growth, are recorded in `../backend.md`.

## Integration of live inventory and simulator — 31 October 2026

The release combines the local live-data work with main d277678, including the new design and persistent theme. Validation passed: **443 backend + 139 frontend tests**, strict TypeScript, formatting, production frontend build, both Docker images and production Compose wiring for 30 environment variables. The bundled airport timezone lookup is included; the development voice harness is excluded from the production entry. Existing warnings remain: Starlette TestClient deprecation and the lazy native Scene bundle above 900 KB.

Browser checks used separate databases on 18806 and 18807, with provider keys blank and backend outbound connections disabled. A deliberately malformed 502 preserved the original submission; Escape/close/tab switching could not discard it. A 409 replay used the same payload and key, unlocked editing, and a subsequent request generated four test passengers. The isolated operator entered an explicitly named QA coach, the solver produced two proposals, one passenger declined and the other accepted a preference. The UI showed awaiting supplier bookings; zero external bookings were created. SSE updated the operator and switching passenger hashes showed the correct group. The simulator planned journeys for 120 passengers and showed an explicit Maps configuration error when its key was missing. This run does not establish new real-provider or spoken-voice access.

[Machine-readable evidence](integration-release-check.json), [pre-redesign simulator screenshot](integration-simulation.jpg), [pre-redesign live preference screenshot](integration-live-acceptance.jpg). After merging the redesign, the live header, summary and light-theme operation form were checked in the browser; both Docker images rebuilt successfully. Theme bootstrap CSP compatibility is covered by source regression tests. Auxiliary instances were then stopped at the owner's request; subsequent work uses the principal live instance only.

# Live recovery inputs and source provenance

`OPERATION_MODE=live` is the application default. A new operation contains the
Craiova reference location, no passengers, no transport, and no hotel inventory.
Switching an existing simulation to live mode archives its world in SQLite's
`world_archives` table before replacing it. The archived scenario is not loaded
into the live planner. `OPERATION_MODE=simulation` remains an internal regression
and development mode, never a fallback for missing live supply.

## Inputs and provenance

Open **Operation data**. The operator can enter explicitly labelled passenger
planning assumptions, import a real passenger CSV, search provider offers, or
enter a documented operator-verified service. Generated groups never create
flights, vehicles, rooms, prices or schedules. They represent assumed passenger
requirements, not verified identities, and cannot receive real external booking
confirmations. These initial assumptions are separate from sourced supply.

The CSV has one row per passenger and the following exact header:

```csv
passenger_id,group_id,name,origin,destination,original_flight_id,ready_at,deadline,group_budget_eur,accessible,priority,is_adult
```

Times require an ISO timestamp with timezone. Group constraints must agree
between members. `accessible` and `is_adult` are explicit true/false values.
Manual services require the operator, evidence reference, confirming person,
observation time, validity deadline, exact capacity, price and dated schedule.
Coach services also require a vehicle identifier. Existing station node IDs
can be used for confirmed first/last-mile services.

## Supplier connections

| Supplier | Purpose | Configuration and access |
| --- | --- | --- |
| Nuitee / LiteAPI | Dated hotel quotes | Existing production key, `LITEAPI_ENVIRONMENT=live`. |
| Nuitee Flights | Dated flight offers using the existing hotel key | Nuitee must enable production Flights separately; selected automatically when the production LiteAPI key is configured. |
| Duffel | Alternative dated flight offers | `DUFFEL_ACCESS_TOKEN`, `DUFFEL_ENVIRONMENT=live`; production account activation required. |
| Distribusion | Scheduled coach fares | `DISTRIBUSION_API_KEY`, `DISTRIBUSION_ENVIRONMENT=live`, `DISTRIBUSION_OFFERS_ENABLED=true`; partner approval, carrier coverage and Offers-flow activation required. |
| Airport Pickups London | UK private-transfer quotes and source-backed airport–hotel planning legs in either direction | `APL_API_KEY`; exact LiteAPI property metadata, Google server Routes access and a usable ECB reference are required for planning import. No fleet stock or booking is inferred. |
| Google Routes and ECB | Future driving predictions and dated EUR planning estimates for APL GBP prices | `GOOGLE_MAPS_SERVER_API_KEY` for Routes; official ECB reference fetched by the server. EUR settlement is not guaranteed. |
| Flightradar24 | Exact flight lookup and observed aircraft positions | `FR24_API_KEY`; feed start/stop remains explicit in Connections. Neither lookup nor positions establish ticket inventory. |

Secrets stay in the private backend environment. Missing access and sandbox
responses never produce substitute transport. Search adapters call no order,
prebook, payment or cancellation endpoints. Distribusion is scheduled transport,
not a charter-coach dispatch service.

Search and import are separate. Imports retrieve or consume server-held quotes;
the browser cannot submit its own price. Quotes are tied to the exact passenger
group and occupancy and retain provider IDs, terms and observation times.
Supplier expiry is preserved when supplied. Search quotes can be imported for
two minutes. The planner uses the supplier expiry when present; otherwise it
requires a quote from the last two minutes. These checks are not supplier holds.
Hotel arrival/departure times are explicit inputs. The server validates them
against the exact property's sourced check-in/checkout policy and local time
zone, or the operator separately declares an agreement with the property.
Missing/ambiguous policy is excluded; an absent check-in end permits only the
exact published start. The operator override is not a booking or supplier fact.
Current flight and hotel requests support all-adult groups. Coach searches
require explicit ages for every traveller.

Coach quotes use their actual departure and arrival stations. An airport-based
group therefore also needs verified airport–station transport; no implicit
first/last-mile transfer is invented. Aircraft observations alone are drawn on
the live map as tracked aircraft; sourced property/route overlays are separate,
and scheduled resources are not animated as tracked vehicles.

**Private transfers** supports an operator-declared initial airport point to a
sourced UK hotel, or that property to a declared UK airport drop-off bound to an
exact sourced onward flight. Coordinates and the airport-processing allowance
are labelled planning inputs, not verified terminal assignments. Original GBP,
ECB EUR estimates and future Google driving evidence remain attached to the
resource and passenger proposal. The quoted capacity covers the requested party,
not extra sellable seats. APL uses a 120-second app-receipt window; return legs
also expire for new planning at Google receipt plus 120 seconds, whichever is
earlier. Source expiry stays unknown when not supplied. See the
[physical-source and freshness rules](real-data.md#manual-airporthotel-transfers).

The case form also offers a bounded complete hotel package for one explicitly
adult group: initial transfer, requested full hotel stay, return transfer and
onward flight. All three modes and exact stay/point/luggage/processing inputs are
required. It searches one complete-price property and one onward date, with at
least ten counted job calls. Successful apply adds only the chosen chain and
saves exact case/group-bound inputs; unrelated allocations remain unchanged.
Voice/text can reuse a valid saved profile with at least eleven total calls
including interpretation. The default eight-call allowance is never increased
automatically. [The contract](implementation-contract.md#durable-recovery-jobs)
describes the limits and atomic source revalidation.

## Reporting operational changes

Use the case panel to report a passenger group's actual or explicitly assumed
planning location/readiness, or the service inspector to report an existing
service unavailable. These operator reports preserve supplier evidence and
invalidate only the affected pending proposals. They cannot relocate groups or
change services with externally confirmed bookings. A reported arrival is not
proof of landing or airport permission; an unavailable-service report is not a
supplier cancellation. The forms do not start a search.

Voice/text can submit one such report, optionally followed by an explicitly
requested recovery search in the same command. For arrival/readiness or a
follow-up search, name the current case/flight and exact linked groups. Specify
an unambiguous arrival airport and either the full readiness date, minute and
UTC/offset, or an explicit interval such as “ready in 30 minutes” or “pregătiți în
30 de minute” (0–10080 integer minutes). Relative readiness uses the server's
durable command-receipt time, rounded up to a UTC minute; the acknowledgement
speaks the resulting absolute UTC time. Retries retain that same anchor rather
than moving readiness forward. Bare “now”, a flight ETA or diversion alone is
insufficient. For a service outage, use its exact resource ID
or a unique current name. Ambiguous, conditional or negated reports require
clarification; selected UI context and conversation history do not authorize the
missing facts or a call limit.

The acknowledgement separates **report recorded** from **search request
recorded**. A valid report can remain saved while search is blocked by a missing
hotel configuration, insufficient allowance or an occupied queue. That outcome
makes no supplier-search calls. The default total allowance remains eight,
including interpretation; the complete saved hotel flow still needs eleven and
revalidates its inputs after the report. A changed location may invalidate the
saved hotel setup instead of silently choosing new pickup coordinates or times.

A repeated request uses its original receipt and says already recorded; inspect
the case panel for current progress. If the connection leaves the outcome
unknown, use **Recover request**, which reuses the same transcript, request and
ID. Do not treat the spoken acknowledgement as a completed journey, a current
supplier hold or a booking. Report-plus-search and acknowledgement handling have
offline tests; they have not established successful paid spoken recovery or
physical microphone behavior.

## Planning and confirmation

The solver uses sourced resources, exact whole-group quote totals, capacities,
deadlines, group constraints and real elapsed time. Missing, stale, inaccessible
or synthetic transport cannot satisfy a live plan. The result is a **proposal**.
Passenger acceptance records a preference and leaves supplier booking pending.
Only explicit operator references for every segment can mark a real passenger
itinerary confirmed. Those references are operator assertions, not automatic
supplier verification. No purchase is made by the application.

All input routes are operator-authenticated, versioned and idempotent:
`/api/live/import-passengers`, `/generate-passengers`, `/resources`,
`/hotel-quotes`, `/travel/import`, and `/offers/{id}/confirm`, under the
`/api/live` prefix. `/api/live/travel/search` is a read-only supplier lookup.

## Activation limits

Nuitee hotel and flight quotes, APL in both directions, Google dated routes and
ECB estimates have bounded real evidence. One automatic ten-call hotel package
produced a complete four-resource proposal for two adults, preserving the other
group and creating no bookings. This does not establish universal supplier
coverage or future availability. Spoken saved-profile recovery remains
unverified: the last authorized probe stopped on corrupted transcription before
any backend command. [Verification evidence](verification/report.md) separates
these outcomes from offline tests.

Duffel and Distribusion production access remains account-specific. Distribusion
requires a production partner key and Offers-flow activation; no coach
availability is fabricated while access is missing. The connected Booking.com
account has not supplied the required partner access. Set
`FLIGHT_OFFERS_PROVIDER=auto`, `nuitee` or `duffel` to choose explicitly; a provider
failure never switches suppliers. Missing keys leave the affected functions
unavailable rather than populating the operation with substitute services.

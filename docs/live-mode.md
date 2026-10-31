# Live resources with explicit test passengers

`OPERATION_MODE=live` is the application default. A new operation contains the
Craiova reference location, no passengers, no transport, and no hotel inventory.
Switching an existing simulation to live mode archives its world in SQLite's
`world_archives` table before replacing it. The archived scenario is not loaded
into the live planner. `OPERATION_MODE=simulation` remains available for offline
demonstration and regression tests.

## Inputs and provenance

Open **Live data inputs**. The operator can generate clearly marked test
passengers, import a real passenger CSV, search provider offers, or enter an
operator-confirmed service. Generating passengers never generates flights,
vehicles, rooms, prices or schedules. Test passengers cannot receive real
external booking confirmations.

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
| Flightradar24 | Observed aircraft positions | Existing FR24 connection; start/stop remains explicit in Connections. This is not ticket inventory. |

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
Hotel arrival/departure times require an explicit operator confirmation.
Current flight and hotel requests support all-adult groups. Coach searches
require explicit ages for every traveller.

Coach quotes use their actual departure and arrival stations. An airport-based
group therefore also needs verified airport–station transport; no implicit
first/last-mile transfer is invented. Aircraft observations alone are drawn on
the live map; scheduled resources are not animated as tracked vehicles.

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

Nuitee hotel rates have been verified with the connected production account.
Nuitee Flights, Duffel and Distribusion adapters are covered by offline contract tests; their
production access is account-specific. Nuitee returned flight offers using the
existing production key and its live response was verified. Distribusion still
requires a production partner key and Offers-flow activation; no coach availability
is fabricated while access is missing. Set `FLIGHT_OFFERS_PROVIDER=auto`, `nuitee`
or `duffel` to choose explicitly; a provider failure never switches suppliers.

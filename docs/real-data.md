# Real accommodation and road data

Open **Real hotels & routes** in the Resources rail. Choose the hotel airport,
route destination, stay dates, adults and rooms, then select **Search real data**.
The operator-only `POST /api/real-data/search` reads external sources. It never
adds inventory, holds rooms, reserves seats or modifies the recovery scenario.

## Sources and meaning

| Section | Default source | Meaning |
| --- | --- | --- |
| Nearby hotels | OpenStreetMap through Overpass | Named accommodation records within 20 km, addresses/contacts when supplied, and straight-line distance from the airport. No room count or price is inferred. |
| Airport transfer route | OSRM using OpenStreetMap | Road distance and estimated driving time for a car profile. It is not a scheduled coach service, vehicle quote or live-traffic measurement. |
| Hotel room offers | Booking.com Demand API 3.3, when configured | Full-stay prices returned for the selected dates, adults and rooms, joined with property names from the details endpoint. No booking is placed. |
| Hotel room offers | Nuitee / LiteAPI, preferred by automatic selection when configured for live use | A validated offer per hotel for all requested rooms and adults, preferring complete mandatory costs. Supplier booking amount and additional mandatory fees remain separately visible; no prebooking or payment is performed. |
| Private-transfer planning | APL, exact LiteAPI property metadata, dated Google Routes and ECB | Manual initial UK airport-point → hotel or hotel → UK airport leg. Return binds an exact group-scoped onward flight and operator buffer. GBP is the supplier price; EUR and driving arrival are estimates. No vehicle booking or complete itinerary is implied. |

**Compare hotel prices** opens the public Momondo hotel search and provides a
copyable summary of the airport, dates, guests and rooms. The search summary is
not automatically transmitted to Momondo. This is an external comparison link,
not a Momondo API connection or an embedded quote. Momondo/KAYAK production API
access requires approval through their affiliate programme.

A separate Google server key selects Google Places and Google Routes for the
first two sections. Google Routes requests traffic-aware driving estimates.
Missing Google configuration selects the documented public-map providers;
failure of a configured Google provider is shown explicitly rather than
silently changing sources.

Each section reports its source, retrieval time and whether a cached response
was used. Retrieval time is not the date of the last edit to an OSM place. An
empty successful response differs from an unavailable provider. Source failures
do not erase successful results from the other sections.

## Configuration

Merge the needed values from [the credential template](../backend/real-data.env.example)
into the private `backend/.env`. Restart the API after changing environment values;
for Docker, recreate the API container. The server reads its key from
`GOOGLE_MAPS_SERVER_API_KEY`; use a dedicated key with the appropriate API/IP
restrictions for production.

```dotenv
GOOGLE_MAPS_SERVER_API_KEY=
APL_API_KEY=
BOOKING_DEMAND_API_TOKEN=
BOOKING_AFFILIATE_ID=
BOOKING_BOOKER_COUNTRY=ro
HOTEL_OFFERS_PROVIDER=auto
LITEAPI_API_KEY=
LITEAPI_ENVIRONMENT=sandbox
LITEAPI_GUEST_NATIONALITY=RO
REAL_DATA_OVERPASS_URL=https://overpass-api.de/api/interpreter
REAL_DATA_OSRM_URL=https://router.project-osrm.org
```

Google requires Places API (New) and Routes API enabled for the server key.
Booking requires authorised Demand API partner access. Supplying credentials
does not prove that the account has access; an actual search checks that access.
`BOOKING_BOOKER_COUNTRY` describes the booking customer market, not the hotel
country. LiteAPI's separate `LITEAPI_GUEST_NATIONALITY` describes the nationality
used for its rate request; the default is RO. Change this setting for a different
traveller market before using its quotes.

`HOTEL_OFFERS_PROVIDER=auto` selects LiteAPI when its key is set, otherwise a
configured Booking account. Set `liteapi` or `booking` to choose explicitly.
An unavailable selected provider never silently switches to another supplier.
LiteAPI live pricing requires `LITEAPI_ENVIRONMENT=live` and an authorised `prod_`
key. The rates response schema makes `sandbox` optional, and production responses
may omit it. When present, it must be the boolean `false`; `true` is test data,
and other values are rejected. Test keys show **TEST ACCESS** and no prices,
including when a test key is accidentally configured as live. The free Nuitee
account initially provides sandbox access; the provider
requires a valid card for production activation. Account/card setup is separate
from this application's read-only rates integration.

The public OSM/OSRM endpoints are for low-volume local evaluation. They can be
unavailable or rate limited; use operated services for production. Operator-set
endpoint URLs must use HTTPS. Browser requests cannot supply provider URLs or
credentials. The backend sends an identifying User-Agent, spaces public requests
at least one second apart, bounds each external transfer to 20 seconds and 2 MB,
and does not retry or follow redirects automatically.

Search is explicit, without background polling. Successful hotel records are
cached for one hour, OSRM routes for five minutes and supplier offers for one minute.
`/api/real-data/search` does not put Google Places/Routes content in its reference
cache. The separate private-transfer flow below uses a bounded, short-lived
server receipt so an import can use verified source results. The
in-process reference cache holds at most 64 entries and coalesces same-source
work. Errors are not cached. Results include at most ten hotel records and ten offers.

## Manual airport–hotel transfers

Open **Private transfers** in the live data dialog. Select an existing adult-only
group and sourced hotel property, then provide the group's initial meeting-point
label/coordinates, explicit suitcase count and future pickup time. Confirm the
point as operator-provided passenger input. It is not an airport or supplier
assertion that the passengers are there, and it does not establish an arriving
flight's terminal connection. Unsupported accessibility or child-seat needs
block this flow rather than being assumed satisfied.

The same form also offers **Hotel → airport for onward flight**. Select a current,
available, unbooked sourced flight quoted exclusively for the group; stale quotes
must be refreshed explicitly. Enter the reported airport drop-off label/coordinates
and an operator processing allowance of 1–720 minutes. The server checks predicted
driving arrival plus this allowance against that exact flight's departure. Both
directions are limited to supported UK airports. The departure terminal remains
unknown; the allowance is not airline guidance or a boarding guarantee.

The server first retrieves the exact LiteAPI hotel ID's metadata. Coordinates
must agree with the existing `HP-…` property node; its postal address, city and
country come from that record, not an editable destination field. Missing usable
metadata prevents further APL/Routes work. Existing geography can be used even
when a previous room quote is stale; no bed price or room availability is refreshed
by the transfer search.

APL receives that property address, the declared airport endpoint, direction, party
size, suitcases and exact London pickup date/time. Google Routes receives the exact
coordinates and future RFC3339 departure time. These independent requests run
concurrently after property validation. A missing Google server key, provider
failure or Google's fallback computation is not replaced by an OSRM or
current-time estimate. Unknown APL terminal/expiry fields remain unknown;
conflicting airport, direction, terminal or destination evidence blocks import.
Optional APL `meeting_point.name` and `meeting_point.instructions` are displayed
separately as supplier pickup guidance; they do not establish the flight terminal.

The response keeps requested and road-snapped coordinates separate, with their
offsets. Application screening limits are 10 km from the catalog airport
reference for the declared airport point and 100 m for either road snap. Passing these
checks does not verify pedestrian access or a terminal connection. Driving
seconds retain their source precision; only the importer rounds arrival upward
to an operation minute. Loading and walking are not included unless the source
establishes them. A bounded Google `OVERVIEW` encoded polyline may be shown as the
sourced road path; missing geometry stays unavailable rather than being invented.
Return routes and their separate reported-drop-off marker use the declared
drop-off coordinates, not the airport's catalog reference position.

Choose an importable vehicle option to add the planning leg. The server-held
`quote_id` supplies all price and route data. Outbound import creates a distinct
`MP-…` meeting point eligible only as that group's first leg; the group's airport
origin is not rewritten. A return uses the actual hotel node and onward airport
node, with no first-leg exemption. Its typed airport-arrival assumption binds the
exact flight revision, drop-off and operator buffer, with a null departure terminal.
The solver requires that flight immediately after the return. The quoted party
capacity is not fleet stock, a supplier reservation
or extra sellable seats. The accepted `ecb_reference_v1` policy preserves original
GBP and dated estimated EUR through resources, itinerary legs, Decisions and the
passenger view. Missing/stale FX leaves GBP visible but blocks import. Revalidate
the supplier price and currency conversion before booking; EUR settlement is
not guaranteed.

The source receipt/cache is usable for at most 120 seconds, with a possibly
earlier pickup/cutoff. This is application policy; APL's supplier quote time and
expiry remain null. Repeat the same serialized request/key to recover a known
result without another dispatch. An unknown dispatch is never retried
automatically; starting another search is an explicit new attempt with possible
provider cost. Backups omit the transient cache and cannot resume in-flight
searches. Imports use version checks, immutable revisions and atomic scoped
updates, preserving unrelated and confirmed allocations. Import retires only
replaceable source revisions for the same group and direction; it retains the
other direction's source records.
For an imported return, planning freshness ends at the earlier of APL receipt
plus 120 seconds and Google prediction receipt plus 120 seconds. Missing, malformed
or future Google receipts do not establish current return evidence. The UI keeps
both UTC receipt times, the combined cutoff and recorded-booking status separate.

These are manual legs, not verified terminal-to-flight connections. Each import
by itself creates no complete offer, supplier hold or booking. The separate
automatic hotel package flow below sources and validates a full chain. Browser
and provider evidence is tracked in the
[verification report](verification/report.md), separately from implementation and
mocked tests.

## Automatic sourced hotel package

The case form can request one adult-only group's complete initial transfer →
hotel stay → return transfer → onward flight plan. Initial and onward airports
must be supported UK airports. The operator supplies exact offset-bearing stay
and pickup times, rooms/minimum rest, initial/drop-off points, bags and airport
processing allowance. All three modes are required. Unsupported accessibility,
missing inputs or a cap below ten job calls stop before source dispatch; the
default eight is not increased.

The job obtains current flight and room offers, shortlists one cheapest property
with complete mandatory EUR costs, and fetches its exact metadata. Local stay
policy, occupancy, postal address and coordinates must reconcile. It sources two
APL quotes, two future Google driving routes and one ECB reference separately,
with every dispatch counted. Responses query selection and Decisions bring the
conservative complete pipeline to ten calls. The bounded search covers one
onward flight date (checkout's Europe/London date) and at most three fitting
vehicles per direction; it does not silently move to another property on failure.

The full requested stay is mandatory; a wait or disconnected room quote cannot
satisfy it. Return pickup is checkout plus ten minutes, an application allowance.
Exact driving evidence and the operator's processing buffer must fit the sourced
flight. The same physical-source, GBP/estimated-EUR and receipt-freshness rules as
manual transfers apply. Only a complete validated chosen chain is committed with
the selected group's requirements, proposal and saved configuration. Unrelated
allocations stay unchanged; no supplier holds or bookings are made.

After success, text/voice can reuse that exact case/group-bound configuration
with at least eleven total calls including interpretation. The default stays
eight. Without a saved profile, use the typed form first. Reuse revalidates
current constraints and exact times/points, then launches new counted source
searches. It does not treat old fares as current, shift dates or infer missing
input. Failed/unknown jobs preserve the prior profile.

One real automatic run on API `95a0606`/web `03112f6` completed exactly ten calls
and applied the four-resource proposal for two passengers, with zero unresolved
requirements and no bookings. It took 9.495 seconds from queue to apply and
preserved the other group. The selected hotel, flight and two transfer quotes
totalled approximately EUR934.86, including explicitly estimated ECB transfer
components; the quotes expired normally afterwards. Saved-input browser review
preserved exact times and the cap, required both point confirmations again and
made no supplier calls. This single run verifies the typed source assembly, not
general availability, latency guarantees or a spoken/browser-voice hotel run.
See the [verification report](verification/report.md) for the bounded evidence.

## Verification and limits

The provider tests cover malformed/upstream errors, response size and timeout
limits, source separation, URL safety, exact decimal prices, cache behavior and
Booking and LiteAPI request contracts, complete room occupancy and explicit
sandbox rejection. API tests verify operator access, input validation,
private credentials and that searches do not change scenario resources or
reservations. These mocked tests do not establish live provider access.

Live checks and browser results are recorded separately in the verification
report. Google Places and Routes were activated in the existing project after
the owner confirmed the requested EEA terms, and both passed live API and
browser verification. The connected Booking account returned no access rights
to Affiliate Partner Center; Booking offers therefore remain explicitly
unavailable. Nuitee production access was subsequently enabled by the owner;
the live search for CRA, 31 October–1 November 2026, two adults and one room
returned nine validated EUR offers in the browser. No room was booked.
Road estimates never establish coach availability, fares, vehicle accessibility
or suitability of a road for a coach.

## Mandatory prices and physical hotel connections

LiteAPI's exact booking amount remains unchanged. Included fees are not added
again. Excluded mandatory fees are counted once for each quoted room occupancy;
they are not multiplied again by nights or guests. Explicit `null` or empty
`taxesAndFees` means the supplier states that taxes are included. An absent or
incomplete disclosure, or fees in another currency, leaves the mandatory EUR
total unknown. Such quotes remain visible but cannot enter a fully budgeted
recovery plan. The budget allowance rounds complete EUR totals upward to cents;
it does not claim a payment date or supplier charge rounding policy.

Imported hotels have their own physical location, using the property's supplier
coordinates. Missing rate coordinates are requested from LiteAPI's matching
hotel-details record. The property is never placed at the search airport.
A complete itinerary must contain independently sourced connections to and from
that location; no shuttle, fare or vehicle capacity is inferred from a room quote.

Stay timestamps are explicit planning inputs. They require either validation
against the exact property's sourced stay policy or a separately declared
agreement with the property; an unchecked override does not imply confirmation.
Their calendar dates are checked in the actual property's time zone, including daylight-saving
changes. Offline lookup uses pinned `timezonefinder` 9.2.0,
`timezonefinder-data` 4.2026.5 and IANA `tzdata` 2026.6. No Google Time Zone API
or extra API key is required. The geographic boundaries come from
[timezone-boundary-builder](https://github.com/evansiroky/timezone-boundary-builder)
and [OpenStreetMap contributors](https://www.openstreetmap.org/copyright), under
ODbL; the lookup library is MIT licensed. These are reference geography and
clock rules, not evidence of hotel availability.

## Official references

- [Overpass public-server guidance](https://dev.overpass-api.de/overpass-doc/en/preface/commons.html)
- [OSRM HTTP API](https://project-osrm.org/docs/v5.24.0/api/)
- [Public routing service policy](https://routing.openstreetmap.de/about.html)
- [OpenStreetMap attribution](https://www.openstreetmap.org/copyright)
- [Google Nearby Search](https://developers.google.com/maps/documentation/places/web-service/nearby-search)
- [Google Compute Routes](https://developers.google.com/maps/documentation/routes/compute_route_directions)
- [Google dated route request and response](https://developers.google.com/maps/documentation/routes/reference/rest/v2/TopLevel/computeRoutes)
- [Airport Pickups London API](https://mcp.airport-pickups-london.com/docs)
- [ECB reference rates](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html)
- [Booking Demand authentication](https://developers.booking.com/demand/docs/development-guide/authentication)
- [Booking 3.3 search](https://developers.booking.com/demand/docs/open-api/3.3/demand-api/accommodations/accommodations/search)
- [Booking 3.3 OpenAPI](https://developers.booking.com/_bundle/demand/docs/open-api/3.3/demand-api.json?download)
- [Nuitee / LiteAPI account access](https://docs.liteapi.travel/docs/getting-a-sandbox-key)
- [LiteAPI hotel rates](https://docs.liteapi.travel/reference/post_hotels-rates)
- [LiteAPI hotel details](https://docs.liteapi.travel/reference/get_data-hotel)
- [LiteAPI usage terms](https://docs.liteapi.travel/reference/api-pricing-usage-costs)
- [KAYAK affiliate access, including Momondo](https://affiliates.kayak.com/)

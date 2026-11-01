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
| Romanian airport transfer | Bucharest.CAB consumer quotation, OTP → CRA | Native EUR cash/no-extras vehicle-category fare for an explicit pickup and adult party. Initial airport-level leg only; source driving duration is an estimate without departure-time traffic. Fleet stock and supplier expiry remain unknown. |

## Romanian airport vehicle quotes

The **Bucharest.CAB** panel in **Real hotels & routes** supports an explicit
adult-only group of one to seven people whose recorded initial airport is OTP.
Choose a future pickup, suitcases and cabin bags, then request the quote. The
destination of this leg is CRA; the group's eventual destination may be another
airport. Adding the leg does not create the onward flight or a complete itinerary.

The backend performs exactly two read-only GETs: the public
[consumer form](https://www.bucharest.cab/en/book) for a fresh anonymous session,
then its localized vehicle-search endpoint. No account or company credentials
are required. The query sends the exact date/time, party and public airport place
identities. Supplier-returned airport references, coordinates, local/UTC pickup,
timezone, road distance and vehicle limits must agree. Luggage fit is checked
locally; the source endpoint does not receive bag counts. No booking, price-store,
payment or driver-dispatch endpoint is implemented.

The displayed fare reproduces the supplier's published client calculation from
its returned route distance and vehicle tariff, including a returned night
surcharge. It is **native EUR, cash, with no optional extras**, not an ECB
conversion. Payment-link fees and optional extras are outside that amount.
The source's day/night advance rules are currently four/nine hours; because the
rule-selection clock is undocumented, the adapter enforces their greater value
and explains that application policy. It never silently shifts the pickup.

Each receipt has an application window of **at most 120 seconds**, also bounded
by the conservative advance-notice cutoff. This is not a supplier hold or expiry.
The route's driving duration has no pickup-time traffic query.
Airport references do not verify a terminal, curb, pedestrian connection or
accessible vehicle. The planning resource is bound to the exact group's current
initial facts and may only be the first leg. A later flight arrival cannot reuse
that initial-position evidence. For this source, the combined plan and fixed
allocations may contain at most one quoted vehicle; this is a conservative
application limit because simultaneous fleet availability is unverified.

Source searches, per-HTTP dispatch counts and imports are durable and idempotent.
Scope is checked before each dispatch, when the response is saved, on replay and
again on import. An uncertain search is never silently repeated. An old receipt
cannot resurrect retired supply. Imported capacity describes the selected
group's allocation, not available seats in a fleet. Generic external-booking
confirmation is disabled for these unreserved category quotes.

On 1 November 2026, the public consumer flow and its query-only endpoint returned
OTP → CRA for **2 November, 12:00 Europe/Bucharest, two adults**: Sedan EUR188,
Combi EUR212, Van EUR235 and Business Class EUR258, with 234,466 metres and an
8,787-second source road estimate. These are historical observations, not current
prices or reserved vehicles. The interface and parser matched; the later
application verification is recorded in the [verification report](verification/report.md).

These consumer endpoints are observed website interfaces, not a contracted public
API. Unexpected protocol, tariff, identity or timing changes fail visibly. Other
Romanian airport pairs and flight-arrival pickup binding are not established by
this bounded integration.

### Automatic initial transfer before flight discovery

Normal recovery and the structured **reported arrival + recovery** form accept
an optional `airport_transfers` list. Each entry names one exact selected group,
`destination_airport: "CRA"`, an offset-aware whole-minute `pickup_at`, and explicit
`suitcases` and `handbags` counts (each 0–7). The list is bounded to 12 distinct
groups within the job selection; each party must be explicitly adult-only, 1–7
people, with a reported initial OTP position and no unsupported accessibility
requirement or pending flight arrival. The arrival form validates against its
reported passenger state. `bus` mode is required, and this option cannot be
combined with `hotel_recovery`. Empty luggage fields do not mean zero; readiness,
deadline, future-date limits and source advance rules cannot shift the pickup.

For N requested transfers, the preflight allowance is at least **2N+1 calls**:
two source GETs per group plus Decisions. This lower bound does not cover new
flight searches. One CAB quote, one Responses query selection, one flight search
and Decisions require at least **five calls**. The selected cap is never raised.
The source phases are separate durable job steps; only safe bootstrap-received
metadata is recorded for the first, and the normalized quote for the second.
Anonymous tokens and cookies stay in memory. Automatic and manual CAB searches
share the persistent receipt sequence, so their revision ordering is comparable.

Eligible category alternatives are staged before flight-query discovery, allowing
an initial OTP→CRA drive followed by a sourced departure from CRA. Every complete
candidate must still pass party, time, capacity, source, budget and destination
checks. The vehicle remains initial-only; there is no implicit airport pickup
after a future flight. A fixed allocation already using Bucharest.CAB prevents
additional sourcing from this source, and the combined plan may use at most one
quoted vehicle. Several group searches do not establish several available cars.

Only the selected vehicle category and selected onward resources are committed
with the complete validated proposal in one transaction. Current version, scope,
receipt freshness and revision retirement are rechecked; unrelated inventory is
protected. Expired, cancelled or unknown attempts are not retried or refreshed
automatically. The short receipt window can expire while the rest of the plan
is being evaluated; no old quote is silently renewed. There are no bookings.

Current voice commands do not supply `airport_transfers`; they cannot infer its
pickup or baggage inputs. Historical live evidence above covers the consumer
quote and manual path. The new combined automatic CAB-plus-flight flow has
**offline mocked integration verification only**, not live production acceptance.
See the [verification report](verification/report.md) for separately dated evidence.

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

## Romanian ground-transport coverage gap

The bounded [Bucharest.CAB initial OTP → CRA flow](#romanian-airport-vehicle-quotes)
is now separate from this remaining network-coverage gap. It does not establish
reverse-direction service, CRA/SBZ-wide coverage, coach stock or rail connections.

Further read-only consumer research on 1 November found two useful, **not yet
integrated** sources. [Transfero](https://www.transfero.ro/bilete-online/) returned
OTP Arrivals → Hotel Aro Palace, Brașov for 2 November: a two-adult RON260 total
and three source-reported seats. The same departure disappeared for four adults.
No requested OTP↔SBZ or OTP↔GHV offer was returned for that date; CRA was absent
from the observed selector. Its local-clock times, baggage and expiry need their
own contract; a hotel stop must not be relabeled as an airport.

The [CFR consumer flow](https://www.cfrcalatori.ro/cum-cumpar-online/) returned
R-E9900 Craiova railway station → București Nord on 2 November, 02:10–06:23,
RON97 for two adults in second class, with a separate timestamped 530-seat
availability response. The seven read-only HTTP requests stopped at pricing,
before account/confirmation steps. Rail requires its own mode, shared stock and
exact airport–station links. Neither research observation created app inventory,
held seats or established current supply.

The connected APL flow covers supported UK airport–hotel transfers. It does not
provide CRA/OTP/SBZ supply. A read-only review on 31 October 2026 found a relevant
consumer booking route through [BILETERIA](https://www.bileteria.ro/): its public
form accepts origin, destination, departure date and passenger count, and its
[consumer terms](https://www.bileteria.ro/Termeni) describe passenger checkout and
electronic tickets without transport-company registration.

Official operator/marketplace listings identify these airport routes:

- [Union Business & Travel: CRA terminal → OTP departures](https://ubt.autogari.ro/Transport/AeroportCraiova-Otopeni).
  [Craiova Airport also lists UBT](https://aeroportcraiova.ro/transfer-aeroport/ubt/)
  as a transfer provider.
- [Trans Olteanu: CRA terminal → SBZ airport](https://transolteanu.autogari.ro/Transport/AeroportCraiova-SibiuAeroportInternational),
  with a connection through Brașov; this is not a direct vehicle.
- [BILETERIA: SBZ airport → OTP airport](https://bileteria.ro/Transport/SibiuAeroportInternational-Otopeni),
  with operator-specific itineraries and purchase cutoffs.

That 31 October review established route listings and a consumer checkout path,
not an implemented integration, a requested-date fare or proof of seats for a
recovery group. No dated search, account creation or booking was performed in
that earlier review.

A subsequent read-only consumer search on 1 November 2026 selected **Aeroport
Craiova → Aeroport Otopeni, 2 November 2026, two adults**. The
[exact dated search](https://www.bileteria.ro/Transport/AeroportCraiova-Otopeni?zi=2026-11-02&pas=2-0-0-0-0)
displayed Union Business & Travel services at **190 lei for the selected party**;
the preceding one-adult search displayed 95 lei. One example departed at 00:15
and arrived at 03:20, from CRA **Terminal Plecari/Sosiri** to OTP **Terminal
PLECARI/ DEPARTURES**. These are the site's displayed times; no UTC offset was
shown. An enabled **Cumpără bilet** button and the wording **Se pot face
rezervări înainte de îmbarcare** were present. A connecting B&B result lacked a
price and cannot be treated as a zero-cost offer.

The search required no account or transport-company registration. A separate
unauthenticated HTTP GET to the same public URL returned 200 with the selected
date, party parameter, fare and stop names in the HTML. This establishes a
technical path for the bounded automatic consumer-search adapter described
below. It does not establish a supported inventory API or
an integration contract.

The displayed party fare and purchase button are **not proof that two seats were
checked, held or reserved**. No remaining-seat count or supplier quote expiry was
exposed. The boarding wording is retained as source text, not invented as a
quote-expiration timestamp. The check stopped at fare display without clicking
the purchase button, creating an account or reservation, accepting terms or
making a payment. Any
adapter must preserve original RON pricing, exact dated party/stops,
source observation time and unknown availability/expiry; EUR conversion needs
explicit estimate provenance. Luggage terms, current sellability and any
connections still need verification for the selected trip. An application cache
lifetime cannot be presented as a supplier hold.

No open consumer inventory API was verified. The
[documented Autogari API](https://www.autogari.ro/blog/beneficiile-si-costurile-ecosistemului-autogari-ro/)
concerns transport-operator system synchronization and commercial onboarding,
not unrestricted inventory access for this application.

## Integrated Romanian consumer-fare search

The application now has a bounded **CRA airport → OTP airport** adapter for the
public BILETERIA dated search. Other Romanian routes listed above remain research
references and are not silently enabled. Searches require an explicit local date
from today through the next 31 days and **one to eight adults**; discounts and
child ages are not inferred. No account, checkout request, booking endpoint or
new credential is involved.

Authenticated `GET /api/coaches/status` exposes this exact coverage and
`POST /api/coaches/search` accepts `origin_airport`, `destination_airport`,
`travel_date` and `adults`. The provider makes one bounded HTTPS GET with redirects
and retries disabled. It verifies the returned search form's route/party, selected
date, visible fare and currency, exact station links and inert per-row date/party
metadata. It never executes the page scripts or follows purchase links. Changed,
conflicting, oversized or unrecognizable source markup cannot create a substitute
fare. Connections, missing prices and unverifiable rows are disclosed as exclusions.

Direct results retain source operator, exact stops, original RON total for the
requested party, displayed departure/arrival dates and clocks, duration,
reservation notices and application observation time. An overnight arrival day is
retained only when the source explicitly supplies it and the displayed duration
agrees. The source supplies no UTC offset; these local clocks are not presented as
verified UTC instants. Purchase-button state is source evidence only. Seat
availability and supplier expiry remain unknown, including when the button is
enabled. No `Resource`, capacity, hold or EUR allocation is inferred from a fare.

Optional ECB enrichment retains the RON amount and adds a separately labelled
EUR reference estimate with `source_currency: RON`, rate date, observation time
and the existing three-calendar-day freshness policy. Missing or stale rates leave
the original fare visible without an EUR estimate. The APL-specific GBP planning
contract is unchanged.

The existing **Real hotels & routes** dialog includes coach fare search. Automatic
recovery requests containing ground transport also query supported selected groups'
earliest eligible local date, deduplicating identical route/date/adult-party
requests. Each BILETERIA or ECB dispatch is counted in the durable job allowance;
reference searches use spare calls after flight sourcing and preserve an actual
candidate's required Decisions call. Original RON observations survive an FX
failure or exhausted FX allowance. Timeout/failed reference steps remain visible,
without retries or invalidating an independently verified flight plan.

Jobs retain bounded `ground_references` separately from allocated services. The
operator sees reference fares alongside the unresolved availability/physical
connection requirement; Decisions receives an application-authored summary of
that limitation. A ground-only request cannot report passengers recovered merely
because a consumer fare was found. This integration establishes automatic dated
fare discovery, **not verified Romanian seat inventory or a complete ground journey**.

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

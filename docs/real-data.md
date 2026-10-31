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
Google Places and Routes content is not placed in the backend cache. The
in-process reference cache holds at most 64 entries and coalesces same-source
work. Errors are not cached. Results include at most ten hotel records and ten offers.

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

Stay timestamps are explicit operator-confirmed inputs. Their calendar dates
are checked in the actual property's time zone, including daylight-saving
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
- [Booking Demand authentication](https://developers.booking.com/demand/docs/development-guide/authentication)
- [Booking 3.3 search](https://developers.booking.com/demand/docs/open-api/3.3/demand-api/accommodations/accommodations/search)
- [Booking 3.3 OpenAPI](https://developers.booking.com/_bundle/demand/docs/open-api/3.3/demand-api.json?download)
- [Nuitee / LiteAPI account access](https://docs.liteapi.travel/docs/getting-a-sandbox-key)
- [LiteAPI hotel rates](https://docs.liteapi.travel/reference/post_hotels-rates)
- [LiteAPI hotel details](https://docs.liteapi.travel/reference/get_data-hotel)
- [LiteAPI usage terms](https://docs.liteapi.travel/reference/api-pricing-usage-costs)
- [KAYAK affiliate access, including Momondo](https://affiliates.kayak.com/)

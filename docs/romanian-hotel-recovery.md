# Romanian scheduled transport and hotel recovery

**Romanian coach + hotel** searches **OTP → Hotel Aro Palace, Brașov → OTP → an
onward flight**, using exact LiteAPI property `lpce923`. The operator's
[route](https://direct-aeroport.ro/program-rute/brasov-aeroport-otopeni/) and
[boarding address](https://direct-aeroport.ro/contact/) name this property.
The [hotel address](https://aro-palace.ro/contact/) and live metadata agree on
Eroilor Boulevard 27–29; proximity alone does not establish the connection.

This is bounded coverage: one explicitly adult-only group of 1–12, already ready
at OTP, without unsupported accessibility requirements, ending at another airport.
Twelve is an application limit. A scheduled aircraft arrival does not establish
passenger readiness at a coach stop.

## Inputs and connection rules

Select flight, hotel and ground modes. Supply exact offset-aware stay times,
rooms, minimum rest, hold bags and hand bags. Confirm at most one hold bag up to
25 kg and one hand bag up to 10 kg per adult. Extra baggage and optional address
pickup are outside this integration. Empty fields do not mean zero.

Conservative rules reconcile differing
[carrier terms](https://direct-aeroport.ro/termeni-conditii-calatorie/): 12 hours'
advance notice, 20 minutes for boarding, and at least 180 minutes between scheduled
return to OTP and the flight. Larger hotel/airport margins may be requested.
These application rules do not establish exact doors/bays, walking time, the
flight's terminal or a protected connection. Times and deadlines are never shifted.

Supplier stay policy and complete mandatory EUR costs are checked for each job.
When only a check-in start is published, the existing strict policy accepts that
exact start only. The whole requested stay and minimum rest must be covered.

## Source and decision flow

The minimum is **eight source/model calls**; default eight stays unchanged:

1. Two dated Transfero searches, one per direction for the exact adult party.
2. One exact-hotel rate search and one property/policy request to LiteAPI.
3. One ECB request for dated RON-to-EUR planning estimates.
4. One Responses selection of an onward query and one flight-source search.
5. One Decisions selection among independently validated complete plans.

The search considers the property-local stay dates, at most three eligible
services per direction and one onward flight date. The model chooses; code checks
identity, stock, timing, party, stay, budget and destination. Known failures stop
dependent work; unknown dispatched outcomes are not automatically retried.
The durable ledger preserves the original call allowance.

Transfero is an observed consumer website interface, not a contracted public API.
Each direction uses a query-only POST to `get_busses`. No login, checkout, hold,
payment or booking endpoint is involved. Changed or unresolved protocol, fare,
quantity or timing fails visibly.

Money retains the **RON total for the requested party**. EUR values are ECB
reference estimates, not supplier EUR prices. Quotes require revalidation before
booking. Source vehicle labels may be van/minivan; no larger vehicle is invented.
Remaining seats are observations, not holds. Quotes for the same service share
capacity across groups and receipt revisions.

Only the chosen complete journey's hotel, two ground services and onward flight
are committed atomically with the proposal, after scope, version, freshness and
constraint checks. Unused alternatives do not become inventory. Ground services
have no sourced road geometry or tracked position; map reference lines must not
imply either. No booking occurs.

The first complete successful typed plan saves its exact inputs in a separate
Romanian profile, atomically with the proposal and completed job. Failed,
cancelled, quote-only or unchosen attempts cannot replace it. Stable identity
binds the operation, reciprocal case/group, flight/date, party and route.
Readiness, deadline, budget and all saved inputs are checked against current
conditions before each new search.

**Review saved Romania inputs** retrieves the selected group's configuration
without contacting suppliers. The review shows exact times, baggage, margins,
source job and save time. Loading does not submit a search or change the group,
services or call allowance. A nonempty compatible draft requires explicit
replacement; changed scope/version or incompatible setup prevents loading.
Past dates remain exact for editing, not silently advanced.

An affirmative RO/EN command can explicitly reuse this saved Romanian package:
“Recover CASE for G1 using the saved Romanian hotel configuration with maximum
9 calls.” This needs **nine total calls**, including interpretation and the
eight-call fresh-source workflow. Default eight is never increased. Requests
to edit dates, rooms, bags, route or margins require typed review. Descriptive
source names, passenger labels, negated or conditional requests and another
profile do not authorize reuse. The voice startup receives only an identity
validity flag; the command interpreter receives a bounded reference summary.
Neither contains saved fares or authorizes spending.

Named Transfero, Direct Aeroport or Aro Palace requests cannot reuse UK inputs.
A valid operational report survives a known recovery setup block; unexpected
transaction errors roll it back. Persistence, review and command-to-worker flow
have offline test evidence; fresh-provider and spoken acceptance remain open.

## Dated discovery — 1 November 2026

Two query-only searches each returned 13 Direct Aeroport services for two adults.
Examples in Europe/Bucharest local time:

| Date and route | Schedule | Reported seats | Party fare |
| --- | --- | --- | --- |
| 2 November, OTP Arrivals → Hotel Aro Palace | 11:00–14:00 | 13 | RON 260 |
| 3 November, Hotel Aro Palace → OTP Departures | 06:00–08:30 | 19 | RON 260 |

Three production LiteAPI calls established property identity, rate and policy.
For **2–3 November, two adults, one room**: **EUR 114.48 + EUR 1.86 mandatory
excluded fees = EUR 116.34**. Published check-in was 15:00 without an end;
checkout cutoff was 12:00. The outbound fits that check-in; the return requires
checkout by 05:40 with the minimum boarding margin. An onward flight must still
be sourced, departing at or after 11:30; none was searched during discovery.

These are historical observations, not current inventory, bookings or a complete
application acceptance run. Ground fares totalled RON 520; no fresh ECB rate or
invented cross-currency total was added. Official
[LiteAPI usage guidance](https://docs.liteapi.travel/reference/api-pricing-usage-costs)
documents these hotel endpoints as free under standard usage conditions, not an
account-wide billing guarantee.

Integration tests use isolated state and mocked transports. Fresh-provider
acceptance is separately tracked in the [verification report](verification/report.md).
Historical quotes are never preloaded as fresh supply in the running application.

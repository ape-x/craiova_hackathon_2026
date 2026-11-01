# Simulation with live supplier data

`OPERATION_MODE=demo` starts a separate passenger-demand simulation with the same
real sourcing, quote freshness, itinerary validation and proposal flow as live
operations. The workspace labels it **Simulation · live data**.

Only the passengers and incident are fictional. The initial case contains two
adult planning passengers at Heathrow with destination Otopeni, tomorrow's UTC
readiness, a following-day deadline and a EUR 2,000 group budget. It contains no
flights, hotel rooms, vehicles, offers or reservations. Airports are real catalog
locations. The fictional incident has `source=demo_scenario` and no FR24 flight
reference; it is never represented as an observed cancellation or tracked flight.

Use **New test case** to create another mocked passenger list, with real airport
codes, explicit future UTC times and a budget. Case creation is local and makes
no supplier calls. Select its groups and use **Find sourced alternatives** to
request current quotes. Flight, hotel and transport searches use the configured
integrations and their existing access requirements and call limits. Missing
access, expired quotes, unsupported routes and provider failures remain visible.
No synthetic inventory is substituted.

The mode retains `world.mode=live` with `world.demo=true` so real sourcing rules
remain in force. Mocked groups retain `source=synthetic`; they cannot receive
external booking confirmations. Manifest and manual inventory imports are
disabled in this mode, as are real passenger notification deliveries. Simulated
scenario/reset endpoints remain unavailable. Historical or unknown airport
operational status is shown as reference information, not verified availability.

Start locally using a project name distinct from the real operation:

```sh
docker compose -p air-walkthrough-demo -f compose.yaml -f compose.demo.yaml up -d --build
```

Open http://localhost:8081/. Existing `air-walkthrough-demo` state from another
mode is archived before the new mode is initialized; mode changes preserve the
provider usage ledger. The original wholly synthetic development mode is still
available through `compose.simulation.yaml`. Never use the live operation's
Compose project name for the demo.

# New scenes and operational weather

The command bar and voice agent accept a complete new simulation in ordinary Romanian or English. A request to create a demonstration authorizes the agent to choose missing synthetic counts, fares, schedules and budgets; the verified response states those assumptions. Changing an existing scenario does not authorize silently inventing additional inventory.

To keep demonstrations separate from an instance using real inventory, run:

```sh
docker compose -p airport-recovery-simulation -f compose.yaml -f compose.simulation.yaml up -d --build
```

The simulator is at **http://localhost:8081** and has its own database volume. The distinct project name is required. It uses the server credentials already configured in `backend/.env`; those credentials are not copied into the browser. The regular instance on port 8080 keeps its current mode and inventory.

Example:

> Resetează complet simularea și creează de la zero un scenariu nou: furtună la Lisabona, aeroportul nu permite aterizări, iar un avion este deja în aer spre Lisabona cu pasageri la bord. Alege tu cea mai bună deviere și organizează transportul pasagerilor până la destinație.

Then change the conditions:

> Acum apare ceață densă și la Faro: nu se mai poate ateriza. Alege alt aeroport pentru avionul aflat încă în aer și recalculează transportul pasagerilor.

## Execution

1. Responses produces schema-constrained operations, including `scenario_reset` and `airport_weather`. New airports and nearby alternatives are grounded in the local geographic catalog.
2. The backend projects and validates the entire command before any mutation. A reset must be the first and only reset in the batch. Failed interpretation, validation or decision leaves the existing scene intact.
3. Every eligible diversion airport is evaluated against the current passenger and service constraints. OpenAI Decisions selects among validated candidates; the airport choice is not scripted.
4. One transaction commits the new scene, diversion, passenger offers and reservations. A new scene gets a new identity and passenger tokens; the world version remains monotonic. Retries use the original idempotency key.
5. The UI displays simulated weather, operational restrictions, route changes and the verified explanation. Voice uses this same command path.

`scenario_reset` accepts `empty` (new scene, retaining the simulation clock) or `seed` (initial demonstration scene). A standalone reset does not request a recovery decision. Observed traffic remains separate from synthetic inventory.

## Weather model

Each airport can have `condition` (`clear`, `fog`, `storm`, `snow`, `wind`, `rain`), optional `visibility_m` and `wind_kts`, and explicit `landing_allowed` / `departure_allowed` constraints. These are synthetic scenario inputs, not a live weather feed or aircraft flight physics. Measurements alone do not silently apply aviation thresholds.

Landing restrictions cause airborne arrivals to require diversion and exclude that airport from diversion choices. Future affected flights are held for weather. Clearing weather cannot reopen an explicitly closed airport or restore a manually cancelled flight. Restrictions persist until another command changes them; timed weather evolution is not modeled.

The model does not compute fuel endurance, aircraft-specific minima or revised flight durations from meteorology. Generated flight and transfer schedules are disclosed demo assumptions and remain subject to chronology/capacity/budget checks. Real inventory mode rejects synthetic reset and weather edits.

## Verification

The isolated live-provider test created a new scene with 60 passengers, Lisbon landing restrictions and Faro/Porto onward bus options. Six candidate plans passed validation. Decisions chose Faro and a €2,100 recovery plan covering all 60 passengers. Measured server total: 7.23 s, of which interpretation 6.73 s, solver 46.72 ms and Decisions 381.64 ms. These are observations from one run, not a latency guarantee.

A follow-up submitted through the browser introduced fog and blocked landings at Faro. The model moved the airborne flight to Porto and replaced the passenger offers with the existing Porto–Lisbon bus: 60 passengers, €2,700, three validated candidates, 2.90 s server total (Decisions 497.05 ms). No new transport inventory was invented for this update.

Offline regressions cover reset rollback, token invalidation, retry idempotency, weather exclusions, offer invalidation, restoration and UI state. Browser verification uses an isolated SQLite scene; the operator's current scene is not reset. Voice delegation is covered by protocol tests; this verification did not record the operator's microphone.

Structured output follows the [official OpenAI schema guidance](https://developers.openai.com/api/docs/guides/structured-outputs). Domain feasibility is independently checked after parsing.

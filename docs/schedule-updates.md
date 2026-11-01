# Schedule updates in the simulator

Natural-language delays use the current service state. A future service moves its departure and arrival by the requested delay. A flight already in the air keeps its actual departure and changes only its estimated arrival. Repeated delays build on the latest arrival time.

The previous interpretation instructions told the model to retain flight duration for every delay, which moved an airborne flight's departure and triggered the server's temporal-continuity guard. The prompt now distinguishes airborne and future services, and `resource_schedule` supports partial updates:

```json
{
  "type": "resource_schedule",
  "target_id": "F201",
  "value": { "departure": null, "arrival": 780 }
}
```

Both keys remain required for strict structured output. A null value preserves the corresponding current time; at least one value must be an absolute simulation minute. Existing payloads containing two integer times remain supported. An explicit request to change an airborne flight's departure is explained without mutation; the backend still rejects any such change that reaches it.

Weather-held flights have not departed and may receive a new departure strictly after the current simulation time, even if their old scheduled times have elapsed. Rescheduling preserves weather restrictions and never restores an explicitly cancelled flight. Completed flights and departed ground services remain immutable; an identical schedule is an audited no-op.

An arrival change updates passenger readiness and invalidates dependent offers and reservations before replanning. Compound commands remain atomic, including reset/create/update commands. Live inventory still requires provider or operator evidence and does not accept synthetic schedule edits.

Verification evidence is in [schedule-live-check.json](verification/schedule-live-check.json). Live-provider checks used an isolated synthetic database, with Flightradar and Maps disabled. The first case was submitted through the browser; follow-up cases used the same HTTP command endpoint. Microphone input was not recorded for these checks; voice delegation is covered by offline tests and uses the same command path.

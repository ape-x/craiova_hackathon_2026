# Voice control across the platform

The microphone and the command box use the same authenticated command dispatcher. The AI can call the operator's existing business APIs and request interface navigation. The backend advertises a named capability catalog with each operation's input schema. It does not execute model-generated URLs, code or database queries.

## Using it

Start the microphone once and speak naturally in Romanian or English. The active case, selected group and selected resource accompany each command, so references such as “this case” and “the selected group” do not require reading an internal ID aloud.

Examples:

- “Deschide un caz nou de simulare.”
- “Creează doi pasageri fictivi de la Heathrow la București. Sunt pregătiți mâine la 08:00 UTC, trebuie să ajungă poimâine până la 22:00 UTC, buget total 2.000 EUR.”
- “Caută zboruri pentru grupul selectat, cu cel mult opt apeluri în total.”
- “Arată progresul căutării pentru acest caz.”
- “Oprește căutarea [job reference].”
- “Arată hotelurile.”
- “Deschide căutarea de transferuri.”
- “Arată restricțiile pentru acest caz.”
- “Schimbă în vizualizarea de rețea și arată aeroportul Heathrow.”
- “Arată jurnalul deciziilor.”
- “Mărește cronologia.”
- “Exportă sesiunea.”

The assistant asks for missing required inputs. Follow-up answers remain in conversation context. Supplier searches and imports use the existing source verification, eligibility and freshness rules. Creating a fictional case is available in **Simulation · live data**; its passengers are fictional and supply is sourced through real integrations.

## Supported actions

The catalog includes case creation and flight lookup, passenger setup and constraints, recovery search configuration/status/cancellation, flight/hotel/coach/transfer searches and imports, operator reports and supplied booking evidence, inventory and audit inspection, notification operations, and traffic polling. Interface actions cover views and selection, all existing operation-data tabs, case/report panels, settings/help, passenger links, planner/theme selection, timeline controls, refresh and audit export.

Up to six named operations can run in one command. Later operations can refer to IDs returned by earlier operations. Each step retains its result; a failed step stops subsequent execution. Retries reuse the command and step identities, and an uncertain provider outcome is not silently repeated. The command reply reports actual outcomes. Detailed results open only when explicitly requested, without an automatic results popup or a persistent footer button.

An explicit external-call allowance includes the interpretation call. Direct provider calls share the remaining allowance, including fan-out, and queued recovery work reserves its own bounded allowance. Existing provider/account limits continue to apply.

## Boundaries

Access is the authenticated operator's access. The AI cannot change server credentials, bypass role checks, impersonate a passenger's acceptance, or manufacture supplier confirmation. Notification dispatch requires an explicit current request and recipient; demo mode keeps notifications disabled. The browser still requires the initial microphone permission. Navigation waits when an open form has an unresolved mutation.

UI actions are acknowledged only after the browser processes them. The assistant distinguishes a requested UI change, an executed operation, a queued search, a supplier quote and an externally confirmed booking.

# Google geographic 3D view

The operator explicitly selects the geographic view. Only mounting
`frontend/src/maps/GoogleMaps3D.tsx` loads Google Maps. The default procedural
simulation view makes no Google API request. A singleton dynamic-import bootstrap
loads the current `weekly` JavaScript API and requests `maps3d` and `marker` using
`google.maps.importLibrary()`. No deprecated npm `Loader` class is used.

Component props: `{ world, mode, selectedId, onSelect, apiKey, onClose? }`, where
`mode` is `airport | region | passenger`. The operator controls the toggle. Unmount removes
the map and overlays; the single shared API loader remains available for a later
explicit toggle. World changes update overlays without reloading the API.

## Configuration and verification

Set `GOOGLE_MAPS_API_KEY` on the backend; `/api/config` exposes this specifically
public browser key. It is expected to appear in the browser's Google script URL.
Use a dedicated key restricted to the permitted HTTP referrer origins and Maps
JavaScript API. Enable the required API and billing in the team's Google Cloud
project. Do not put server OpenAI or FR24 credentials in this field.

The component catches script failures, authentication failure, `gmp-error` and
a render timeout. It reports ready only after the map's `gmp-steadychange` event
has `isSteady=true`. A successful library load alone does not prove that tiles
rendered. Errors stay visible; no alternate imagery or simulated basemap is
silently displayed under a Google label.

Tests cover loader behavior and state-derived overlays with mocked libraries.
An authorized Chrome check on 2026-10-31 successfully rendered real Google satellite
imagery and terrain at CRA with labeled simulation routes and markers; see the
[verification report](verification/report.md). Detailed terminal photogrammetry,
authored GLB alignment/rendering and an actual FR24 overlay remain unverified.
Each deployment needs its own restricted browser key, enabled API and valid billing.
Follow-up checks: open geographic view, confirm real terrain appears, pan/zoom, select CRA
and OTP, introduce a bus failure, confirm red scenario links, and inspect one real
aircraft observation plus its source timestamp. Check attribution remains visible
on the presentation screen. The application does not provision a Google Cloud
account or activate billing; account administration remains with the owner.

## Source meanings

- Google supplies terrain and available imagery. 3D building detail depends on
  geographic coverage; this implementation does not claim that Craiova terminal
  buildings or interiors are available as photogrammetry.
- Airport markers use the scenario's geographic coordinates. Airport closure
  labels are simulation state, not a claim about the real airport's operation.
- Cyan aircraft markers use received FR24 coordinates and source timestamps.
  Feet are converted to meters for the map. Missing altitude is not invented.
  Older points are visibly marked stale; static provider samples say SANDBOX.
- Cyan trails connect only positions received while this view is mounted, capped
  at 30 source samples per aircraft. They are not provider flight plans or predicted
  trajectories. Stale, replay, sandbox and unknown-time trails use the same muted
  color as their markers. No history endpoint or extra traffic API calls are made.
- Amber dashed connectors are schematic simulator network links. Green connectors
  have confirmed inventory in the simulator. Red links represent unavailable
  resources. They are **not road directions, flight plans or safe routing advice**.
- A selected simulated vehicle marker follows the simulator's timetable along a
  schematic link. Its label says SIM; it is not a GPS measurement. Passenger
  markers indicate the synthetic group's scenario origin, not a live location.

## Authored 3D vehicle models

The geographic view creates actual `Model3DElement` overlays with the authored
core-PBR GLB assets in `frontend/public/scene/models/`. They use meters, Y-up and
front/nose -Z, with no glTF extensions or external texture resources. The matching
source marker remains clickable and carries the observation timestamp or SIM label.

At most 20 received aircraft models are shown, prioritizing the selected aircraft.
Models require both a finite provider altitude and direction; otherwise only the
source marker is retained. Provider feet convert to meters without replacing the
value. Direction is a source heading/track, not a measured pitch or roll: models
use a neutral level posture. Generic aircraft shapes do not identify the actual
airframe. Replay, sandbox and stale positions remain explicitly labeled on markers.

Only the selected simulated flight or bus receives a vehicle model. Its position
and bearing follow the scenario's schematic route, not GPS or road directions.
Aircraft models are enlarged 2.5× and buses 4× for presentation visibility, as the
legend discloses. Their dimensions are not a geographic footprint measurement.

Models and their assets load only after the operator enables the geographic view
and Google reports a steady map. Each local GLB is checked once per page for HTTP
success and a valid binary header, with a 10-second fetch timeout. Construction
and asset errors produce a visible warning while source markers/routes remain.
Google documents no individual model-load event: a successful binary check does
not prove GPU rendering. Model elements are removed on updates/unmount, and late
asset completion cannot append to a disposed map.

Mock tests cover source-faithful descriptors, the 20-aircraft cap, missing pose,
lazy binary checks, failure reporting and cleanup before/after asset resolution.
With the team's key, additionally verify that the authored -Z-forward aircraft
faces north at heading 0, that observed heading changes rotate correctly, and that
the selected bus rests on terrain. Exact asset alignment and rendering have not
been live-verified, despite the successful basic map render.

The component does not download, scrape or cache map imagery, use Map Tiles API,
or attempt to bypass geographic/product restrictions. Google attribution and
default map UI remain intact. Application overlays sit above the bottom attribution
area; keep at least the lowest 32 px clear. Account terms, quotas and
actual usage still apply. There is no claim that all map loads are free.

Official documentation checked 2026-10-31:

- [Dynamic library import](https://developers.google.com/maps/documentation/javascript/load-maps-js-api)
- [Map3DElement, camera and events](https://developers.google.com/maps/documentation/javascript/reference/3d-map)
- [Interactive markers and 3D paths](https://developers.google.com/maps/documentation/javascript/reference/3d-map-draw)
- [3D models and supported GLB format](https://developers.google.com/maps/documentation/javascript/3d/models)
- [Geographic position and orientation](https://developers.google.com/maps/documentation/javascript/reference/coordinates)
- [Marker customization](https://developers.google.com/maps/documentation/javascript/3d/marker-customization)
- [3D coverage](https://developers.google.com/maps/documentation/javascript/3d/coverage)
- [Authentication failure events](https://developers.google.com/maps/documentation/javascript/events)
- [API key restrictions](https://developers.google.com/maps/documentation/javascript/get-api-key#restrict_key)

# Google geographic 3D view

The operator explicitly selects the geographic view. Only mounting
`frontend/src/maps/GoogleMaps3D.tsx` loads Google Maps. The default procedural
simulation view makes no Google API request. A singleton dynamic-import bootstrap
loads the current `weekly` JavaScript API and requests `maps3d` and `marker` using
`google.maps.importLibrary()`. No deprecated npm `Loader` class is used.

Component props: `{ world, mode, selectedId, onSelect, apiKey, onClose? }`, where
`mode` is `airport | region | passenger`. The operator controls the view toggle.
Unmount removes the map, model elements and listeners; the single shared API loader
remains available for a later explicit toggle. World changes update overlays
without reloading the API. Vehicles, Passengers and Terminal controls independently
toggle the 3D object layers. Vehicles includes both simulated services and received
aircraft models. These controls leave source markers and routes available and do
not change the backend's traffic polling state.

## Configuration and verification

Set `GOOGLE_MAPS_API_KEY` on the backend; `/api/config` exposes this specifically
public browser key. It is expected to appear in the browser's Google script URL.
Use a dedicated key restricted to the permitted HTTP referrer origins and Maps
JavaScript API. Enable the required API and billing in the team's Google Cloud
project. Do not put server OpenAI or FR24 credentials in this field.

The browser credential and `GOOGLE_MAPS_SERVER_API_KEY` must be separate: their
application/API restrictions serve different callers. Follow Google's
[API-key restrictions guidance](https://developers.google.com/maps/documentation/javascript/get-api-key#restrict_key)
and verify both callers after changing credentials. A local audit on 1 November
2026 found that the configured browser and server values were identical. No key
values were included in the audit or changed. Google Cloud Console did not load
its administration view; its DOM reported a `gstatic.com` JavaScript loading
failure and its visible shell remained on the loading logo. Billing health and
actual account restrictions therefore remain unverified. The shared key must be
replaced with separate restricted credentials before closing this production
gate; successful terrain rendering alone does not close it.

The component catches script failures, authentication failure, `gmp-error` and
a render timeout. It reports ready only after the map's `gmp-steadychange` event
has `isSteady=true`. A successful library load alone does not prove that tiles
rendered. Errors stay visible; no alternate imagery or simulated basemap is
silently displayed under a Google label.

Tests cover loader behavior and state-derived overlays with mocked libraries.
An authorized Chrome check on 2026-10-31 successfully rendered real Google satellite
imagery and terrain at CRA with labeled simulation routes and markers; see the
[verification report](verification/report.md). A subsequent authorized Chrome
check found 96 authored models, upright visible geometry and no map warnings.
The revised CRA apron anchor and upright geometry are visible in
[the live model screenshot](verification/google-models-live.jpg). Surveyed terminal
photogrammetry, exact model alignment and an actual FR24 overlay remain unverified. Successful model rendering does not establish a surveyed airport replica.
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
- Airport labels identify the authored simulation campus, registered near CRA's
  commercial apron or placed relative to other scenario airports' coordinates.
  The campus is not a surveyed terminal location.
  Airport closure labels are simulation state, not a claim about the real airport's
  operation.
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
- Simulated vehicles follow the simulator's timetable along schematic links.
  Passenger positions follow their scenario origin and confirmed itinerary as
  simulation time advances. Labels say SIM; neither is a GPS measurement or a
  live passenger location. All passenger groups in the demonstration are synthetic.

## Authored terminal, fleet and families

The geographic view mounts authored core-PBR GLBs from
`frontend/public/scene/models/`: `aircraft.glb`, `bus.glb`, `terminal.glb` and
`passengers-1.glb` through `passengers-5.glb`. They use meters, +Y up and front/nose
-Z, with no glTF extensions or external texture resources. The manifest records
asset bounds, anchors and structural validation. It does not establish that Google
has rendered the models.

All valid simulated flight and bus resources receive a model, without requiring
selection. These represent scheduled services, not independently tracked physical
airframes or buses. Participating scenario airports receive an authored terminal. Every
synthetic family with a valid scenario position receives one to five representative
figures; larger families retain their full count in the label. During an active,
available confirmed transport leg, the family is represented by its vehicle rather
than a cluster of people beside an airborne aircraft or moving bus. Simulated
models use scale 1. Their authored dimensions are not a measurement of a real
airport, vehicle or person.

`frontend/src/scene/layout.ts` supplies the shared native-view stand, bus-bay and
family offsets. `frontend/src/maps/placement.ts` projects those meter offsets using
an airport-specific anchor and heading. Before rotation, +X is east and +Z is south.
CRA uses `CRA_CAMPUS = { lat: 44.31455, lng: 23.87705, heading: 90 }`: rotating the
common layout puts its aircraft stands along a north–south strip near Apron 1's
charted stand longitude, approximately 23.87764° E, with the authored terminal and
forecourt to the west. The registration references the
[ROMATSA AIP amendment and Craiova apron chart](https://aisro.ro/files/amdt/LR_Amdt_A_2024_04_en.pdf)
and [OpenStreetMap apron outline](https://www.openstreetmap.org/way/792967078).
These are placement references; the chosen anchor and authored building dimensions
are presentation choices, not surveyed stand coordinates or a terminal replica.

Other airports retain the generic anchor 200 meters south of their scenario airport
reference, with heading 0. The terminal asset is rebased at its native anchor
before export so that placement does not apply the same offset twice. Model
headings for the parked campus also account for the airport's layout rotation;
received aircraft retain their provider heading. The authored layout remains a
simulation over real terrain.

Available vehicles park at their origin before departure and at their destination
after arrival. Between those times they interpolate continuously along the
schematic connection. Flights use a synthetic arc above terrain; buses remain
ground-clamped. Unavailable services use their origin stand. This is scenario
presentation behavior, not a claim about a broken-down vehicle's actual position.

Where the loaded Google library supports `Model3DInteractiveElement`, clicking a
model selects its airport, resource, family or observation. The layer otherwise
uses `Model3DElement`; source markers and the operator panels retain selection.
Observation and simulation IDs occupy separate keys. Selection does not remove
the rest of the fleet.

At most 20 received aircraft models are shown, prioritizing the selected aircraft.
Models require both a finite provider altitude and direction; otherwise only the
source marker is retained. Provider feet convert to meters without replacing the
value and are passed to Google's `ABSOLUTE` altitude mode. No terrain or pressure
correction is invented. Direction is a source heading/track, not a measured pitch
or roll. Generic aircraft shapes do not identify the actual airframe. Received
aircraft models are enlarged 2.5×, disclosed in the legend and model title; they are
not a physical footprint measurement. Replay, sandbox and stale positions remain
explicitly labeled on markers.

## Axes, altitude and camera

Models apply `{ heading: bearing, tilt: 270, roll: 0 }`. The fixed tilt adapts the
authored asset axes; it is not a measurement of aircraft pitch. Google's
[orientation reference](https://developers.google.com/maps/documentation/javascript/reference/coordinates#Orientation3D)
defines heading around the vertical Z axis and tilt around X. Its
[model example](https://developers.google.com/maps/documentation/javascript/3d/models)
also applies tilt 270. Together these support mapping asset +Y to the map's
vertical and asset -Z to north before applying heading. This is the implementation's
coordinate rationale, not a live rendering result: verify that heading 0 faces
north and heading 90 faces east in the actual browser.

Parked vehicles, terminals and families use `CLAMP_TO_GROUND`. Simulated flight
arcs, scenario routes and label offsets use `RELATIVE_TO_GROUND`; received aircraft
with altitude use `ABSOLUTE`. Clamping follows terrain, not the tops of buildings,
so placing an authored model inside real imagery can cause occlusion. It does not
make the authored campus a measured map feature.

The [camera reference](https://developers.google.com/maps/documentation/javascript/reference/3d-map#CameraOptions)
defines the default center altitude relative to mean sea level. Omitting altitude
does not promise terrain sampling. The constructor therefore starts at a wide
range of at least 6.5 km. Once Google reports a steady map, `flyCameraTo` applies
`RELATIVE_TO_GROUND` to simulated campus and passenger targets, avoiding a
sea-level target beneath elevated terrain. The campus view is approximately
650 m from its target, bus focus 260 m, flight focus 550 m and passenger view 180 m.
Region mode remains a broad network view. Received aircraft use their reported
absolute position for focus. Camera errors stay visible and manual controls remain
available.

## Loading, updates and verification

Models and their assets load only after the operator enables the geographic view
and Google reports a steady map. Each successful local GLB check is cached per page;
it requires HTTP success, a valid GLB 2 header and a size up to 2 MB, with a
10-second fetch timeout. A failed check may retry when the geographic view is
explicitly reopened. Construction and asset errors produce a visible warning while source
markers/routes remain. Google documents no individual model-load event: a
successful binary check does not prove GPU rendering.

The keyed model layer updates position, orientation, scale, altitude mode and
metadata in place. Ordinary world updates do not detach and reload every model.
Changing an asset replaces that entity's shape; removing an entity or disabling
its layer disposes its element and click listener. Late asset completion cannot
append to a disposed map, and a pending load applies the latest descriptor rather
than an obsolete pose. Unmount disposes the complete layer.

Automated verification uses mocked Google libraries and local asset checks. It
does not establish real Google model rendering or photogrammetry coverage. Before
claiming visual completion, inspect the full scene with no vehicle selected, toggle
each object layer, click a bus and family, check north/east model orientation,
inspect terminal and wheel/foot contact with terrain, and advance simulation time
to compare models, markers and camera targets. Also inspect a real observation's
timestamp and altitude when FR24 credentials become available. Record live evidence
in the verification report; the successful basic map render alone is insufficient.

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
- [Terrain and mesh altitude modes](https://developers.google.com/maps/architecture/3d-understand-altitude-modes)
- [Marker customization](https://developers.google.com/maps/documentation/javascript/3d/marker-customization)
- [3D coverage](https://developers.google.com/maps/documentation/javascript/3d/coverage)
- [Authentication failure events](https://developers.google.com/maps/documentation/javascript/events)
- [API key restrictions](https://developers.google.com/maps/documentation/javascript/get-api-key#restrict_key)

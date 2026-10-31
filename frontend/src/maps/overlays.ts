import type { Airport, Observation, SceneMode, World } from '../types';
import { airportWeatherState } from '../weather';
import { parkedPoint, passengerPoint, resourcePoint, sceneHeading, scenePoint } from './placement';
export { resourcePoint } from './placement';
import type { Camera, GeoPoint } from './contracts';

export const COLORS = {
  observed: '#58ddeb',
  scenario: '#f8bb69',
  confirmed: '#6fe2b6',
  unavailable: '#f3717a',
  muted: '#a3b4c4',
};
export interface GeoMarker {
  id: string;
  label: string;
  title: string;
  point: GeoPoint;
  color: string;
  observed: boolean;
  selected: boolean;
}
export interface GeoRoute {
  id: string;
  path: GeoPoint[];
  color: string;
  dashed: boolean;
  observed: boolean;
  selected: boolean;
}
export interface TrackPoint extends GeoPoint {
  timestamp: string;
}
export type ObservationTracks = Map<string, TrackPoint[]>;

export function pointOf(airport: Airport): GeoPoint {
  return { lat: airport.lat, lng: airport.lon, altitude: 0 };
}
export function observedPoint(observation: Observation): GeoPoint | null {
  const lat = observation.lat ?? observation.latitude,
    lng = observation.lon ?? observation.longitude;
  if (
    typeof lat !== 'number' ||
    typeof lng !== 'number' ||
    !Number.isFinite(lat + lng) ||
    Math.abs(lat) > 90 ||
    Math.abs(lng) > 180
  )
    return null;
  const feet = observation.altitude_ft ?? observation.alt ?? observation.altitude;
  return {
    lat,
    lng,
    ...(typeof feet === 'number' && Number.isFinite(feet) ? { altitude: feet * 0.3048 } : {}),
  };
}

export function updateObservationTracks(
  tracks: ObservationTracks,
  observations: Observation[],
): void {
  const current = new Set<string>();
  for (const observation of observations) {
    const id = String(observation.id ?? observation.fr24_id ?? '');
    const timestamp = observation.observed_at ?? observation.timestamp;
    const point = observedPoint(observation);
    if (!id || !point || !timestamp || !Number.isFinite(Date.parse(timestamp))) continue;
    current.add(id);
    const history = tracks.get(id) ?? [];
    if (!history.some((sample) => sample.timestamp === timestamp)) {
      history.push({ ...point, timestamp });
      history.sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp));
    }
    tracks.set(id, history.slice(-30));
  }
  for (const id of tracks.keys()) if (!current.has(id)) tracks.delete(id);
}

/** Schematic network connector, NOT a navigable road route or flight plan. */
export function schematicPath(
  origin: GeoPoint,
  destination: GeoPoint,
  flight: boolean,
): GeoPoint[] {
  return Array.from({ length: 25 }, (_, index) => {
    const t = index / 24;
    return {
      lat: origin.lat + (destination.lat - origin.lat) * t,
      lng: origin.lng + (destination.lng - origin.lng) * t,
      altitude: flight ? Math.sin(t * Math.PI) * 9000 : 0.5,
    };
  });
}

export function buildOverlays(
  world: World,
  selectedId: string | null,
  tracks: ObservationTracks,
  now = Date.now(),
): { markers: GeoMarker[]; routes: GeoRoute[] } {
  const markers: GeoMarker[] = world.airports.map((airport) => ({
    id: airport.id,
    label: `${airport.code}${!airport.available ? ' · CLOSED IN SIM' : airportWeatherState(airport).weatherRestricted ? ' · WEATHER RESTRICTED' : ''}`,
    title: `${airport.name}. Airport availability is simulated.${airport.weather ? ` ${airportWeatherState(airport).condition}. Landings ${airportWeatherState(airport).landingAllowed ? 'allowed' : 'blocked'}; departures ${airportWeatherState(airport).departureAllowed ? 'allowed' : 'blocked'}.` : ''}`,
    point: scenePoint(airport, 0, 51, 28),
    color: airportWeatherState(airport).restricted ? COLORS.unavailable : COLORS.scenario,
    observed: false,
    selected: selectedId === airport.id,
  }));
  const routes: GeoRoute[] = [];
  const active = world.offers.filter(
    (offer) => offer.status === 'held' || offer.status === 'confirmed',
  );
  const selectedGroupOffer = active.find((offer) => offer.group_id === selectedId);
  const selectedResources = new Set(
    selectedGroupOffer?.itinerary.legs.map((leg) => leg.resource_id) ?? [],
  );
  if (selectedId) selectedResources.add(selectedId);
  // Draw a single representative route per origin/destination/type/status. A
  // selected resource remains distinguishable even when parallel services exist.
  const drawn = new Set<string>();
  for (const resource of [...world.resources].sort(
    (a, b) => Number(selectedResources.has(b.id)) - Number(selectedResources.has(a.id)),
  )) {
    if (resource.kind === 'hotel' || resource.origin === resource.destination) continue;
    const origin = world.airports.find((airport) => airport.id === resource.origin),
      destination = world.airports.find((airport) => airport.id === resource.destination);
    if (!origin || !destination) continue;
    const confirmed = active.some(
      (offer) =>
        offer.status === 'confirmed' &&
        offer.itinerary.legs.some((leg) => leg.resource_id === resource.id),
    );
    const selected = selectedResources.has(resource.id);
    const key = `${resource.origin}:${resource.destination}:${resource.kind}:${resource.available}:${confirmed}`;
    if (drawn.has(key) && !selected) continue;
    drawn.add(key);
    routes.push({
      id: resource.id,
      path: schematicPath(
        parkedPoint(resource, world, origin),
        parkedPoint(resource, world, destination),
        resource.kind === 'flight',
      ),
      color: !resource.available
        ? COLORS.unavailable
        : confirmed
          ? COLORS.confirmed
          : COLORS.scenario,
      dashed: !confirmed,
      observed: false,
      selected,
    });
  }
  const selectedResource = world.resources.find((resource) => resource.id === selectedId);
  if (selectedResource) {
    const point = resourcePoint(selectedResource, world);
    if (point)
      markers.push({
        id: selectedResource.id,
        label: `${selectedResource.id} · SIM`,
        title: `${selectedResource.id}: illustrative scenario position, not a GPS observation.`,
        point: {
          ...point,
          altitude: (point.altitude ?? 0) + (selectedResource.kind === 'flight' ? 18 : 8),
        },
        color: selectedResource.available ? COLORS.scenario : COLORS.unavailable,
        observed: false,
        selected: true,
      });
  }
  for (const resource of world.resources) {
    if (resource.kind === 'hotel' || (resource.available && resource.id !== selectedId)) continue;
    const point = resourcePoint(resource, world);
    if (!point) continue;
    const radius = resource.kind === 'flight' ? 27 : 12;
    routes.push({
      id: `selection-${resource.id}`,
      path: Array.from({ length: 41 }, (_, index) => {
        const theta = (index / 40) * Math.PI * 2;
        return {
          lat: point.lat + (Math.cos(theta) * radius) / 111320,
          lng:
            point.lng +
            (Math.sin(theta) * radius) / (111320 * Math.cos((point.lat * Math.PI) / 180)),
          altitude: (point.altitude ?? 0) + 0.6,
        };
      }),
      color: resource.available ? COLORS.confirmed : COLORS.unavailable,
      dashed: false,
      observed: false,
      selected: true,
    });
  }
  const selectedGroup = world.groups.find((group) => group.id === selectedId);
  if (selectedGroup) {
    const point = passengerPoint(selectedGroup, world);
    if (point)
      markers.push({
        id: selectedGroup.id,
        label: `${selectedGroup.name} · SIM`,
        title: `${selectedGroup.size} synthetic passengers; scenario origin, not a physical location.`,
        point: { ...point, altitude: (point.altitude ?? 0) + 5 },
        color: selectedGroup.status === 'confirmed' ? COLORS.confirmed : COLORS.scenario,
        observed: false,
        selected: true,
      });
  }
  for (const observation of world.observations) {
    const point = observedPoint(observation);
    const id = String(observation.id ?? observation.fr24_id ?? '');
    const timestamp = observation.observed_at ?? observation.timestamp;
    if (!point || !id) continue;
    const age = timestamp ? Math.floor((now - Date.parse(timestamp)) / 1000) : NaN;
    const sandbox = observation.mode === 'SANDBOX';
    const replay = observation.mode === 'REPLAY';
    const knownTime = Number.isFinite(age) && age >= -60;
    const provenance = sandbox
      ? 'SANDBOX'
      : replay
        ? 'REPLAY'
        : knownTime
          ? age > 90
            ? 'STALE OBS'
            : 'OBSERVED'
          : 'TIME UNKNOWN';
    const observationColor =
      sandbox || replay || !knownTime || age > 90 ? COLORS.muted : COLORS.observed;
    markers.push({
      id,
      label: `${observation.callsign ?? observation.flight ?? id} · ${provenance}`,
      title: `${observation.source ?? 'External source'} · ${timestamp ?? 'no source timestamp'}${knownTime ? ` · ${Math.max(0, age)}s old` : ''}. ${sandbox ? 'Static provider sample.' : 'Last received position; altitude follows provider data.'}`,
      point,
      color: observationColor,
      observed: true,
      selected: selectedId === id,
    });
    const history = tracks.get(id);
    if (history && history.length > 1)
      routes.push({
        id: `observed-${id}`,
        path: history,
        color: observationColor,
        dashed: false,
        observed: true,
        selected: selectedId === id,
      });
  }
  return { markers, routes };
}

export function cameraFor(world: World, mode: SceneMode, selectedId: string | null): Camera {
  const home = world.airports.find((airport) => airport.id === 'CRA') ?? world.airports[0];
  const fallback = home ? pointOf(home) : { lat: 44.3181, lng: 23.8886, altitude: 0 };
  const observation = world.observations.find(
    (o) => String(o.id ?? o.fr24_id ?? '') === selectedId,
  );
  const observed = observation && observedPoint(observation);
  if (observed)
    return {
      center: observed,
      range: 4000,
      tilt: 60,
      heading: 20,
      altitudeMode: 'ABSOLUTE',
    };
  const airport = world.airports.find((a) => a.id === selectedId);
  if (airport && mode === 'airport')
    return {
      center: scenePoint(airport, 0, 45),
      range: 650,
      tilt: 58,
      heading: sceneHeading(airport ?? home) + 12,
      altitudeMode: 'RELATIVE_TO_GROUND',
    };
  const resource = world.resources.find((r) => r.id === selectedId);
  if (resource && mode !== 'region')
    return {
      center: resourcePoint(resource, world) ?? fallback,
      range: resource.kind === 'flight' ? 550 : resource.kind === 'bus' ? 260 : 650,
      tilt: 60,
      heading: sceneHeading(world.airports.find((a) => a.id === resource.origin)) + 25,
      altitudeMode: 'RELATIVE_TO_GROUND',
    };
  if (mode === 'region') {
    const regional = world.airports.filter((a) => a.diversion_eligible || a.id === 'CRA');
    const points = regional.length ? regional.map(pointOf) : [fallback];
    return {
      center: {
        lat: points.reduce((sum, p) => sum + p.lat, 0) / points.length,
        lng: points.reduce((sum, p) => sum + p.lng, 0) / points.length,
        altitude: 0,
      },
      range: 330000,
      tilt: 25,
      heading: 0,
    };
  }
  const group = world.groups.find((g) => g.id === selectedId);
  return {
    center: group
      ? (passengerPoint(group, world) ?? fallback)
      : home
        ? scenePoint(home, mode === 'passenger' ? -50 : 0, mode === 'passenger' ? 165 : 45)
        : fallback,
    range: mode === 'passenger' ? 180 : 650,
    tilt: 58,
    heading: sceneHeading(airport ?? home) + 12,
    altitudeMode: 'RELATIVE_TO_GROUND',
  };
}

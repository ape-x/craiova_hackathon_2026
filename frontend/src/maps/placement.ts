import type { Airport, PassengerGroup, Resource, World } from '../types';
import type { GeoPoint } from './contracts';
import { gates, busSpots, groupPosition } from '../scene/layout';

// Presentation registration against commercial Apron 1, not the runway ARP.
// ROMATSA Apron 1 stands run north–south near 23.87764 E. Rotating the shared
// layout 90° places its three airside stands on that strip, with the authored
// terminal/forecourt to the west. The building itself is not a surveyed replica.
// https://aisro.ro/files/amdt/LR_Amdt_A_2024_04_en.pdf
export const CRA_CAMPUS = { lat: 44.31455, lng: 23.87705, heading: 90 };
export const sceneHeading = (airport?: Airport): number =>
  airport?.id === 'CRA' ? CRA_CAMPUS.heading : 0;

/** Geographic projection of the authored campus. This is a SIM layout, not GPS. */
export function scenePoint(airport: Airport, x = 0, z = 0, altitude = 0): GeoPoint {
  const radians = Math.PI / 180;
  const anchor =
    airport.id === 'CRA' ? CRA_CAMPUS : { lat: airport.lat - 200 / 111320, lng: airport.lon };
  const heading = sceneHeading(airport) * radians;
  const east = x * Math.cos(heading) - z * Math.sin(heading);
  const north = -x * Math.sin(heading) - z * Math.cos(heading);
  return {
    lat: anchor.lat + north / 111_320,
    lng: anchor.lng + east / (111_320 * Math.cos(anchor.lat * radians)),
    altitude,
  };
}

export function parkedPoint(resource: Resource, world: World, airport: Airport): GeoPoint {
  const peers = world.resources.filter(
    (r) => r.kind === resource.kind && (r.origin === airport.id || r.destination === airport.id),
  );
  const index = Math.max(
    0,
    peers.findIndex((r) => r.id === resource.id),
  );
  if (resource.kind === 'flight') {
    const [x, , z] = gates[index % gates.length];
    return scenePoint(airport, x, z - Math.floor(index / gates.length) * 80);
  }
  if (resource.kind === 'bus') {
    const [x, , z] = busSpots[index % busSpots.length];
    return scenePoint(airport, x, z + Math.floor(index / busSpots.length) * 45);
  }
  return scenePoint(airport, 150, 100);
}

export function resourceProgress(resource: Resource, world: World): number {
  if (!resource.available || resource.kind === 'hotel') return 0;
  return Math.max(
    0,
    Math.min(
      1,
      (world.sim_time - resource.departure) / Math.max(1, resource.arrival - resource.departure),
    ),
  );
}

export function resourcePoint(resource: Resource, world: World): GeoPoint | null {
  const origin = world.airports.find((a) => a.id === resource.origin);
  const destination = world.airports.find((a) => a.id === resource.destination);
  if (!origin) return null;
  const start = parkedPoint(resource, world, origin);
  if (!destination) return start;
  const progress = resourceProgress(resource, world);
  if (progress === 0) return start;
  const end = parkedPoint(resource, world, destination);
  if (progress === 1) return end;
  return {
    lat: start.lat + (end.lat - start.lat) * progress,
    lng: start.lng + (end.lng - start.lng) * progress,
    // Simulated aircraft use height above terrain, so departure/arrival touch ground.
    altitude: resource.kind === 'flight' ? Math.sin(progress * Math.PI) * 9000 : 0,
  };
}

export function passengerPoint(group: PassengerGroup, world: World): GeoPoint | null {
  const origin = world.airports.find((a) => a.id === group.origin);
  if (!origin) return null;
  const index = world.groups
    .filter((g) => g.origin === group.origin)
    .findIndex((g) => g.id === group.id);
  const [x, , z] = groupPosition(Math.max(0, index));
  const waiting = scenePoint(origin, x, z);
  const offer = world.offers.find((o) => o.group_id === group.id && o.status === 'confirmed');
  if (!offer) return waiting;
  let position = waiting;
  for (const leg of offer.itinerary.legs) {
    if (world.sim_time < leg.departure) break;
    const resource = world.resources.find((r) => r.id === leg.resource_id);
    if (resource) position = resourcePoint(resource, world) ?? position;
    if (world.sim_time < leg.arrival) break;
  }
  return position;
}

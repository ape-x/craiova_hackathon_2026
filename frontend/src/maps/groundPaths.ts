import type { Resource, World } from '../types';
import type { GeoPoint } from './contracts';
import { sourceInstant } from '../resourceFreshness';
import { airportArrivalEvidence } from '../airportArrival';
import { isAirportCab } from '../airportCabPresentation';

/** Decode Google's 1e5 polyline format with bounded input, work and coordinates. */
export function decodeRoadPolyline(encoded: unknown): GeoPoint[] | null {
  if (typeof encoded !== 'string' || !encoded.length || encoded.length > 100_000) return null;
  const text = encoded;
  let cursor = 0,
    latitude = 0,
    longitude = 0;
  const points: GeoPoint[] = [];
  function delta(): number | null {
    let value = 0,
      shift = 0;
    while (cursor < text.length && shift < 30) {
      const byte = text.charCodeAt(cursor++) - 63;
      if (byte < 0 || byte > 63) return null;
      value += (byte & 31) * 2 ** shift;
      if (byte < 32) return value % 2 ? -(Math.floor(value / 2) + 1) : Math.floor(value / 2);
      shift += 5;
    }
    return null;
  }
  while (cursor < encoded.length) {
    if (points.length >= 4096) return null;
    const dlat = delta(),
      dlng = delta();
    if (dlat === null || dlng === null) return null;
    latitude += dlat;
    longitude += dlng;
    const point = { lat: latitude / 1e5, lng: longitude / 1e5, altitude: 0 };
    if (
      !Number.isFinite(point.lat + point.lng) ||
      Math.abs(point.lat) > 90 ||
      Math.abs(point.lng) > 180
    )
      return null;
    points.push(point);
  }
  return points.length >= 2 ? points : null;
}

function object(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}
function coordinates(value: unknown): GeoPoint | null {
  const point = object(value);
  return typeof point?.latitude === 'number' &&
    typeof point.longitude === 'number' &&
    Number.isFinite(point.latitude + point.longitude) &&
    Math.abs(point.latitude) <= 90 &&
    Math.abs(point.longitude) <= 180
    ? { lat: point.latitude, lng: point.longitude, altitude: 0 }
    : null;
}
function distance(a: GeoPoint, b: GeoPoint) {
  const rad = Math.PI / 180;
  const dlat = (b.lat - a.lat) * rad,
    dlng = (b.lng - a.lng) * rad;
  const value =
    Math.sin(dlat / 2) ** 2 +
    Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dlng / 2) ** 2;
  return 12_742_000 * Math.asin(Math.sqrt(Math.min(1, Math.max(0, value))));
}

export function reportedAirportDropoff(
  resource: Resource,
  world: Pick<World, 'airports' | 'epoch'>,
) {
  const arrival = airportArrivalEvidence(resource.airport_arrival_assumption);
  if (
    !arrival ||
    arrival.airport_id !== resource.destination ||
    resource.initial_position_assumption != null ||
    world.airports.find((node) => node.id === resource.origin)?.kind !== 'hotel' ||
    world.airports.find((node) => node.id === resource.destination)?.kind !== 'airport' ||
    sourceInstant(arrival.epoch) !== sourceInstant(world.epoch)
  )
    return null;
  const point = coordinates(arrival.dropoff.coordinates);
  return point ? { point, label: arrival.dropoff.label } : null;
}

export function groundPath(
  resource: Resource,
  world: Pick<World, 'airports' | 'epoch'>,
): { path: GeoPoint[]; geometry: 'sourced_road' | 'connection_only' } | null {
  if (
    resource.transport_mode !== 'private_transfer' &&
    resource.price_evidence?.kind !== 'apl_ecb_reference'
  )
    return null;
  const origin = world.airports.find((node) => node.id === resource.origin),
    destination = world.airports.find((node) => node.id === resource.destination);
  if (!origin || !destination) return null;
  const dropoff = reportedAirportDropoff(resource, world);
  if (resource.airport_arrival_assumption != null && !dropoff) return null;
  const start = coordinates({ latitude: origin.lat, longitude: origin.lon }),
    end = dropoff?.point ?? coordinates({ latitude: destination.lat, longitude: destination.lon });
  if (!start || !end) return null;
  const fallback = { path: [start, end], geometry: 'connection_only' as const };
  // CAB's source contract has a duration estimate, but no road geometry.
  if (isAirportCab(resource)) return fallback;
  const route = object(resource.provenance?.google_route),
    request = object(route?.request),
    road = object(route?.road_endpoints);
  const requestedStart = coordinates(request?.origin),
    requestedEnd = coordinates(request?.destination);
  const roadStart = coordinates(road?.origin),
    roadEnd = coordinates(road?.destination);
  const departure = sourceInstant(request?.departure_time),
    epoch = sourceInstant(world.epoch);
  if (
    route?.source !== 'Google Routes' ||
    request?.travel_mode !== 'DRIVE' ||
    !requestedStart ||
    !requestedEnd ||
    !roadStart ||
    !roadEnd ||
    distance(start, requestedStart) > 1 ||
    distance(end, requestedEnd) > 1 ||
    departure === null ||
    epoch === null ||
    Math.floor((departure - epoch) / 60_000) !== resource.departure
  )
    return fallback;
  const path = decodeRoadPolyline(route.encoded_polyline);
  // Quantization is about a metre; a different road-endpoint pair is not this leg.
  if (!path || distance(path[0], roadStart) > 20 || distance(path[path.length - 1], roadEnd) > 20)
    return fallback;
  return { path, geometry: 'sourced_road' };
}

export function groundCamera(path: GeoPoint[]) {
  const lats = path.map((point) => point.lat),
    lngs = path.map((point) => point.lng);
  const cornerA = { lat: Math.min(...lats), lng: Math.min(...lngs) },
    cornerB = { lat: Math.max(...lats), lng: Math.max(...lngs) };
  return {
    center: {
      lat: (cornerA.lat + cornerB.lat) / 2,
      lng: (cornerA.lng + cornerB.lng) / 2,
      altitude: 0,
    },
    range: Math.max(750, Math.min(400000, distance(cornerA, cornerB) * 1.6)),
  };
}

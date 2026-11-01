import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { GeographicLegend } from './GoogleMaps3D';
import { buildOverlays } from './overlays';
import { groundPath } from './groundPaths';
import { transferReview } from '../resourceLabels';
import type { Resource, World } from '../types';

const now = Date.parse('2026-11-01T12:00:30Z');
const cab = {
  id: 'CAB-1',
  source: 'Bucharest.CAB',
  name: 'Bucharest.CAB Sedan',
  kind: 'bus',
  transport_mode: 'private_transfer',
  origin: 'OTP',
  destination: 'CRA',
  departure: 2280,
  arrival: 2480,
  capacity: 2,
  accessible: false,
  cost_per_person: 0,
  room_capacity: 0,
  vehicle_id: null,
  available: true,
  status: 'quoted',
  capacity_scope: 'private_vehicle_quote',
  quote_freshness: {
    basis: 'app_receipt',
    received_at: '2026-11-01T12:00:00Z',
    max_age_seconds: 120,
  },
  provenance: {
    airport_cab_quote: {
      request: { pickup_at: '2026-11-02T14:00:00Z' },
      policy: {
        min_advance_day_hours: 4,
        min_advance_night_hours: 9,
        required_advance_hours: 9,
        advance_policy_basis: 'conservative_maximum_of_source_day_night_rules',
      },
    },
  },
} as Resource;
const apl = {
  ...cab,
  id: 'APL-1',
  name: 'APL Saloon',
  source: 'Airport Pickups London',
  origin: 'MP',
  destination: 'HP',
  provenance: {
    google_route: {
      source: 'Google Routes',
      fetched_at: '2026-11-01T12:00:00Z',
      encoded_polyline: '_p~iF~ps|U_ulLnnqC_mqNvxq`@',
      request: {
        origin: { latitude: 38.5, longitude: -120.2 },
        destination: { latitude: 43.252, longitude: -126.453 },
        departure_time: '2026-11-02T14:00:00Z',
        travel_mode: 'DRIVE',
      },
      road_endpoints: {
        origin: { latitude: 38.5, longitude: -120.2 },
        destination: { latitude: 43.252, longitude: -126.453 },
      },
    },
  },
} as Resource;
function operation(): World {
  return {
    mode: 'live',
    epoch: '2026-11-01T00:00:00Z',
    resources: [structuredClone(cab), structuredClone(apl)],
    airports: [
      {
        id: 'OTP',
        code: 'OTP',
        name: 'Henri Coanda Airport',
        kind: 'airport',
        lat: 44.57,
        lon: 26.08,
        operational_status: 'unknown',
      },
      {
        id: 'CRA',
        code: 'CRA',
        name: 'Craiova Airport',
        kind: 'airport',
        lat: 44.31,
        lon: 23.88,
        operational_status: 'unknown',
      },
      // Published Google polyline example, unrelated to the Romanian transfer.
      {
        id: 'MP',
        code: 'MP',
        name: 'Reported pickup',
        kind: 'meeting_point',
        lat: 38.5,
        lon: -120.2,
      },
      { id: 'HP', code: 'HP', name: 'Hotel property', kind: 'hotel', lat: 43.252, lon: -126.453 },
    ],
    groups: [],
    offers: [],
    reservations: [],
    observations: [],
  } as unknown as World;
}
function geometry(world: World) {
  const routes = buildOverlays(world, null, new Map(), now).routes;
  return {
    sourced: routes.some((route) => route.geometry === 'sourced_road'),
    connectionOnly: routes.some((route) => route.geometry === 'connection_only'),
  };
}

describe('selected ground-transfer map review', () => {
  it('does not describe selected CAB as a dated Google route when an unrelated sourced route exists', () => {
    const world = operation();
    const overview = geometry(world);
    expect(overview).toEqual({ sourced: true, connectionOnly: true });
    const html = renderToStaticMarkup(
      <GeographicLegend
        mode="live"
        selectedReview={transferReview(world, cab, now)}
        selectedGeometry={groundPath(cab, world)?.geometry ?? null}
        roadGeometry={overview}
        hasObservations={false}
      />,
    );
    expect(html).toContain('Bucharest.CAB Sedan');
    expect(html).toContain('Supplier driving estimate without departure-time traffic');
    expect(html).toContain('Endpoints only; no driving path or vehicle position shown');
    expect(html).toContain('Connection only');
    expect(html).not.toMatch(/Google Routes|Dated prediction|predicted road/);
  });
  it('keeps the actual Google route distinction for APL and both keys in network overview', () => {
    const world = operation();
    const overview = geometry(world);
    const html = renderToStaticMarkup(
      <GeographicLegend
        mode="live"
        selectedReview={transferReview(world, apl, now)}
        selectedGeometry={groundPath(apl, world)?.geometry ?? null}
        roadGeometry={overview}
        hasObservations={false}
      />,
    );
    expect(html).toContain('Dated prediction, not a vehicle position');
    expect(html).toContain('Google Routes · predicted road');
    expect(html).not.toContain('Connection only');
    const network = renderToStaticMarkup(
      <GeographicLegend
        mode="live"
        selectedReview={null}
        roadGeometry={overview}
        hasObservations={false}
      />,
    );
    expect(network).toContain('Google Routes · predicted road');
    expect(network).toContain('Connection only');
  });
  it('does not inherit previously selected geometry when the current transfer has no verified path', () => {
    const world = operation();
    const html = renderToStaticMarkup(
      <GeographicLegend
        mode="live"
        selectedReview={transferReview(world, cab, now)}
        selectedGeometry={null}
        roadGeometry={{ sourced: true, connectionOnly: true }}
        hasObservations={false}
      />,
    );
    expect(html).toContain('Road geometry unverified; no driving path shown');
    expect(html).not.toMatch(/Google Routes|Dated prediction|Endpoints only|Connection only/);
  });
  it('places an endpoint-only connection label between airports without moving airport pins or inventing a route', () => {
    const world = operation();
    const map = buildOverlays(world, cab.id, new Map(), now);
    const marker = map.markers.find((item) => item.id === cab.id)!;
    expect(marker).toMatchObject({ observed: false, selected: true });
    expect(marker.label).toContain('CONNECTION ONLY');
    expect(marker.title).toContain('Connection label, not a vehicle position');
    expect(marker.point.lat).toBeCloseTo(44.44);
    expect(marker.point.lng).toBeCloseTo(24.98);
    expect(marker.point).not.toEqual(map.markers.find((item) => item.id === 'OTP')!.point);
    expect(marker.point).not.toEqual(map.markers.find((item) => item.id === 'CRA')!.point);
    expect(map.markers.find((item) => item.id === 'OTP')!.point).toEqual({
      lat: 44.57,
      lng: 26.08,
      altitude: 0,
    });
    expect(map.markers.find((item) => item.id === 'CRA')!.point).toEqual({
      lat: 44.31,
      lng: 23.88,
      altitude: 0,
    });
    expect(map.routes.find((item) => item.id === cab.id)).toMatchObject({
      geometry: 'connection_only',
      observed: false,
      path: [
        { lat: 44.57, lng: 26.08, altitude: 0 },
        { lat: 44.31, lng: 23.88, altitude: 0 },
      ],
    });
    const sourced = buildOverlays(world, apl.id, new Map(), now);
    expect(sourced.markers.find((item) => item.id === apl.id)!.point).toEqual({
      lat: 40.7,
      lng: -120.95,
      altitude: 0,
    });
    expect(sourced.routes[0].geometry).toBe('sourced_road');
    expect(sourced.routes[0].path).toHaveLength(3);
  });
  it('keeps a connection annotation between endpoints across the date line', () => {
    const world = operation();
    world.airports[0].lon = 179;
    world.airports[1].lon = -179;
    const marker = buildOverlays(world, cab.id, new Map(), now).markers.find(
      (item) => item.id === cab.id,
    )!;
    expect(Math.abs(marker.point.lng)).toBe(180);
  });
});

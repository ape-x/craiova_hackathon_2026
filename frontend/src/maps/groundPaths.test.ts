import { describe, expect, it } from 'vitest';
import { decodeRoadPolyline, groundPath } from './groundPaths';
import { buildOverlays, cameraFor } from './overlays';
import type { Resource, World } from '../types';

// Google's published three-point encoded-polyline example; no provider request.
const encoded = '_p~iF~ps|U_ulLnnqC_mqNvxq`@';
const route = {
  source: 'Google Routes',
  encoded_polyline: encoded,
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
};
const resource: Resource = {
  id: 'GROUND-1',
  kind: 'bus',
  transport_mode: 'private_transfer',
  origin: 'MP',
  destination: 'HP',
  departure: 2280,
  name: 'Sourced private transfer',
  arrival: 2600,
  capacity: 2,
  accessible: false,
  cost_per_person: 0,
  room_capacity: 0,
  vehicle_id: null,
  status: 'quoted',
  source: 'APL',
  available: true,
  provenance: { google_route: route },
};
const world = {
  epoch: '2026-11-01T00:00:00Z',
  mode: 'live',
  airports: [
    { id: 'MP', code: 'MP', name: 'Pickup', kind: 'meeting_point', lat: 38.5, lon: -120.2 },
    { id: 'HP', code: 'HP', name: 'Source property', kind: 'hotel', lat: 43.252, lon: -126.453 },
  ],
  resources: [resource],
  offers: [],
  observations: [],
  groups: [],
  reservations: [],
} as unknown as World;

describe('sourced private-transfer map geometry', () => {
  it('decodes the published polyline without substituting an airport schematic', () => {
    expect(decodeRoadPolyline(encoded)).toEqual([
      { lat: 38.5, lng: -120.2, altitude: 0 },
      { lat: 40.7, lng: -120.95, altitude: 0 },
      { lat: 43.252, lng: -126.453, altitude: 0 },
    ]);
    expect(groundPath(resource, world)?.geometry).toBe('sourced_road');
  });
  it('bounds malformed/truncated input and decoded point count', () => {
    for (const value of [null, '', '?', '~~~~~~', '\n?', '?'.repeat(100001), '??'.repeat(4097)])
      expect(decodeRoadPolyline(value)).toBeNull();
  });
  it('falls back to explicit endpoint-only connections for missing or mismatched geometry', () => {
    for (const google_route of [
      { ...route, encoded_polyline: null },
      { ...route, encoded_polyline: 'bad' },
      { ...route, source: 'Unknown' },
      { ...route, request: { ...route.request, departure_time: '2026-11-02T15:00:00Z' } },
      { ...route, request: { ...route.request, destination: { latitude: 0, longitude: 0 } } },
      {
        ...route,
        road_endpoints: { ...route.road_endpoints, destination: { latitude: 0, longitude: 0 } },
      },
    ]) {
      const connection = groundPath({ ...resource, provenance: { google_route } }, world);
      expect(connection?.geometry).toBe('connection_only');
      expect(connection?.path).toHaveLength(2);
    }
  });
  it('renders live imported paths with source labels, not simulated vehicle positions', () => {
    const overlay = buildOverlays(world, resource.id, new Map());
    expect(overlay.routes[0]).toMatchObject({
      id: 'GROUND-1',
      geometry: 'sourced_road',
      observed: false,
      selected: true,
    });
    expect(overlay.routes[0].path).toEqual(decodeRoadPolyline(encoded));
    expect(overlay.markers.find((marker) => marker.id === 'GROUND-1')?.label).toContain(
      'ROAD PREDICTION',
    );
    expect(overlay.markers.find((marker) => marker.id === 'GROUND-1')?.title).toContain(
      'not vehicle telemetry',
    );
    expect(overlay.markers.some((marker) => marker.label.includes('· SIM'))).toBe(false);
    const camera = cameraFor(world, 'airport', resource.id);
    expect(camera.center.lat).toBeCloseTo(40.876, 6);
    expect(camera.center.lng).toBeCloseTo(-123.3265, 6);
    expect(camera.range).toBeGreaterThan(10000);
  });
  it('labels absent geometry honestly without drawing an invented road curve', () => {
    const operation = { ...world, resources: [{ ...resource, provenance: {} }] };
    const overlay = buildOverlays(operation, resource.id, new Map());
    expect(overlay.routes[0].geometry).toBe('connection_only');
    expect(overlay.routes[0].path).toHaveLength(2);
    expect(overlay.markers.find((marker) => marker.id === resource.id)?.title).toContain(
      'not a driving path',
    );
  });
});

import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import Inspector from './Inspector';
import { geographicContext, locationName, resourceTitle, transferReview } from './resourceLabels';
import { buildOverlays, cameraFor } from './maps/overlays';
import { ALL_MODELS, buildModelDescriptors } from './maps/models';
import type { Airport, Resource, World } from './types';

// Entirely synthetic coordinates and records; no provider request or production fixture.
const now = Date.parse('2026-10-31T20:00:00Z');
const resourceId = 'APL-0123456789abcdef0123456789abcdef';
const pointId = 'MP-0123456789abcdef0123456789abcdef';
const hotelId = 'HP-0123456789abcdef0123456789abcdef';
const origin = { latitude: 51.47, longitude: -0.48 };
const destination = { latitude: 51.48, longitude: -0.49 };
const resource: Resource = {
  id: resourceId,
  name: 'APL Saloon',
  kind: 'bus',
  transport_mode: 'private_transfer',
  origin: pointId,
  destination: hotelId,
  departure: 2280,
  arrival: 2320,
  capacity: 2,
  accessible: false,
  available: true,
  cost_per_person: 0,
  room_capacity: 0,
  vehicle_id: null,
  status: 'quoted',
  source: 'Airport Pickups London',
  booking_status: 'unbooked',
  capacity_scope: 'private_vehicle_quote',
  quote_freshness: {
    basis: 'app_receipt',
    received_at: '2026-10-31T20:00:00Z',
    max_age_seconds: 120,
  },
  provenance: {
    google_route: {
      source: 'Google Routes',
      fetched_at: '2026-10-31T20:00:04Z',
      request: {
        origin,
        destination,
        departure_time: '2026-11-02T14:00:00Z',
        travel_mode: 'DRIVE',
      },
      road_endpoints: { origin, destination },
      encoded_polyline: 'ovcyH~v|Ag^f^g^f^',
    },
  },
};
function node(id: string, name: string, kind: Airport['kind'], lat: number, lon: number): Airport {
  return { id, code: id, name, kind, lat, lon, available: true, diversion_eligible: false };
}
function operation(): World {
  return {
    scenario_id: 'map-selection-fixture',
    version: 1,
    epoch: '2026-11-01T00:00:00Z',
    sim_time: 100,
    mode: 'live',
    currency: 'EUR',
    budget: 1000,
    airports: [
      node('CRA', 'Craiova reference', 'airport', 44.3181, 23.8886),
      node('LHR', 'Heathrow reference', 'airport', 51.47, -0.45),
      node(
        pointId,
        'Reported Terminal 5 pickup',
        'meeting_point',
        origin.latitude,
        origin.longitude,
      ),
      node(hotelId, 'Source Hotel London', 'hotel', destination.latitude, destination.longitude),
    ],
    resources: [
      structuredClone(resource),
      {
        ...resource,
        id: 'ASSUMED-COACH',
        source: 'synthetic',
        transport_mode: null,
        origin: 'CRA',
        destination: 'LHR',
      },
      {
        ...resource,
        id: 'FLIGHT-QUOTE',
        kind: 'flight',
        transport_mode: null,
        source: 'Nuitee',
        origin: 'LHR',
        destination: 'CRA',
      },
    ],
    groups: [
      {
        id: 'ASSUMED-GROUP',
        name: 'Reported party',
        size: 2,
        members: ['Adult 1', 'Adult 2'],
        origin: 'LHR',
        destination: hotelId,
        deadline: 3000,
        accessible: false,
        budget: 1000,
        original_flight_id: 'FLIGHT',
        status: 'waiting',
        token: 'test-only',
        priority: 0,
        source: 'planning_assumption',
      },
    ],
    offers: [],
    reservations: [],
    events: [],
    decisions: [],
    observations: [],
    stats: {},
    provider_status: {},
  };
}
afterEach(() => vi.useRealTimers());

describe('imported transfer selection in the live geographic workspace', () => {
  it('focuses the sourced road in Airport and Network before any complete itinerary exists', () => {
    const world = operation();
    const context = geographicContext(world, resourceId);
    expect(context.world).toBe(world);
    expect(context.selectionId).toBe(resourceId);
    expect(context.home?.id).toBe(pointId);
    expect(context.locationLabel).toBe('APL Saloon · private transfer');
    expect(context.siteLabel).toBe('TRANSFER');
    for (const mode of ['airport', 'region'] as const) {
      const camera = cameraFor(context.world, mode, context.selectionId);
      expect(camera.center.lat).toBeCloseTo(51.475);
      expect(camera.center.lng).toBeCloseTo(-0.485);
      expect(camera.range).toBeGreaterThan(1000);
    }
    const map = buildOverlays(context.world, context.selectionId, new Map(), now);
    expect(map.routes).toHaveLength(1);
    expect(map.routes[0]).toMatchObject({
      geometry: 'sourced_road',
      selected: true,
      observed: false,
    });
    expect(map.routes[0].path).toHaveLength(3);
    expect(map.markers.find((marker) => marker.id === pointId)).toMatchObject({
      label: 'Reported Terminal 5 pickup · REPORTED MEETING POINT',
      selected: true,
    });
    expect(map.markers.find((marker) => marker.id === hotelId)).toMatchObject({
      label: 'Source Hotel London · HOTEL',
      selected: true,
    });
  });

  it('does not create vehicles, families, or terminal models from the full live source world', () => {
    const world = operation();
    const context = geographicContext(world, resourceId);
    expect(buildModelDescriptors(context.world, context.selectionId, ALL_MODELS)).toEqual([]);
    const map = buildOverlays(context.world, context.selectionId, new Map(), now);
    expect(
      map.markers.some((marker) =>
        ['ASSUMED-GROUP', 'ASSUMED-COACH', 'FLIGHT-QUOTE'].includes(marker.id),
      ),
    ).toBe(false);
    world.observations = [
      {
        id: 'observed-aircraft',
        lat: 51.5,
        lon: -0.4,
        altitude_ft: 10000,
        heading: 85,
        mode: 'LIVE',
        observed_at: '2026-10-31T20:00:00Z',
      },
    ];
    const models = buildModelDescriptors(context.world, resourceId, ALL_MODELS);
    expect(models).toHaveLength(1);
    expect(models[0]).toMatchObject({
      observed: true,
      kind: 'aircraft',
      options: { position: { lat: 51.5, lng: -0.4, altitude: 3048 } },
    });
  });

  it('retains coherent dated road geometry after the separate quote window expires', () => {
    const world = operation();
    const current = buildOverlays(world, resourceId, new Map(), now + 30_000);
    const stale = buildOverlays(world, resourceId, new Map(), now + 300_000);
    expect(stale.routes[0].path).toEqual(current.routes[0].path);
    const label = stale.markers.find((marker) => marker.id === resourceId)!;
    expect(label.label).toContain('APL Saloon · Unbooked · Quote stale');
    expect(label.title).toContain('20:00:04 UTC');
    expect(label.title).toContain('Dated Google Routes driving prediction, not vehicle telemetry');
    expect(transferReview(world, resource, now + 300_000).sourceTime).toContain('20:00:04 UTC');
    world.resources[0].available = false;
    expect(transferReview(world, world.resources[0], now + 300_000).status).toContain(
      'Unavailable',
    );
    expect(buildModelDescriptors(world, resourceId)).toEqual([]);
  });

  it('uses human primary labels, keeps app identifiers secondary, and dates road versus quote evidence independently', () => {
    vi.useFakeTimers();
    vi.setSystemTime(now + 300_000);
    const world = operation();
    const html = renderToStaticMarkup(
      <Inspector
        world={world}
        selectedId={resourceId}
        onSelect={() => {}}
        onClose={() => {}}
        onReplan={() => {}}
        onScenario={() => {}}
        busy={false}
        showLog={false}
        config={{}}
      />,
    );
    expect(html).toMatch(/<h3>APL Saloon/);
    expect(html).toContain('<strong>Reported Terminal 5 pickup</strong>');
    expect(html).toContain('<strong>Source Hotel London</strong>');
    expect(html).not.toContain(`<strong>${pointId}</strong>`);
    expect(html).toContain(`<dt>Transfer record</dt><dd>${resourceId}</dd>`);
    expect(html).toContain('Supplier reference not provided');
    expect(html).toContain('Road prediction received');
    expect(html).toContain('20:00:04 UTC');
    expect(html).toContain('20:00:00 UTC');
    expect(html).toContain('Requested pickup');
    expect(html).toContain('Predicted road arrival');
    expect(html).toContain('No vehicle position is observed');
    expect(resourceTitle({ ...resource, name: resourceId })).toBe('APL private transfer');
    expect(resourceTitle({ ...resource, name: '  APL\n Saloon  ' })).toBe('APL Saloon');
    expect(locationName(world, 'LHR')).toBe('LHR');
  });

  it('still maps a selected hotel resource to its property while preserving simulation selection', () => {
    const world = operation();
    world.resources.push({
      ...resource,
      id: 'HOTEL-QUOTE',
      kind: 'hotel',
      transport_mode: null,
      origin: hotelId,
      destination: hotelId,
    });
    expect(geographicContext(world, 'HOTEL-QUOTE')).toMatchObject({
      selectionId: hotelId,
      siteLabel: 'HOTEL',
      locationLabel: 'Source Hotel London · accommodation',
    });
    world.mode = 'simulation';
    expect(geographicContext(world, 'HOTEL-QUOTE').selectionId).toBe('HOTEL-QUOTE');
  });
});

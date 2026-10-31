import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import Inspector, { ItineraryView } from './Inspector';
import { geographicContext, locationName, resourceTitle, transferReview } from './resourceLabels';
import { buildOverlays, cameraFor } from './maps/overlays';
import { ALL_MODELS, buildModelDescriptors } from './maps/models';
import { GeographicLegend } from './maps/GoogleMaps3D';
import { groundPath } from './maps/groundPaths';
import { AirportArrivalDetails, SupplierMeetingInstructions } from './TransferEvidence';
import type { Airport, AirportArrivalAssumption, Offer, Resource, World } from './types';

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

function returnOperation(): World {
  const world = operation();
  const arrival: AirportArrivalAssumption = {
    source: 'operator_declared',
    confirmed: true,
    group_id: 'ASSUMED-GROUP',
    group_fingerprint: 'a'.repeat(64),
    scenario_id: world.scenario_id,
    epoch: world.epoch,
    airport_id: 'LHR',
    onward_resource_id: 'FLIGHT-QUOTE',
    onward_flight_fingerprint: 'b'.repeat(64),
    onward_departure_at: '2026-11-02T18:00:00Z',
    dropoff: { label: 'Reported forecourt', coordinates: origin },
    processing_minutes: 120,
    departure_terminal: null,
  };
  world.resources[0] = {
    ...world.resources[0],
    origin: hotelId,
    destination: 'LHR',
    airport_arrival_assumption: arrival,
    provenance: {
      google_route: {
        source: 'Google Routes',
        fetched_at: '2026-10-31T20:00:04Z',
        request: {
          origin: destination,
          destination: origin,
          departure_time: '2026-11-02T14:00:00Z',
          travel_mode: 'DRIVE',
        },
        road_endpoints: { origin: destination, destination: origin },
        encoded_polyline: '_ueyHnu~Af^g^f^g^',
      },
      apl_quote: {
        meeting_point: {
          name: 'Hotel reception',
          instructions: 'Wait at the desk.\nAsk for the driver’s name.',
        },
      },
    },
  };
  world.resources[2] = { ...world.resources[2], departure: 2520, arrival: 2700 };
  return world;
}

describe('imported transfer selection in the live geographic workspace', () => {
  it('plots a return at its reported drop-off rather than moving it to the airport reference', () => {
    const world = returnOperation();
    const resource = world.resources[0];
    const road = groundPath(resource, world)!;
    expect(road.geometry).toBe('sourced_road');
    expect(road.path.at(-1)).toMatchObject({ lat: origin.latitude, lng: origin.longitude });
    const overlay = buildOverlays(world, resource.id, new Map(), now);
    const dropoff = overlay.markers.find((marker) => marker.id === `${resource.id}-dropoff`)!;
    expect(dropoff).toMatchObject({
      selectionId: resource.id,
      label: 'Reported forecourt · REPORTED DROP-OFF',
      point: { lat: origin.latitude, lng: origin.longitude },
      selected: true,
    });
    expect(dropoff.title).toContain('Departure terminal unknown');
    const airport = overlay.markers.find((marker) => marker.id === 'LHR')!;
    expect(airport.point.lng).toBe(-0.45);
    expect(airport.selected).toBe(false);
    const selectedFromMarker = dropoff.selectionId ?? dropoff.id;
    expect(geographicContext(world, selectedFromMarker).selectionId).toBe(resource.id);
    expect(cameraFor(world, 'region', selectedFromMarker).center.lng).toBeCloseTo(-0.485);
    expect(buildModelDescriptors(world, resource.id)).toEqual([]);
  });

  it('rejects mismatched return geometry without substituting a fabricated road to the airport centroid', () => {
    const world = returnOperation();
    const resource = world.resources[0];
    const route = resource.provenance!.google_route as { request: { destination: typeof origin } };
    route.request.destination = { latitude: 51.47, longitude: -0.45 };
    const road = groundPath(resource, world)!;
    expect(road.geometry).toBe('connection_only');
    expect(road.path).toHaveLength(2);
    expect(road.path[1].lng).toBe(origin.longitude);
    resource.airport_arrival_assumption = {
      ...resource.airport_arrival_assumption!,
      departure_terminal: '5',
    } as unknown as AirportArrivalAssumption;
    expect(groundPath(resource, world)).toBeNull();
    expect(
      renderToStaticMarkup(
        <AirportArrivalDetails assumption={resource.airport_arrival_assumption} />,
      ),
    ).toContain('Airport arrival evidence is incomplete');
  });

  it('preserves operator buffer and unknown terminal in passenger legs, independently of supplier pickup instructions', () => {
    const world = returnOperation();
    const resource = world.resources[0];
    const offer = {
      id: 'return-offer',
      group_id: 'ASSUMED-GROUP',
      status: 'proposed',
      itinerary: {
        arrival: resource.arrival,
        total_cost: 0,
        legs: [
          {
            resource_id: resource.id,
            kind: 'bus',
            origin: resource.origin,
            destination: resource.destination,
            departure: resource.departure,
            arrival: resource.arrival,
            cost: 0,
            transport_mode: 'private_transfer',
            airport_arrival_assumption: resource.airport_arrival_assumption,
          },
        ],
      },
    } as Offer;
    const html = renderToStaticMarkup(<ItineraryView offer={offer} world={world} />);
    expect(html).toContain('Source Hotel London → LHR');
    expect(html).toContain('Reported drop-off: Reported forecourt');
    expect(html).toContain('Operator processing allowance: 120 minutes');
    expect(html).toContain('Departure terminal unknown');
    expect(html).toContain('not airline guidance or guaranteed boarding');
    expect(html).toContain('APL meeting guidance');
    expect(html).toContain('does not verify hotel pickup instructions');
    expect(html).toContain('Hotel reception');
    expect(html).toContain('14:00:00 UTC');
    expect(html).not.toContain('Initial meeting point:');
    const safe = renderToStaticMarkup(
      <SupplierMeetingInstructions
        value={{ name: 'Reception', instructions: '<script>alert(1)</script>' }}
      />,
    );
    expect(safe).not.toContain('<script>');
    expect(safe).toContain('&lt;script&gt;');
    expect(safe).toContain(
      'Its applicability to the requested pickup or flight departure terminal has not been verified',
    );
    expect(
      renderToStaticMarkup(
        <SupplierMeetingInstructions value={{ name: null, instructions: 42 }} />,
      ),
    ).toBe('');
  });
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
    expect(label.label).toBe('APL Saloon · ROAD PREDICTION');
    expect(label.title).toContain('Unbooked · Quote stale');
    expect(label.title).toContain('20:00:04 UTC');
    expect(label.title).toContain('Dated Google Routes driving prediction, not vehicle telemetry');
    expect(transferReview(world, resource, now + 300_000).sourceTime).toContain('20:00:04 UTC');
    world.resources[0].available = false;
    expect(transferReview(world, world.resources[0], now + 300_000).status).toContain(
      'Unavailable',
    );
    expect(buildModelDescriptors(world, resourceId)).toEqual([]);
  });

  it('focuses property labels on the selected road while preserving all properties in the overview', () => {
    const world = operation();
    world.airports.push(
      node('OTHER-HP', 'Unrelated hotel', 'hotel', 51.473, -0.481),
      node('OTHER-MP', 'Other meeting point', 'meeting_point', 51.472, -0.482),
    );
    world.observations = [
      {
        id: 'observed-plane',
        lat: 51.5,
        lon: -0.4,
        mode: 'LIVE',
        observed_at: '2026-10-31T20:00:00Z',
      },
    ];
    const focused = buildOverlays(world, resourceId, new Map(), now);
    expect(focused.markers.map((marker) => marker.id)).toEqual(
      expect.arrayContaining([pointId, hotelId, 'LHR', 'observed-plane', resourceId]),
    );
    expect(focused.markers.some((marker) => ['OTHER-HP', 'OTHER-MP'].includes(marker.id))).toBe(
      false,
    );
    const overview = buildOverlays(world, null, new Map(), now);
    expect(overview.markers.map((marker) => marker.id)).toEqual(
      expect.arrayContaining([pointId, hotelId, 'OTHER-HP', 'OTHER-MP']),
    );
    expect(world.airports).toHaveLength(6);
    world.resources[0].provenance = {};
    expect(
      buildOverlays(world, resourceId, new Map(), now).markers.some(
        (marker) => marker.id === 'OTHER-HP',
      ),
    ).toBe(true);
  });

  it('keeps source time and stale/unbooked evidence in a compact legend without irrelevant aircraft prose', () => {
    const selectedReview = transferReview(operation(), resource, now + 300_000);
    const props = {
      mode: 'live' as const,
      selectedReview,
      roadGeometry: { sourced: true, connectionOnly: false },
      hasObservations: false,
    };
    const html = renderToStaticMarkup(<GeographicLegend {...props} />);
    expect(html).toContain('Unbooked · Quote stale');
    expect(html).toContain('20:00:04 UTC');
    expect(html).toContain('Dated prediction, not a vehicle position');
    expect(html).toContain('Google Routes');
    expect(html).not.toContain('Received observations');
    expect(html).not.toContain('Observed aircraft at provider positions');
    const withAircraft = renderToStaticMarkup(<GeographicLegend {...props} hasObservations />);
    expect(withAircraft).toContain('Received observations');
    expect(withAircraft).toContain('enlarged 2.5');
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

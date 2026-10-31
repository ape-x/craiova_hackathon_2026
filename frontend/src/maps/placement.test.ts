import { describe, expect, it } from 'vitest';
import type { Leg, Offer, PassengerGroup, Resource, World } from '../types';
import type { GeoPoint } from './contracts';
import { ALL_MODELS, buildModelDescriptors, MODEL_URLS } from './models';
import { cameraFor } from './overlays';
import { parkedPoint, passengerPoint, resourcePoint, resourceProgress } from './placement';

function resource(
  id: string,
  kind: Resource['kind'],
  origin: string,
  destination: string,
  departure: number,
  arrival: number,
): Resource {
  return {
    id,
    kind,
    name: id,
    origin,
    destination,
    departure,
    arrival,
    capacity: 20,
    accessible: true,
    available: true,
    cost_per_person: 10,
    room_capacity: 2,
    vehicle_id: kind === 'hotel' ? null : id,
    status: 'scheduled',
    source: 'synthetic',
  };
}
function group(
  id: string,
  size: number,
  origin: string,
  destination: string,
  status: PassengerGroup['status'],
): PassengerGroup {
  return {
    id,
    size,
    origin,
    destination,
    status,
    name: `Synthetic family ${id}`,
    members: Array.from({ length: size }, (_, index) => `Synthetic member ${index + 1}`),
    deadline: 1000,
    ready_at: 500,
    accessible: false,
    budget: 1000,
    original_flight_id: 'F101',
    token: `synthetic-passenger-token-${id}`,
    priority: 1,
  };
}

/** Complete, typed scenario data: no provider requests or partially cast worlds. */
function scenario(): World {
  const resources = [
    resource('B12', 'bus', 'CRA', 'OTP', 600, 720),
    resource('B13', 'bus', 'CRA', 'OTP', 600, 720),
    resource('B14', 'bus', 'CRA', 'OTP', 600, 720),
    resource('F101', 'flight', 'CRA', 'OTP', 600, 720),
    resource('F102', 'flight', 'CRA', 'OTP', 620, 740),
    resource('F201', 'flight', 'OTP', 'CRA', 300, 420),
    resource('F301', 'flight', 'OTP', 'SBZ', 780, 840),
    resource('H-OTP', 'hotel', 'OTP', 'OTP', 720, 780),
  ];
  const groups = [
    group('G001', 4, 'CRA', 'SBZ', 'confirmed'),
    group('G002', 2, 'CRA', 'OTP', 'offered'),
    group('G003', 7, 'OTP', 'CRA', 'waiting'),
  ];
  function offer(id: string, groupId: string, ids: string[], status: Offer['status']): Offer {
    const passengers = groups.find((item) => item.id === groupId)!;
    const legs: Leg[] = ids.map((resourceId) => {
      const item = resources.find((candidate) => candidate.id === resourceId)!;
      return {
        resource_id: item.id,
        kind: item.kind,
        origin: item.origin,
        destination: item.destination,
        departure: item.departure,
        arrival: item.arrival,
        cost: item.cost_per_person * passengers.size,
      };
    });
    return {
      id,
      group_id: groupId,
      status,
      itinerary: {
        legs,
        arrival: legs.at(-1)!.arrival,
        total_cost: legs.reduce((sum, leg) => sum + leg.cost, 0),
      },
      expires_at: '2030-01-01T00:00:00Z',
      created_version: 1,
      confirmed_at: status === 'confirmed' ? '2026-10-31T08:00:00Z' : null,
      reason: null,
    };
  }
  const offers = [
    offer('O001', 'G001', ['B12', 'F301'], 'confirmed'),
    offer('O002', 'G002', ['F101'], 'held'),
  ];
  return {
    scenario_id: 'placement-regression',
    version: 1,
    epoch: '2026-10-31T00:00:00Z',
    sim_time: 500,
    mode: 'simulation',
    currency: 'EUR',
    budget: 5000,
    airports: [
      {
        id: 'CRA',
        code: 'CRA',
        name: 'Craiova',
        lat: 44.3181,
        lon: 23.8886,
        available: true,
        diversion_eligible: true,
      },
      {
        id: 'OTP',
        code: 'OTP',
        name: 'Otopeni',
        lat: 44.5711,
        lon: 26.085,
        available: true,
        diversion_eligible: true,
      },
      {
        id: 'SBZ',
        code: 'SBZ',
        name: 'Sibiu',
        lat: 45.7856,
        lon: 24.0913,
        available: true,
        diversion_eligible: true,
      },
    ],
    resources,
    groups,
    offers,
    reservations: offers.flatMap((item) =>
      item.itinerary.legs.map((leg) => ({
        id: `${item.id}-${leg.resource_id}`,
        offer_id: item.id,
        group_id: item.group_id,
        resource_id: leg.resource_id,
        quantity: groups.find((g) => g.id === item.group_id)!.size,
        status: item.status === 'confirmed' ? 'confirmed' : 'held',
        expires_at: item.expires_at,
      })),
    ),
    observations: [
      {
        id: 'OBS-1',
        callsign: 'OBSERVED-TEST',
        lat: 44.4,
        lon: 24,
        altitude_ft: 10000,
        heading: 85,
        source: 'Flightradar24',
        mode: 'LIVE',
        observed_at: '2026-10-31T08:20:00Z',
      },
    ],
    events: [],
    decisions: [],
    stats: {},
    provider_status: {},
  };
}

function metersBetween(a: GeoPoint, b: GeoPoint): number {
  const north = (a.lat - b.lat) * 111320;
  const east = (a.lng - b.lng) * 111320 * Math.cos((((a.lat + b.lat) / 2) * Math.PI) / 180);
  return Math.hypot(north, east);
}

describe('geographic simulation placement', () => {
  it('shows the fleet, origin and arrival terminals, and waiting families with nothing selected', () => {
    const world = scenario();
    const models = buildModelDescriptors(world, null);
    expect(
      models
        .filter((model) => !model.observed && ['aircraft', 'bus'].includes(model.kind!))
        .map((model) => model.id)
        .sort(),
    ).toEqual(
      world.resources
        .filter((item) => item.kind !== 'hotel')
        .map((item) => item.id)
        .sort(),
    );
    expect(
      models
        .filter((model) => model.kind === 'terminal')
        .map((model) => model.id)
        .sort(),
    ).toEqual(['CRA', 'OTP', 'SBZ']);
    expect(
      models
        .filter((model) => model.kind === 'passengers')
        .map((model) => model.id)
        .sort(),
    ).toEqual(['G001', 'G002', 'G003']);
    expect(models.every((model) => model.options.orientation.tilt === 270)).toBe(true);
    expect(
      models.filter((model) => !model.observed).every((model) => model.title.includes('SIMULATED')),
    ).toBe(true);
    expect(models.find((model) => model.id === 'G003')).toMatchObject({
      options: { src: MODEL_URLS.passenger5 },
    });
    expect(models.find((model) => model.id === 'G003')!.title).toContain('7 SIMULATED passengers');
  });

  it('grounds scheduled flights at their origin and completed flights at their destination', () => {
    const world = scenario();
    const models = buildModelDescriptors(world, null);
    for (const id of ['F101', 'F201']) {
      const item = world.resources.find((candidate) => candidate.id === id)!;
      const airportId = world.sim_time < item.departure ? item.origin : item.destination;
      const airport = world.airports.find((candidate) => candidate.id === airportId)!;
      const model = models.find((candidate) => candidate.id === id)!;
      expect(model.options.position).toEqual(parkedPoint(item, world, airport));
      expect(model.options.position.altitude).toBe(0);
      expect(model.options.altitudeMode).toBe('CLAMP_TO_GROUND');
    }
  });

  it('allocates separate parked stands and bus bays instead of stacking vehicles', () => {
    const world = scenario();
    const home = world.airports[0];
    for (const kind of ['flight', 'bus'] as const) {
      const points = world.resources
        .filter((item) => item.kind === kind && item.origin === 'CRA')
        .map((item) => parkedPoint(item, world, home));
      expect(points.length).toBeGreaterThan(1);
      for (let i = 0; i < points.length; i++)
        for (let j = i + 1; j < points.length; j++)
          expect(metersBetween(points[i], points[j])).toBeGreaterThan(20);
    }
  });

  it.each(['B12', 'F101'])(
    'moves %s continuously through the route midpoint and touches ground at its endpoints',
    (id) => {
      const world = scenario();
      const item = world.resources.find((candidate) => candidate.id === id)!;
      world.sim_time = item.departure;
      const start = resourcePoint(item, world)!;
      expect(resourceProgress(item, world)).toBe(0);
      world.sim_time = item.arrival;
      const end = resourcePoint(item, world)!;
      expect(resourceProgress(item, world)).toBe(1);
      world.sim_time = (item.departure + item.arrival) / 2;
      const middle = resourcePoint(item, world)!;
      expect(resourceProgress(item, world)).toBe(0.5);
      expect(middle.lat).toBeCloseTo((start.lat + end.lat) / 2, 10);
      expect(middle.lng).toBeCloseTo((start.lng + end.lng) / 2, 10);
      expect(start.altitude).toBe(0);
      expect(end.altitude).toBe(0);
      expect(middle.altitude).toBe(item.kind === 'flight' ? 9000 : 0);
      if (item.kind === 'flight')
        expect(
          buildModelDescriptors(world, null).find((m) => m.id === id)!.options.altitudeMode,
        ).toBe('RELATIVE_TO_GROUND');
      world.sim_time = item.departure + 1;
      const firstMinute = resourcePoint(item, world)!;
      expect(metersBetween(start, firstMinute)).toBeGreaterThan(0);
      expect(metersBetween(start, firstMinute)).toBeLessThan(metersBetween(start, end) / 50);
      expect(firstMinute.altitude!).toBeLessThan(500);
    },
  );

  it('follows a confirmed family through two vehicles and shows figures only while waiting or after arrival', () => {
    const world = scenario();
    const family = world.groups[0];
    const bus = world.resources.find((item) => item.id === 'B12')!;
    const flight = world.resources.find((item) => item.id === 'F301')!;
    const visible = () =>
      buildModelDescriptors(world, family.id).some((model) => model.id === family.id);
    world.sim_time = 599;
    expect(visible()).toBe(true);
    expect(passengerPoint(family, world)).not.toEqual(resourcePoint(bus, world));
    for (const time of [600, 660, 719]) {
      world.sim_time = time;
      expect(passengerPoint(family, world)).toEqual(resourcePoint(bus, world));
      expect(visible()).toBe(false);
    }
    for (const time of [720, 750, 779]) {
      world.sim_time = time;
      expect(passengerPoint(family, world)).toEqual(resourcePoint(bus, world));
      expect(visible()).toBe(true);
    }
    for (const time of [780, 810, 839]) {
      world.sim_time = time;
      expect(passengerPoint(family, world)).toEqual(resourcePoint(flight, world));
      expect(visible()).toBe(false);
    }
    world.sim_time = 840;
    expect(passengerPoint(family, world)).toEqual(resourcePoint(flight, world));
    expect(visible()).toBe(true);
  });

  it('does not board a family with only a held offer', () => {
    const world = scenario();
    const family = world.groups[1];
    const waiting = passengerPoint(family, world);
    world.sim_time = 660;
    expect(passengerPoint(family, world)).toEqual(waiting);
    expect(
      buildModelDescriptors(world, family.id).find((model) => model.id === family.id),
    ).toBeDefined();
  });

  it('keeps ground cameras terrain-relative and observed altitude explicitly absolute', () => {
    const world = scenario();
    for (const id of [null, 'CRA', 'B12', 'G001'])
      expect(cameraFor(world, 'airport', id).altitudeMode).toBe('RELATIVE_TO_GROUND');
    expect(cameraFor(world, 'passenger', 'G001').altitudeMode).toBe('RELATIVE_TO_GROUND');
    expect(cameraFor(world, 'airport', 'OBS-1')).toMatchObject({
      center: { lat: 44.4, lng: 24, altitude: 3048 },
      altitudeMode: 'ABSOLUTE',
    });
  });

  it('applies model toggles independently and never overrides hidden layers for selection', () => {
    const world = scenario();
    const all = buildModelDescriptors(world, null);
    const noVehicles = buildModelDescriptors(world, 'B12', { ...ALL_MODELS, vehicles: false });
    expect(noVehicles).toEqual(
      all.filter((model) => model.kind === 'terminal' || model.kind === 'passengers'),
    );
    expect(buildModelDescriptors(world, 'CRA', { ...ALL_MODELS, terminal: false })).toEqual(
      all.filter((model) => model.kind !== 'terminal'),
    );
    expect(buildModelDescriptors(world, 'G001', { ...ALL_MODELS, passengers: false })).toEqual(
      all.filter((model) => model.kind !== 'passengers'),
    );
    expect(
      buildModelDescriptors(world, 'G001', { vehicles: false, terminal: false, passengers: false }),
    ).toEqual([]);
  });
});

import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import {
  AirportCabFareDetails,
  airportCabFare,
  airportCabFinalArrival,
  nativeAirportCabLeg,
} from './airportCabPresentation';
import { costPresentation, EstimatedCostNote } from './priceEvidence';
import Inspector, { ItineraryView } from './Inspector';
import PassengerHistory from './PassengerHistory';
import { SourceRecordDetails } from './HistoricalSource';
import { resourceTitle, transferReview } from './resourceLabels';
import { liveResourceStatus, resourceFreshness } from './resourceFreshness';
import { groundPath } from './maps/groundPaths';
import { buildOverlays } from './maps/overlays';
import { GeographicLegend } from './maps/GoogleMaps3D';
import type { Itinerary, Offer, Resource, World } from './types';

const now = Date.parse('2026-11-01T12:00:30Z');
const resource: Resource = {
  id: 'CAB-1',
  name: 'Bucharest.CAB Standard',
  source: 'Bucharest.CAB',
  kind: 'bus',
  transport_mode: 'private_transfer',
  origin: 'OTP',
  destination: 'CRA',
  departure: 2280,
  arrival: 2480,
  capacity: 2,
  capacity_scope: 'private_vehicle_quote',
  eligible_group_ids: ['G1'],
  available: true,
  accessible: false,
  status: 'quoted',
  requires_revalidation: true,
  cost_per_person: 0,
  quote_total_cost: 191.25,
  budget_total_cost: null,
  price_evidence: null,
  room_capacity: 0,
  vehicle_id: null,
  quote_freshness: {
    basis: 'app_receipt',
    received_at: '2026-11-01T12:00:00Z',
    max_age_seconds: 120,
  },
  provenance: {
    airport_cab_option_id: 'CAR-1',
    direction: 'initial_airport_to_airport',
    estimated_arrival: true,
    supplier_booked: false,
    airport_cab_quote: {
      provider: 'Bucharest.CAB',
      source_url: 'https://www.bucharest.cab/en/book',
      booking_supported: false,
      received_at: '2026-11-01T12:00:00Z',
      price_basis: 'supplier_client_formula_cash_no_extras',
      request: {
        origin_airport: 'OTP',
        destination_airport: 'CRA',
        passengers: 2,
        pickup_at: '2026-11-02T14:00:00Z',
      },
      policy: {
        min_advance_day_hours: 4,
        min_advance_night_hours: 9,
        required_advance_hours: 9,
        advance_policy_basis: 'conservative_maximum_of_source_day_night_rules',
      },
      route: { duration_seconds: 12000, duration_basis: 'source_estimate_without_departure_time' },
      options: [
        {
          id: 'CAR-1',
          price: { amount: '191.25', currency: 'EUR' },
          price_scope: 'per_vehicle',
          fits_request: true,
        },
      ],
    },
  },
};
const itinerary: Itinerary = {
  legs: [
    {
      resource_id: 'CAB-1',
      kind: 'bus',
      transport_mode: 'private_transfer',
      origin: 'OTP',
      destination: 'CRA',
      departure: 2280,
      arrival: 2480,
      cost: 191.25,
    },
  ],
  arrival: 2480,
  total_cost: 191.25,
};
const offer: Offer = {
  id: 'O1',
  group_id: 'G1',
  itinerary,
  status: 'proposed',
  expires_at: '2026-11-01T12:02:00Z',
  created_version: 8,
  confirmed_at: null,
  reason: null,
};
function operation(): World {
  return {
    scenario_id: 'cab-presentation',
    version: 8,
    epoch: '2026-11-01T00:00:00Z',
    sim_time: 720,
    mode: 'live',
    currency: 'EUR',
    budget: 1000,
    // Google's published encoded-polyline endpoints, used only to test a stray geometry attachment.
    airports: [
      {
        id: 'OTP',
        code: 'OTP',
        name: 'Origin airport',
        kind: 'airport',
        lat: 38.5,
        lon: -120.2,
        operational_status: 'unknown',
        available: false,
        diversion_eligible: false,
      },
      {
        id: 'CRA',
        code: 'CRA',
        name: 'Destination airport',
        kind: 'airport',
        lat: 43.252,
        lon: -126.453,
        operational_status: 'unknown',
        available: false,
        diversion_eligible: false,
      },
    ],
    resources: [structuredClone(resource)],
    groups: [],
    offers: [structuredClone(offer)],
    reservations: [],
    events: [],
    decisions: [],
    observations: [],
    stats: {},
    provider_status: {},
  } as World;
}
afterEach(() => vi.useRealTimers());

describe('native EUR CAB fare in saved resources and itineraries', () => {
  it('shows exact native EUR cents rather than a missing ECB estimate', () => {
    expect(airportCabFare(resource)?.display).toBe('€191.25');
    expect(nativeAirportCabLeg(itinerary.legs[0], [resource])?.amount).toBe('191.25');
    expect(costPresentation(itinerary, [resource])).toEqual({
      amount: '€191.25',
      estimated: false,
    });
    const html = renderToStaticMarkup(
      <>
        <AirportCabFareDetails resource={resource} />
        <EstimatedCostNote value={itinerary} resources={[resource]} />
      </>,
    );
    expect(html).toContain('€191.25 per vehicle');
    expect(html).toContain('cash, no extras');
    expect(html).toContain('Vehicle availability is unconfirmed');
    expect(html).toContain('does not create a reservation');
    expect(html).not.toMatch(/GBP|ECB|£|Estimated total unavailable|guaranteed settlement/);
  });
  it('does not infer native price from private-transfer type or a missing/mismatched source record', () => {
    expect(costPresentation(itinerary)).toEqual({
      estimated: true,
      amount: 'Estimated total unavailable',
    });
    for (const changed of [
      { ...resource, source: 'Airport Pickups London' },
      { ...resource, quote_total_cost: 195 },
      { ...resource, budget_total_cost: 191.25 },
      { ...resource, eligible_group_ids: ['G2', 'G1'] },
      { ...resource, provenance: {} },
    ])
      expect(airportCabFare(changed)).toBeNull();
    for (const changed of [
      { cost: 192 },
      { departure: 2200 },
      { destination: 'LHR' },
      { resource_id: 'OTHER' },
    ])
      expect(nativeAirportCabLeg({ ...itinerary.legs[0], ...changed }, [resource])).toBeNull();
    const changed = structuredClone(resource);
    const quote = changed.provenance!.airport_cab_quote as {
      options: { price: { currency: string } }[];
    };
    quote.options[0].price.currency = 'GBP';
    expect(airportCabFare(changed)).toBeNull();
    expect(renderToStaticMarkup(<AirportCabFareDetails resource={changed} />)).toContain(
      'Native EUR fare evidence unavailable',
    );
  });
  it('keeps mixed APL totals estimated and preserves native cash fare details', () => {
    const mixed: Itinerary = {
      ...itinerary,
      legs: [
        ...itinerary.legs,
        { ...itinerary.legs[0], resource_id: 'APL-1', origin: 'CRA', destination: 'LHR', cost: 80 },
      ],
      total_cost: 271.25,
    };
    expect(costPresentation(mixed, [resource])).toEqual({
      estimated: true,
      amount: 'Estimated total unavailable',
    });
    expect(costPresentation({ legs: [], arrival: 0, total_cost: 200 })).toEqual({
      estimated: false,
      amount: '€200',
    });
  });
  it('distinguishes CAB final arrival from a CAB initial leg before an onward flight', () => {
    expect(airportCabFinalArrival(itinerary, [resource])).toBe(true);
    expect(
      airportCabFinalArrival(
        {
          ...itinerary,
          legs: [
            ...itinerary.legs,
            { ...itinerary.legs[0], kind: 'flight', resource_id: 'FLIGHT-1', transport_mode: null },
          ],
        },
        [resource],
      ),
    ).toBe(false);
  });
  it('uses only the app receipt for CAB freshness, without supplying a supplier validity time', () => {
    expect(resourceFreshness(resource, now)).toMatchObject({
      state: 'current',
      basis: 'app_receipt',
      quotedAt: null,
    });
    expect(resourceFreshness(resource, now + 180_000)).toMatchObject({
      state: 'stale',
      label: 'Quote stale',
    });
    for (const changed of [
      { ...resource, quote_freshness: null },
      { ...resource, valid_until: '2026-11-01T13:00:00Z' },
      { ...resource, provenance: { ...resource.provenance, quoted_at: '2026-11-01T12:00:00Z' } },
      {
        ...resource,
        quote_freshness: { ...resource.quote_freshness!, received_at: '2026-11-01T14:00:00Z' },
      },
    ])
      expect(resourceFreshness(changed, now).state).toBe('unknown');
  });
  it('renders the imported record as an unbooked group quote with airport-level estimated timing', () => {
    vi.useFakeTimers();
    vi.setSystemTime(now);
    const world = operation();
    const html = renderToStaticMarkup(
      <Inspector
        world={world}
        selectedId={resource.id}
        onSelect={() => {}}
        onClose={() => {}}
        onReplan={() => {}}
        onScenario={() => {}}
        busy={false}
        showLog={false}
        config={{}}
      />,
    );
    for (const expected of [
      'Unbooked vehicle quote',
      '€191.25 per vehicle',
      'Quoted group',
      '2 passengers · one vehicle quote',
      'Origin airport reference',
      'Destination airport reference',
      'Estimated road arrival',
      'without departure-time traffic',
      'Accessibility <b>Unverified',
      'No vehicle position is observed',
    ])
      expect(html).toContain(expected);
    expect(html).not.toMatch(
      /Available \/ total|2 seats|Hotel property|Predicted road arrival|Road prediction received|Price evidence unavailable|GBP|ECB/,
    );
    expect(liveResourceStatus(resource, resourceFreshness(resource, now), false).label).toBe(
      'Unbooked vehicle quote',
    );
    expect(resourceTitle({ ...resource, name: resource.id })).toBe(
      'Bucharest.CAB private transfer',
    );
  });
  it('ends CAB planning at the source advance-notice cutoff even before receipt freshness expires', () => {
    const near = structuredClone(resource);
    const quote = near.provenance!.airport_cab_quote as {
      request: { pickup_at: string };
      policy: Record<string, unknown>;
    };
    quote.request.pickup_at = '2026-11-01T21:01:00Z';
    const boundary = Date.parse('2026-11-01T12:01:00Z');
    expect(resourceFreshness(near, boundary)).toMatchObject({
      state: 'current',
      cutoff: boundary,
      basis: 'app_receipt',
    });
    expect(resourceFreshness(near, boundary + 1)).toMatchObject({
      state: 'stale',
      label: 'Advance-notice window ended',
    });
    expect(
      liveResourceStatus(near, resourceFreshness(near, boundary + 1), false).planningCurrent,
    ).toBe(false);
    expect(resourceFreshness(near, boundary).detail).toContain('not a vehicle hold');
    for (const value of [-1, 169, 9.5, '9', 4]) {
      quote.policy.required_advance_hours = value;
      expect(resourceFreshness(near, now).state).toBe('unknown');
    }
    quote.policy.required_advance_hours = 9;
    quote.request.pickup_at = '2026-11-01T21:01';
    expect(resourceFreshness(near, now).state).toBe('unknown');
  });
  it('retains native amounts and estimated time in passenger and archived source history', () => {
    const world = operation();
    const current = renderToStaticMarkup(<ItineraryView offer={offer} world={world} />);
    expect(current).toContain('Road arrival is estimated');
    expect(current).toContain('€191.25 per vehicle');
    expect(current).not.toContain('APL meeting');
    const history = renderToStaticMarkup(
      <PassengerHistory
        offer={{ ...offer, status: 'expired' }}
        epoch={world.epoch}
        resources={world.resources}
        airports={world.airports}
        archivedResourceIds={[resource.id]}
      />,
    );
    expect(history).toContain('Recorded total: €191.25');
    expect(history).toContain('historical record');
    expect(history).not.toMatch(/GBP|ECB|Estimated total unavailable/);
    const archived = renderToStaticMarkup(
      <SourceRecordDetails
        epoch={world.epoch}
        record={{
          scenario_id: world.scenario_id,
          resource,
          locations: world.airports,
          archived: true,
          archived_at: '2026-11-01T12:03:00Z',
          archived_version: 9,
          archive_reason: 'expired_quote',
        }}
      />,
    );
    expect(archived).toContain('Historical evidence only');
    expect(archived).toContain('Recorded pickup and estimated road arrival');
    expect(archived).toContain('€191.25 per vehicle');
  });
});

describe('CAB geometry and source basis', () => {
  it('keeps endpoint-only geometry even when unrelated Google road metadata is attached', () => {
    const world = operation();
    world.resources[0].provenance!.google_route = {
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
    };
    expect(
      groundPath({ ...world.resources[0], source: 'Airport Pickups London' }, world)?.geometry,
    ).toBe('sourced_road');
    expect(groundPath(world.resources[0], world)?.geometry).toBe('connection_only');
    const map = buildOverlays(world, resource.id, new Map(), now);
    expect(map.routes).toHaveLength(1);
    expect(map.routes[0]).toMatchObject({ observed: false, geometry: 'connection_only' });
    expect(map.routes[0].path).toHaveLength(2);
    expect(map.markers.find((marker) => marker.id === resource.id)?.title).toContain(
      'endpoints only, not a driving path',
    );
    const review = transferReview(world, world.resources[0], now);
    const html = renderToStaticMarkup(
      <GeographicLegend
        mode="live"
        selectedReview={review}
        roadGeometry={{ sourced: false, connectionOnly: true }}
        hasObservations={false}
      />,
    );
    expect(html).toContain('Supplier driving estimate without departure-time traffic');
    expect(html).toContain('no driving path shown');
    expect(html).not.toMatch(
      /Google Routes|Dated prediction|Booking recorded|vehicle position observed/,
    );
  });
});

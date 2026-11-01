import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import Inspector from './Inspector';
import { liveResourceStatus, resourceFreshness } from './resourceFreshness';
import { transferReview } from './resourceLabels';
import type { PostflightConnection, Resource, World } from './types';

const observed = Date.parse('2030-11-01T12:00:00Z');
const epoch = '2030-11-01T00:00:30Z';
const proof: PostflightConnection = {
  source: 'operator_declared_processing_window',
  arrival_basis: 'supplier_schedule_not_observed',
  pickup_rule: 'max_window_start_ceil_arrival_plus_processing',
  incoming_resource_id: 'INBOUND1',
  incoming_resource_fingerprint: 'a'.repeat(64),
  source_arrival_at: '2030-11-02T09:00:30Z',
  airport_processing_minutes: 60,
  pickup_window_start: '2030-11-02T10:00:00Z',
  pickup_window_end: '2030-11-02T12:00:00Z',
  derived_pickup_at: '2030-11-02T10:01:00Z',
};
const incoming: Resource = {
  id: proof.incoming_resource_id,
  name: 'Incoming flight',
  kind: 'flight',
  source: 'Amadeus',
  origin: 'LHR',
  destination: 'OTP',
  departure: 1859,
  arrival: 1980,
  capacity: 2,
  eligible_group_ids: ['G1'],
  capacity_scope: 'quoted_request',
  available: true,
  accessible: false,
  status: 'quoted',
  booking_status: 'unbooked',
  requires_revalidation: true,
  external_offer_id: 'supplier-1',
  quote_total_cost: 100,
  cost_per_person: 0,
  room_capacity: 0,
  vehicle_id: null,
  provenance: {
    provider: 'Amadeus',
    provider_offer_id: 'supplier-1',
    mode: 'live',
    currency: 'EUR',
    origin: 'LHR',
    destination: 'OTP',
    passengers: 2,
    total_price: '100.00',
    departure_at: '2030-11-02T07:00:00Z',
    arrival_at: proof.source_arrival_at,
    quoted_at: '2030-11-01T12:00:00Z',
    expires_at: null,
  },
};
const transfer: Resource = {
  ...incoming,
  id: 'CAB1',
  name: 'Bucharest.CAB transfer',
  kind: 'bus',
  source: 'Bucharest.CAB',
  transport_mode: 'private_transfer',
  capacity_scope: 'private_vehicle_quote',
  origin: 'OTP',
  destination: 'CRA',
  departure: 2041,
  arrival: 2241,
  external_offer_id: null,
  quote_total_cost: 191.25,
  quote_freshness: {
    basis: 'app_receipt',
    max_age_seconds: 120,
    received_at: '2030-11-01T12:01:40Z',
  },
  provenance: {
    direction: 'after_flight_airport_to_airport',
    postflight_connection: proof,
    airport_cab_quote: {
      request: { pickup_at: proof.derived_pickup_at },
      policy: {
        min_advance_day_hours: 4,
        min_advance_night_hours: 9,
        required_advance_hours: 9,
        advance_policy_basis: 'conservative_maximum_of_source_day_night_rules',
      },
    },
  },
};
function operation(): World {
  return {
    scenario_id: 'postflight-freshness',
    version: 1,
    epoch,
    sim_time: 720,
    mode: 'live',
    currency: 'EUR',
    budget: 1000,
    airports: [],
    groups: [],
    resources: structuredClone([transfer, incoming]),
    offers: [],
    reservations: [],
    events: [],
    decisions: [],
    observations: [],
    stats: {},
    provider_status: {},
  };
}
const freshness = (world: World, now = observed + 110_000) =>
  resourceFreshness(world.resources[0], now, world);
afterEach(() => vi.useRealTimers());

describe('bound incoming flight limits postflight CAB freshness', () => {
  it('expires at incoming t=120, not CAB t=220, with an inclusive 120-second boundary', () => {
    const world = operation();
    for (const elapsed of [100_000, 119_999, 120_000]) {
      expect(freshness(world, observed + elapsed)).toMatchObject({
        state: 'current',
        basis: 'app_receipt',
        cutoff: observed + 120_000,
        incomingQuotedAt: observed,
        incomingResourceId: incoming.id,
        receivedAt: observed + 100_000,
        incomingValidUntil: null,
      });
    }
    for (const elapsed of [120_001, 219_000]) {
      const result = freshness(world, observed + elapsed);
      expect(result).toMatchObject({
        state: 'stale',
        label: 'Connection quote stale',
        cutoff: observed + 120_000,
      });
      expect(liveResourceStatus(world.resources[0], result, false).planningCurrent).toBe(false);
    }
  });
  it('still applies quoted_at+120 when supplier expiry is later, and earlier supplier expiry is strict', () => {
    for (const expirySeconds of [110, 300]) {
      const world = operation(),
        flight = world.resources[1];
      flight.valid_until = new Date(observed + expirySeconds * 1000).toISOString();
      flight.provenance!.expires_at = flight.valid_until;
      const cutoff = observed + Math.min(expirySeconds, 120) * 1000;
      expect(freshness(world, cutoff - 1)).toMatchObject({ state: 'current', cutoff });
      expect(freshness(world, cutoff)).toMatchObject({
        state: expirySeconds === 110 ? 'stale' : 'current',
        cutoff,
      });
      expect(freshness(world, cutoff + 1).state).toBe('stale');
    }
  });
  it('keeps an earlier CAB receipt cutoff and exact source advance cutoff', () => {
    const world = operation();
    world.resources[0].quote_freshness!.received_at = new Date(observed).toISOString();
    world.resources[1].provenance!.quoted_at = new Date(observed + 50_000).toISOString();
    expect(freshness(world).cutoff).toBe(observed + 120_000);
    const nearPickup = Date.parse(proof.derived_pickup_at) - 9 * 3_600_000;
    world.resources[0].quote_freshness!.received_at = new Date(nearPickup - 30_000).toISOString();
    world.resources[1].provenance!.quoted_at = new Date(nearPickup - 40_000).toISOString();
    expect(freshness(world, nearPickup)).toMatchObject({ state: 'current', cutoff: nearPickup });
    expect(freshness(world, nearPickup + 1)).toMatchObject({ state: 'stale', cutoff: nearPickup });
  });
  it('does not call a connection current without a current-world incoming binding', () => {
    expect(resourceFreshness(transfer, observed + 110_000).state).toBe('unknown');
    const missing = operation();
    missing.resources.pop();
    expect(freshness(missing).state).toBe('unknown');
    const renamed = operation();
    renamed.resources[1].id = 'OTHER';
    expect(freshness(renamed).state).toBe('unknown');
    const duplicate = operation();
    duplicate.resources.push(structuredClone(duplicate.resources[1]));
    expect(freshness(duplicate).state).toBe('unknown');
  });
  it.each([
    { available: false },
    { status: 'superseded' },
    { kind: 'bus' },
    { source: 'synthetic' },
    { origin: 'OTP' },
    { destination: 'CRA' },
    { capacity: 3 },
    { eligible_group_ids: ['OTHER'] },
    { external_offer_id: 'other-supplier-offer' },
    { booking_status: 'confirmed' },
    { requires_revalidation: false },
    { departure: 1860 },
    { arrival: 1981 },
  ])('fails closed for an unavailable or inconsistent incoming record %j', (change) => {
    const world = operation();
    Object.assign(world.resources[1], change);
    expect(freshness(world).state).toBe('unknown');
  });
  it.each([
    { provider: 'other' },
    { mode: 'sandbox' },
    { currency: 'USD' },
    { passengers: 3 },
    { origin: 'CDG' },
    { destination: 'CRA' },
    { provider_offer_id: 'other' },
    { arrival_at: '2030-11-02T09:01:30Z' },
    { quoted_at: null },
    { quoted_at: 'invalid' },
    { quoted_at: '2030-11-01T12:00:00' },
    { quoted_at: '2030-11-01T12:01:51Z' },
    { expires_at: '2030-11-01T12:05:00Z' },
  ])('does not accept missing, future or conflicting source evidence %j', (change) => {
    const world = operation();
    Object.assign(world.resources[1].provenance!, change);
    expect(freshness(world).state).toBe('unknown');
  });
  it('rejects invalid or unmatched expiry rather than falling back to receipt age', () => {
    for (const expiry of [
      'invalid',
      '2030-11-01T12:05:00',
      '2030-02-30T12:05:00Z',
      '2030-11-01T12:05:00Z',
    ]) {
      const world = operation();
      world.resources[1].valid_until = expiry;
      expect(freshness(world).state).toBe('unknown');
      world.resources[1].provenance!.expires_at = expiry;
      if (expiry !== '2030-11-01T12:05:00Z') expect(freshness(world).state).toBe('unknown');
    }
  });
  it('does not switch to a newer observation of the same supplier offer behind a frozen binding', () => {
    const world = operation(),
      newer = structuredClone(world.resources[1]);
    newer.id = 'NEWER';
    newer.provenance!.quoted_at = '2030-11-01T12:00:20Z';
    world.resources.push(newer);
    expect(freshness(world).state).toBe('unknown');
    newer.external_offer_id = 'different-offer';
    expect(freshness(world).state).toBe('current');
  });
  it('rejects malformed proof, mismatched pickup and an inconsistent planning minute', () => {
    for (const change of [
      null,
      { ...proof, arrival_basis: 'observed' },
      { ...proof, incoming_resource_id: 'OTHER' },
      { ...proof, incoming_resource_fingerprint: 'invalid' },
    ]) {
      const world = operation();
      world.resources[0].provenance!.postflight_connection = change;
      expect(freshness(world).state).toBe('unknown');
    }
    const world = operation();
    // Epoch includes 30 seconds: valid conservative minute 2041 is not the exact source pickup.
    expect(freshness(world).state).toBe('current');
    world.resources[0].departure = 2040;
    expect(freshness(world).state).toBe('unknown');
    world.resources[0].departure = 2041;
    const quote = world.resources[0].provenance!.airport_cab_quote as {
      request: { pickup_at: string };
    };
    quote.request.pickup_at = '2030-11-02T10:01:30Z';
    expect(freshness(world).state).toBe('unknown');
  });
  it('leaves initial CAB receipt freshness unchanged and fails closed on invalid clocks', () => {
    const initial = structuredClone(transfer);
    initial.provenance!.direction = 'initial_airport_to_airport';
    delete initial.provenance!.postflight_connection;
    expect(resourceFreshness(initial, observed + 219_000)).toMatchObject({
      state: 'current',
      cutoff: observed + 220_000,
    });
    expect(freshness(operation(), NaN).state).toBe('unknown');
    expect(freshness(operation(), observed + 99_999).state).toBe('unknown');
  });
  it('shows the combined cutoff in Inspector and shared map review without labeling it CAB supplier expiry', () => {
    vi.useFakeTimers();
    vi.setSystemTime(observed + 121_000);
    const world = operation();
    const html = renderToStaticMarkup(
      <Inspector
        world={world}
        selectedId="CAB1"
        onSelect={() => {}}
        onClose={() => {}}
        onReplan={() => {}}
        onScenario={() => {}}
        busy={false}
        config={{}}
        showLog={false}
      />,
    );
    expect(html).toContain('Connection quote stale');
    expect(html).toContain('Bound incoming flight quoted at');
    expect(html).toContain('Connection planning freshness through');
    expect(html).toContain('01 Nov 2030, 12:02:00 UTC');
    expect(html).not.toContain('12:03:40 UTC');
    expect(html).not.toContain('Recorded validity until');
    expect(html).toContain('CAB supplier expiry and fleet availability remain unknown');
    expect(transferReview(world, world.resources[0], observed + 121_000).status).toContain(
      'Connection quote stale',
    );
  });
});

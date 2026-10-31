import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import ResourceRail from './ResourceRail';
import Inspector from './Inspector';
import {
  liveResourceStatus,
  recordedBooking,
  resourceFreshness,
  startFreshnessClock,
} from './resourceFreshness';
import type { AirportArrivalAssumption, Offer, Resource, World } from './types';

const quotedAt = Date.parse('2026-10-31T20:00:00Z');
const resource: Resource = {
  id: 'QUOTE-1',
  kind: 'flight',
  name: 'Supplier flight',
  origin: 'LHR',
  destination: 'OTP',
  departure: 2000,
  arrival: 2200,
  capacity: 2,
  accessible: false,
  available: true,
  cost_per_person: 0,
  room_capacity: 1,
  vehicle_id: null,
  status: 'quoted',
  source: 'Nuitee',
  requires_revalidation: true,
  capacity_scope: 'quoted_request',
  booking_status: 'unbooked',
  provenance: { quoted_at: '2026-10-31T20:00:00Z' },
};
function operation(item = resource, mode: World['mode'] = 'live'): World {
  return {
    scenario_id: 'freshness-fixture',
    version: 1,
    epoch: '2026-10-31T00:00:00+02:00',
    sim_time: 1200,
    mode,
    currency: 'EUR',
    budget: 1000,
    airports: [],
    resources: [item],
    groups: [],
    offers: [],
    reservations: [],
    events: [],
    decisions: [],
    observations: [],
    stats: {},
    provider_status: {},
  };
}
function rail(world: World) {
  return renderToStaticMarkup(
    <ResourceRail
      world={world}
      selectedId={null}
      onSelect={() => {}}
      onMode={() => {}}
      onScenario={() => {}}
      onLiveData={() => {}}
    />,
  );
}
function inspector(world: World) {
  return renderToStaticMarkup(
    <Inspector
      world={world}
      selectedId={world.resources[0].id}
      onSelect={() => {}}
      onClose={() => {}}
      onReplan={() => {}}
      onScenario={() => {}}
      busy={false}
      config={{}}
      showLog={false}
    />,
  );
}
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('live resource freshness and presentation', () => {
  const returnTransfer: Resource = {
    ...resource,
    kind: 'bus',
    transport_mode: 'private_transfer',
    source: 'Airport Pickups London',
    capacity_scope: 'private_vehicle_quote',
    quote_freshness: {
      basis: 'app_receipt',
      received_at: '2026-10-31T20:00:00Z',
      max_age_seconds: 120,
    },
    airport_arrival_assumption: {
      source: 'operator_declared',
      confirmed: true,
      group_id: 'G1',
      group_fingerprint: 'a'.repeat(64),
      scenario_id: 'freshness-fixture',
      epoch: '2026-10-31T00:00:00+02:00',
      airport_id: 'LHR',
      onward_resource_id: 'ONWARD-1',
      onward_flight_fingerprint: 'b'.repeat(64),
      onward_departure_at: '2026-11-02T18:00:00Z',
      dropoff: { label: 'Reported drop-off', coordinates: { latitude: 51.47, longitude: -0.48 } },
      processing_minutes: 120,
      departure_terminal: null,
    } satisfies AirportArrivalAssumption,
    provenance: { google_route: { source: 'Google Routes', fetched_at: '2026-10-31T19:59:30Z' } },
  };

  it('limits return planning by the earlier of the distinct APL and Google receipt windows', () => {
    const boundary = quotedAt + 90_000;
    expect(resourceFreshness(returnTransfer, boundary)).toMatchObject({
      state: 'current',
      basis: 'app_receipt',
      receivedAt: quotedAt,
      routeReceivedAt: quotedAt - 30_000,
      cutoff: boundary,
    });
    expect(resourceFreshness(returnTransfer, boundary + 1)).toMatchObject({
      state: 'stale',
      label: 'Quote stale',
      cutoff: boundary,
    });
    expect(resourceFreshness(returnTransfer, boundary).detail).toContain('earlier cutoff');
    const laterRoute = {
      ...returnTransfer,
      provenance: { google_route: { source: 'Google Routes', fetched_at: '2026-10-31T20:00:30Z' } },
    };
    expect(resourceFreshness(laterRoute, quotedAt + 120_000)).toMatchObject({
      state: 'current',
      cutoff: quotedAt + 120_000,
      routeReceivedAt: quotedAt + 30_000,
    });
    expect(resourceFreshness(laterRoute, quotedAt + 120_001).state).toBe('stale');
    const outbound = { ...returnTransfer, airport_arrival_assumption: null };
    expect(resourceFreshness(outbound, boundary + 1)).toMatchObject({
      state: 'current',
      cutoff: quotedAt + 120_000,
      routeReceivedAt: null,
    });
  });

  it('does not show return freshness when the road receipt is missing, malformed or future-dated', () => {
    for (const fetched_at of [
      undefined,
      'not-a-date',
      '2026-10-31T20:00:00',
      '2026-02-30T20:00:00Z',
      '2026-10-31T20:00:00.001Z',
    ]) {
      const item = {
        ...returnTransfer,
        provenance: { google_route: { source: 'Google Routes', fetched_at } },
      };
      expect(resourceFreshness(item, quotedAt)).toMatchObject({ state: 'unknown', cutoff: null });
      expect(resourceFreshness(item, quotedAt).detail).toContain('Google Routes receipt time');
      expect(
        liveResourceStatus(item, resourceFreshness(item, quotedAt), false).planningCurrent,
      ).toBe(false);
    }
    for (const google_route of [null, [], 'invalid'])
      expect(
        resourceFreshness({ ...returnTransfer, provenance: { google_route } }, quotedAt).state,
      ).toBe('unknown');
  });

  it('shows both UTC receipts and their earlier cutoff without cancelling a recorded booking', () => {
    vi.useFakeTimers();
    vi.setSystemTime(quotedAt + 90_001);
    const item = { ...returnTransfer, booking_status: 'confirmed' };
    const freshness = resourceFreshness(item, Date.now());
    expect(
      liveResourceStatus(item, freshness, recordedBooking(operation(item), item)),
    ).toMatchObject({ label: 'Booking recorded', planningCurrent: false });
    const html = inspector(operation(item));
    expect(html).toContain('Road prediction received');
    expect(html).toContain('19:59:30 UTC');
    expect(html).toContain('APL response received by app');
    expect(html).toContain('20:00:00 UTC');
    expect(html).toContain('Return planning freshness through');
    expect(html).toContain('20:01:30 UTC');
    expect(html).toContain('Quote stale');
    expect(html).toContain('Booking recorded');
    expect(html).toContain('earlier cutoff');
  });
  it('uses a distinct app-receipt window without inventing a source quote timestamp', () => {
    const item: Resource = {
      ...resource,
      source: 'Airport Pickups London',
      provenance: { source_quoted_at: null },
      quote_freshness: {
        basis: 'app_receipt',
        received_at: '2026-10-31T20:00:00Z',
        max_age_seconds: 120,
      },
    };
    expect(resourceFreshness(item, quotedAt + 120_000)).toMatchObject({
      state: 'current',
      basis: 'app_receipt',
      quotedAt: null,
      receivedAt: quotedAt,
      cutoff: quotedAt + 120_000,
    });
    expect(resourceFreshness(item, quotedAt + 120_001).state).toBe('stale');
    expect(resourceFreshness(item, quotedAt - 1).state).toBe('unknown');
    expect(
      resourceFreshness(
        {
          ...item,
          provenance: resource.provenance,
          quote_freshness: { ...item.quote_freshness!, received_at: 'not-a-date' },
        },
        quotedAt,
      ).state,
    ).toBe('unknown');
    expect(
      resourceFreshness({ ...item, valid_until: '2026-10-31T20:02:00Z' }, quotedAt).state,
    ).toBe('unknown');
    expect(resourceFreshness({ ...item, provenance: resource.provenance }, quotedAt).state).toBe(
      'unknown',
    );
    expect(resourceFreshness({ ...item, quote_freshness: null }, quotedAt).state).toBe('unknown');
    expect(resourceFreshness({ ...item, source: 'Nuitee' }, quotedAt).state).toBe('unknown');
    expect(
      liveResourceStatus(
        { ...item, capacity_scope: 'private_vehicle_quote' },
        resourceFreshness(item, quotedAt),
        false,
      ).label,
    ).toBe('Unbooked vehicle quote');
  });

  it('uses the backend inclusive 120-second fallback, without calling it supplier expiry', () => {
    expect(resourceFreshness(resource, quotedAt + 120_000).state).toBe('current');
    const stale = resourceFreshness(resource, quotedAt + 120_001);
    expect(stale).toMatchObject({
      state: 'stale',
      basis: 'quoted_at',
      cutoff: quotedAt + 120_000,
      label: 'Quote stale',
    });
    expect(stale.detail).toContain('not a supplier expiry');
  });

  it('gives explicit validity precedence and rejects the exact expiry boundary', () => {
    const item = { ...resource, valid_until: '2026-10-31T22:05:00+02:00' };
    expect(resourceFreshness(item, quotedAt + 299_999).state).toBe('current');
    expect(resourceFreshness(item, quotedAt + 300_000)).toMatchObject({
      state: 'stale',
      basis: 'valid_until',
    });
    expect(
      resourceFreshness({ ...item, provenance: { quoted_at: '2026-11-01T20:00:00Z' } }, quotedAt)
        .state,
    ).toBe('current');
    expect(resourceFreshness({ ...resource, valid_until: 'invalid' }, quotedAt).state).toBe(
      'unknown',
    );
  });

  it('does not invent freshness from missing, naive, impossible or future source dates', () => {
    for (const value of [
      undefined,
      'not-a-date',
      '2026-10-31T20:00:00',
      '2026-02-30T20:00:00Z',
      '2026-10-31T20:00:01Z',
    ]) {
      expect(
        resourceFreshness({ ...resource, provenance: { quoted_at: value } }, quotedAt).state,
      ).toBe('unknown');
    }
    expect(resourceFreshness({ ...resource, requires_revalidation: false }, quotedAt).state).toBe(
      'unknown',
    );
    expect(
      resourceFreshness(
        { ...resource, source: 'synthetic', valid_until: '2099-01-01T00:00:00Z' },
        quotedAt,
      ).state,
    ).toBe('unknown');
  });

  it('does not revive unavailable or superseded resources even with a future validity time', () => {
    const item = { ...resource, valid_until: '2099-01-01T00:00:00Z' };
    const freshness = resourceFreshness(item, quotedAt);
    expect(liveResourceStatus({ ...item, available: false }, freshness, false)).toMatchObject({
      label: 'Unavailable',
      tone: 'red',
      planningCurrent: false,
    });
    expect(liveResourceStatus({ ...item, status: 'superseded' }, freshness, false)).toMatchObject({
      label: 'Superseded',
      planningCurrent: false,
    });
  });

  it('recognizes actual recorded confirmations, not accepted passenger preferences', () => {
    const world = operation();
    world.offers = [
      { status: 'confirmed', itinerary: { legs: [{ resource_id: resource.id }] } } as Offer,
    ];
    expect(recordedBooking(world, resource)).toBe(true);
    world.offers[0].status = 'accepted';
    expect(recordedBooking(world, resource)).toBe(false);
    expect(recordedBooking(world, { ...resource, booking_status: 'confirmed' })).toBe(true);
  });

  it('labels expired and unknown flight records without an available group quote or seat count', () => {
    vi.useFakeTimers();
    vi.setSystemTime(quotedAt + 121_000);
    const stale = rail(operation());
    expect(stale).toContain('Flight records');
    expect(stale).toContain('1 record');
    expect(stale).toContain('Quote stale');
    expect(stale).not.toContain('>Group quote</span>');
    const unknown = rail(
      operation({ ...resource, capacity_scope: 'operator_confirmed', provenance: {} }),
    );
    expect(unknown).toContain('Freshness unknown');
    expect(unknown).not.toContain('2 seats');
    const expired = rail(operation({ ...resource, valid_until: '2026-10-31T20:00:00Z' }));
    expect(expired).toContain('Validity expired');
  });

  it('shows hotel source time and the local freshness cutoff in explicit UTC', () => {
    vi.useFakeTimers();
    vi.setSystemTime(quotedAt + 121_000);
    const html = inspector(
      operation({ ...resource, kind: 'hotel', capacity: 1, source: 'Nuitee / LiteAPI' }),
    );
    expect(html).toContain('Quote stale');
    expect(html).toContain('Source quoted at');
    expect(html).toContain('31 Oct 2026, 20:00:00 UTC');
    expect(html).toContain('Local freshness through');
    expect(html).toContain('31 Oct 2026, 20:02:00 UTC');
    expect(html).toContain('120-second local freshness window');
    expect(html).toContain('Recorded quote scope');
    expect(html).not.toContain('Available / total');
  });

  it('keeps a recorded booking separate from expired quote freshness and availability', () => {
    vi.useFakeTimers();
    vi.setSystemTime(quotedAt + 121_000);
    const world = operation({ ...resource, booking_status: 'confirmed' });
    const html = inspector(world);
    expect(html).toContain('Booking recorded');
    expect(html).toContain('Quote stale');
    expect(html).toContain('Quote age does not cancel the recorded booking.');
    expect(rail(world)).toContain('Booking recorded · Quote stale');
    world.resources[0].available = false;
    expect(rail(world)).toContain('>Unavailable</span>');
    expect(inspector(world)).toContain('Confirmation recorded by operations');
  });

  it('preserves simulation availability and capacities despite old or absent quote times', () => {
    vi.useFakeTimers();
    vi.setSystemTime(quotedAt + 121_000);
    const world = operation(
      { ...resource, source: 'synthetic', capacity_scope: 'operator_confirmed' },
      'simulation',
    );
    expect(rail(world)).toContain('2 seats');
    const html = inspector(world);
    expect(html).toContain('Available / total');
    expect(html).not.toContain('Freshness unknown');
    expect(html).not.toContain('Quote stale');
  });

  it('updates visible records as wall time passes, without another world response or provider call', () => {
    vi.useFakeTimers();
    vi.setSystemTime(quotedAt + 119_000);
    const fetch = vi.spyOn(globalThis, 'fetch');
    const renderings: string[] = [];
    const stop = startFreshnessClock(() => renderings.push(rail(operation())));
    expect(renderings.at(-1)).toContain('>Group quote</span>');
    vi.advanceTimersByTime(1000);
    expect(renderings.at(-1)).toContain('>Group quote</span>');
    vi.advanceTimersByTime(1000);
    expect(renderings.at(-1)).toContain('Quote stale');
    stop();
    const count = renderings.length;
    vi.advanceTimersByTime(5000);
    expect(renderings).toHaveLength(count);
    expect(fetch).not.toHaveBeenCalled();
  });
});

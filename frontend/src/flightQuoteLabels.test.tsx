import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { flightQuotePresentation } from './resourceLabels';
import { returnFlightChoices } from './planningTransferData';
import ResourceRail from './ResourceRail';
import type { Resource, World } from './types';

const epoch = '2026-11-01T00:00:00Z';
const now = Date.parse('2026-11-01T08:00:00Z');
const segments = [
  {
    mode: 'flight',
    origin: 'LHR',
    destination: 'ZRH',
    carrier: 'Example Air',
    flight_number: '317',
    departure_at: '2026-11-01T09:00:00Z',
    arrival_at: '2026-11-01T10:55:00Z',
  },
  {
    mode: 'flight',
    origin: 'ZRH',
    destination: 'OTP',
    carrier: 'Example Air',
    flight_number: '1886',
    departure_at: '2026-11-01T20:55:00Z',
    arrival_at: '2026-11-01T23:15:00Z',
  },
];
const flight: Resource = {
  id: 'Q-opaque-12345678',
  name: 'nuitee',
  kind: 'flight',
  origin: 'LHR',
  destination: 'OTP',
  departure: 540,
  arrival: 1395,
  capacity: 2,
  accessible: false,
  available: true,
  cost_per_person: 0,
  room_capacity: 0,
  vehicle_id: null,
  status: 'quoted',
  source: 'nuitee',
  booking_status: 'unbooked',
  capacity_scope: 'quoted_request',
  eligible_group_ids: ['G1'],
  external_offer_id: 'source-quote',
  valid_until: '2026-11-01T08:30:00Z',
  provenance: {
    mode: 'live',
    provider: 'nuitee',
    provider_offer_id: 'source-quote',
    origin: 'LHR',
    destination: 'OTP',
    departure_at: '2026-11-01T09:00:00Z',
    quoted_at: '2026-11-01T07:59:00Z',
    segments,
  },
};
function operation(resources = [flight]): World {
  return {
    scenario_id: 'label-fixture',
    version: 1,
    epoch,
    sim_time: 480,
    mode: 'live',
    currency: 'EUR',
    budget: 1000,
    airports: [],
    resources,
    groups: [{ id: 'G1', name: 'Example party', size: 2, destination: 'OTP', deadline: 1500 }],
    offers: [],
    reservations: [],
    events: [],
    decisions: [],
    observations: [],
    stats: {},
    provider_status: {},
  } as unknown as World;
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
afterEach(() => vi.useRealTimers());

describe('source-validated flight quote labels', () => {
  it('uses reconciled segment identities and a connection count consistently in the list and exact-flight choices', () => {
    vi.useFakeTimers();
    vi.setSystemTime(now);
    const label = flightQuotePresentation(flight, epoch);
    expect(label).toEqual({
      title: 'Example Air 317',
      detail: '1 connection · quote 12345678',
      reference: '12345678',
      identities: ['Example Air 317', 'Example Air 1886'],
    });
    const choice = returnFlightChoices(operation(), 'G1', now)[0];
    expect(choice.flightLabel).toEqual(label);
    expect(choice.eligible).toBe(true);
    const html = rail(operation());
    expect(html).toContain('<strong>Example Air 317</strong>');
    expect(html).toContain('1 connection · quote 12345678');
    expect(html).toContain('Example Air 1886'); // All validated identities remain in the row tooltip/search data.
    expect(html).not.toContain('<strong>Q-opaque-12345678</strong>');
  });
  it('does not turn an opaque quote/name or malformed/mismatched itinerary into a flight identity', () => {
    const malformed: Resource[] = [
      { ...flight, provenance: {} },
      { ...flight, departure: 500 },
      { ...flight, arrival: 1394 },
      { ...flight, provenance: { segments: [segments[0], { ...segments[1], origin: 'CDG' }] } },
      {
        ...flight,
        provenance: {
          segments: [{ ...segments[0], departure_at: '2026-02-30T09:00:00Z' }, segments[1]],
        },
      },
    ];
    for (const value of malformed) {
      const label = flightQuotePresentation(
        { ...value, name: 'Supposed flight BA987', id: 'Q-BA987-12345678' },
        epoch,
      );
      expect(label.title).toBe('Flight quote');
      expect(label.detail).toBe('Flight identity unavailable · quote 12345678');
      expect(label.identities).toEqual([]);
    }
    expect(flightQuotePresentation(flight, '2026-11-02T00:00:00Z').title).toBe('Flight quote');
  });
  it('keeps missing identity honest and does not call a single segment nonstop when it has a technical stop', () => {
    const one = {
      ...flight,
      destination: 'ZRH',
      arrival: 655,
      provenance: {
        segments: [
          { ...segments[0], carrier: null, flight_number: 'XY317', technical_stop_count: 1 },
        ],
      },
    };
    const label = flightQuotePresentation(one, epoch);
    expect(label.title).toBe('Flight XY317');
    expect(label.detail).toBe('1 flight segment · quote 12345678');
    expect(JSON.stringify(label)).not.toMatch(/nonstop|direct/i);
    const unnamed = flightQuotePresentation(
      {
        ...one,
        provenance: { segments: [{ ...segments[0], carrier: null, flight_number: null }] },
      },
      epoch,
    );
    expect(unnamed.title).toBe('Flight quote');
    expect(unnamed.detail).toContain('Flight identity unavailable');
  });
  it('retains unique quote references and stale selection restrictions even when flight identities match', () => {
    const choices = returnFlightChoices(
      operation([
        flight,
        { ...flight, id: 'Q-other-87654321', valid_until: '2026-11-01T07:59:59Z' },
      ]),
      'G1',
      now,
    );
    expect(choices.map((choice) => choice.flightLabel.title)).toEqual([
      'Example Air 317',
      'Example Air 317',
    ]);
    expect(choices.map((choice) => choice.flightLabel.reference)).toEqual(['12345678', '87654321']);
    expect(choices[1].eligible).toBe(false);
    expect(choices[1].reason).toContain('refresh flight quote');
    expect(choices[1].resource.id).toBe('Q-other-87654321');
  });
  it('preserves simulation row names and renders provider text as text', () => {
    const simulation = {
      ...operation([{ ...flight, id: 'F101', source: 'synthetic' }]),
      mode: 'simulation' as const,
    };
    expect(rail(simulation)).toContain('<strong>F101</strong>');
    const hostile = {
      ...flight,
      provenance: {
        ...flight.provenance,
        segments: [{ ...segments[0], carrier: '<b>Example</b>\nAir' }, segments[1]],
      },
    };
    const html = rail(operation([hostile]));
    expect(html).toContain('&lt;b&gt;Example&lt;/b&gt; Air 317');
    expect(html).not.toContain('<b>Example</b>');
  });
});

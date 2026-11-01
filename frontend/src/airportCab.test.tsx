import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import AirportCabPanel, { AirportCabResults } from './AirportCabPanel';
import { ApiError, SearchOutcomeUnknownError, TransportError } from './api';
import {
  AIRPORT_CAB_SOURCE,
  AirportCabRequestGate,
  airportCabApi,
  airportCabContext,
  airportCabCurrent,
  airportCabDefaultPickup,
  airportCabGroupIssue,
  airportCabInput,
  verifyAirportCabQuote,
  verifyAirportCabStatus,
} from './airportCabData';
import type { AirportCabForm, AirportCabQuote, AirportCabStatus } from './airportCabData';
import type { World } from './types';

const now = Date.parse('2030-11-01T12:00:00Z');
const world = {
  mode: 'live',
  scenario_id: 'existing-operation',
  version: 7,
  epoch: '2030-11-01T00:00:00Z',
  groups: [
    {
      id: 'G1',
      name: 'Test group 1 OTP',
      size: 2,
      adult_count: 2,
      origin: 'OTP',
      destination: 'LHR',
      ready_at: 600,
      deadline: 5760,
      status: 'affected',
      accessible: false,
      budget: 1000,
    },
  ],
  offers: [],
  resources: [],
} as unknown as World;
const status: AirportCabStatus = {
  provider: 'Bucharest.CAB',
  configured: true,
  source_url: AIRPORT_CAB_SOURCE,
  supported_routes: [
    {
      origin_airport: 'OTP',
      origin_name: 'Bucharest Henri Coanda Airport',
      destination_airport: 'CRA',
      destination_name: 'Craiova Airport',
    },
  ],
  max_passengers: 7,
  max_advance_days: 31,
  booking_supported: false,
};
const form: AirportCabForm = {
  group_id: 'G1',
  pickup_at: '2030-11-02T14:00',
  suitcases: '2',
  handbags: '1',
};
const input = airportCabInput(form, world, status, now);
function quote(): AirportCabQuote {
  return {
    quote_id: 'CAB-QUOTE-1',
    group_id: 'G1',
    source_version: 7,
    created_at: '2030-11-01T12:00:00Z',
    usable_until: '2030-11-01T12:02:00Z',
    state: 'ready',
    replayed: false,
    blockers: [],
    options: [{ option_id: 'CAR-1', importable: true, blockers: [] }],
    transfer: {
      provider: 'Bucharest.CAB',
      source_url: AIRPORT_CAB_SOURCE,
      state: 'ready',
      transport: 'private_transfer',
      booking_supported: false,
      received_at: '2030-11-01T12:00:00Z',
      source_quoted_at: null,
      expires_at: null,
      price_basis: 'supplier_client_formula_cash_no_extras',
      request: {
        origin_airport: 'OTP',
        destination_airport: 'CRA',
        pickup_at: input.pickup_at,
        passengers: 2,
        suitcases: 2,
        handbags: 1,
        timezone: 'Europe/Bucharest',
        supplier_pickup_datetime: '2030-11-02 16:00:00',
        trip_type: 'from-airport',
        baggage_supplier_scoped: false,
      },
      resolved_route: {
        origin: { airport_code: 'OTP', name: 'Henri Coanda Airport', address: 'Otopeni' },
        destination: { airport_code: 'CRA', name: 'Craiova Airport', address: 'Craiova' },
      },
      route: {
        distance_meters: 246912,
        duration_seconds: 12000,
        duration_text: '3 hours 20 mins',
        duration_basis: 'source_estimate_without_departure_time',
      },
      options: [
        {
          id: 'CAR-1',
          provider_offer_id: null,
          provider_vehicle_type_id: 4,
          car_type: 'Standard',
          price: { amount: '191.00', currency: 'EUR' },
          price_scope: 'per_vehicle',
          max_passengers: 3,
          max_suitcases: 3,
          max_handbags: 3,
          fits_request: true,
          availability_status: 'quoted_not_reserved',
          remaining_fleet: null,
          supplier_valid_until: null,
          accessibility_confirmed: false,
        },
      ],
      notices: ['Optional payment-link fees are excluded.'],
    },
  };
}
const markup = (result = quote(), overrides = {}) =>
  renderToStaticMarkup(
    <AirportCabResults
      result={result}
      now={now}
      pending={false}
      contextCurrent={true}
      importedOptionId={null}
      onImport={() => {}}
      {...overrides}
    />,
  );
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('existing-group airport transfer request', () => {
  it('keeps exact existing group scope and allows CRA before an onward destination', () => {
    expect(input).toEqual({
      group_id: 'G1',
      destination_airport: 'CRA',
      pickup_at: '2030-11-02T14:00:00.000Z',
      suitcases: 2,
      handbags: 1,
    });
    expect(input).not.toHaveProperty('passengers');
    expect(world.groups[0].destination).toBe('LHR');
    expect(world.groups[0].origin).toBe('OTP');
  });
  it.each([
    { origin: 'CRA' },
    { size: 8, adult_count: 8 },
    { adult_count: null },
    { adult_count: 1 },
    { accessible: true },
    { status: 'confirmed' },
  ])('rejects unsuitable recorded group %j before a source call', (changes) => {
    const changed = structuredClone(world);
    Object.assign(changed.groups[0], changes);
    expect(() => airportCabInput(form, changed, status, now)).toThrow();
  });
  it('does not let a stale group status bypass an existing confirmed offer', () => {
    const changed = structuredClone(world);
    changed.offers = [{ group_id: 'G1', status: 'confirmed' }] as World['offers'];
    expect(airportCabGroupIssue(changed, changed.groups[0])).toContain('bookings');
  });
  it.each([
    { suitcases: '' },
    { handbags: '' },
    { suitcases: '-1' },
    { handbags: '1.2' },
    { suitcases: '8' },
    { pickup_at: '2030-11-01T20:59' },
    { pickup_at: '2030-11-01T21:00:15' },
    { pickup_at: '2030-11-05T00:00' },
    { pickup_at: '2030-12-03T12:00' },
  ])('rejects missing, impossible or out-of-window inputs %j', (changes) => {
    expect(() => airportCabInput({ ...form, ...changes }, world, status, now)).toThrow();
  });
  it('rejects simulation, disconnected sources and missing groups', () => {
    expect(() => airportCabInput(form, { ...world, mode: 'simulation' }, status, now)).toThrow(
      'live',
    );
    expect(() => airportCabInput(form, world, { ...status, configured: false }, now)).toThrow(
      'not available',
    );
    expect(() => airportCabInput({ ...form, group_id: 'other' }, world, status, now)).toThrow(
      'existing',
    );
  });
  it('suggests an editable future pickup without changing readiness or deadline', () => {
    const before = structuredClone(world);
    expect(airportCabDefaultPickup(world, world.groups[0], now)).toBe('2030-11-02T12:00');
    expect(airportCabDefaultPickup(world, { ...world.groups[0], ready_at: 3000 }, now)).toBe(
      '2030-11-03T02:00',
    );
    expect(world).toEqual(before);
  });
});

describe('source receipt boundaries', () => {
  it('accepts native EUR supplier receipt and app window without inventing expiry', () => {
    expect(verifyAirportCabStatus(status)).toBe(status);
    const result = quote();
    expect(verifyAirportCabQuote(result, { input, passengers: 2 })).toBe(result);
    expect(airportCabCurrent(result, now)).toBe(true);
    expect(airportCabCurrent(result, now + 120_000)).toBe(false);
    expect(airportCabCurrent(result, now - 1)).toBe(false);
    expect(result.transfer?.expires_at).toBeNull();
  });
  it.each(['RON', 'GBP', 'USD'])('fails closed on unconfirmed original currency %s', (currency) => {
    const result = quote();
    (result.transfer!.options[0].price as { currency: string }).currency = currency;
    expect(() => verifyAirportCabQuote(result)).toThrow('Import is unavailable');
  });
  it.each(['', 'NaN', '-5', '0.00', '1e3', '191.001'])(
    'rejects invalid source money %s',
    (amount) => {
      const result = quote();
      result.transfer!.options[0].price.amount = amount;
      expect(() => verifyAirportCabQuote(result)).toThrow();
    },
  );
  it('rejects swapped group, party, bags or pickup receipts', () => {
    for (const mutate of [
      (result: AirportCabQuote) => {
        result.group_id = 'G2';
      },
      (result: AirportCabQuote) => {
        result.transfer!.request.passengers = 3;
      },
      (result: AirportCabQuote) => {
        result.transfer!.request.suitcases = 0;
      },
      (result: AirportCabQuote) => {
        result.transfer!.request.pickup_at = '2030-11-02T15:00:00Z';
      },
    ]) {
      const result = quote();
      mutate(result);
      expect(() => verifyAirportCabQuote(result, { input, passengers: 2 })).toThrow();
    }
  });
  it('rejects unsafe source links, fabricated stock and contradictory eligibility', () => {
    for (const mutate of [
      (result: AirportCabQuote) => {
        result.transfer!.source_url = 'https://example.com/checkout';
      },
      (result: AirportCabQuote) => {
        Object.assign(result.transfer!.options[0], { remaining_fleet: 7 });
      },
      (result: AirportCabQuote) => {
        result.transfer!.options[0].max_passengers = 1;
      },
      (result: AirportCabQuote) => {
        result.options[0].option_id = 'FOREIGN-OPTION';
      },
      (result: AirportCabQuote) => {
        result.options[0].blockers = [{ code: 'late', message: 'Too late' }];
      },
      (result: AirportCabQuote) => {
        result.state = 'blocked';
      },
    ]) {
      const result = quote();
      mutate(result);
      expect(() => verifyAirportCabQuote(result)).toThrow();
    }
  });
  it('keeps a valid blocked result with source limitations and no price', () => {
    const result: AirportCabQuote = {
      ...quote(),
      state: 'blocked',
      transfer: null,
      options: [],
      blockers: [{ code: 'unsupported', message: 'No fitting vehicle was quoted.' }],
    };
    expect(verifyAirportCabQuote(result)).toBe(result);
    const html = markup(result);
    expect(html).toContain('No fitting vehicle was quoted.');
    expect(html).not.toContain('Add group planning leg');
    expect(html).not.toContain('€191.00');
  });
});

describe('vehicle-fare presentation and stale attempts', () => {
  it('shows original money, raw supplier local clock, observed unknowns and one planning boundary', () => {
    const html = markup();
    for (const label of [
      '€191.00',
      'per vehicle',
      'cash, no extras',
      'No reservation or hold',
      '2030-11-02 16:00:00',
      'Europe/Bucharest',
      'without departure-time traffic',
      'not supplier expiry',
      'Remaining fleet: unknown',
      'Supplier quote time and expiry: unknown',
      'initial OTP',
      'complete itinerary must be planned separately',
      'at most one Bucharest.CAB vehicle',
    ])
      expect(html).toContain(label);
    expect(html).toContain('Vehicle limits: 3 passengers');
    expect(html).not.toContain('seats available');
    expect(html).not.toContain('Reserve');
    expect(html).not.toContain('Book now');
    expect(html).not.toContain('Google Routes');
    expect(html).not.toContain('ECB');
    expect(html).toContain('<details class="airport-cab-source"><summary>');
    expect(html).not.toContain('disabled=""');
  });
  it.each([
    { now: now + 120_000 },
    { pending: true },
    { contextCurrent: false },
    { importedOptionId: 'CAR-1' },
  ])('disables new import when %j', (overrides) => {
    expect(markup(quote(), overrides)).toContain('disabled=""');
  });
  it('shows blocked vehicle limitations rather than clickable capacity stock', () => {
    const result = quote();
    result.options[0].importable = false;
    result.options[0].blockers = [
      { code: 'deadline', message: 'Estimated arrival is after the group deadline.' },
    ];
    expect(markup(result)).toContain('Estimated arrival is after the group deadline.');
    expect(markup(result)).toContain('disabled=""');
  });
  it('preserves existing group labels and requires explicit counts and UTC pickup', () => {
    const html = renderToStaticMarkup(
      <AirportCabPanel world={world} onApplied={() => {}} onMutationPending={() => {}} />,
    );
    expect(html).toContain('Test group 1 OTP');
    expect(html).toContain('Pickup date and time (UTC)');
    expect(html).toContain('Suitcases');
    expect(html).toContain('Handbags');
    expect(html).toContain('Get vehicle fares');
    expect(html).not.toContain('Vehicle fare options');
  });
  it('ignores an earlier attempt or previous scenario and detects changed group facts', () => {
    const gate = new AirportCabRequestGate();
    const first = gate.begin(),
      second = gate.begin();
    expect(gate.accepts(first, 'live:A', 'live:A')).toBe(false);
    expect(gate.accepts(second, 'live:A', 'live:B')).toBe(false);
    expect(gate.accepts(second, 'live:A', 'live:A')).toBe(true);
    gate.invalidate();
    expect(gate.accepts(second, 'live:A', 'live:A')).toBe(false);
    expect(airportCabContext({ ...world, version: 8 }, 'G1')).toBe(airportCabContext(world, 'G1'));
    const changed = structuredClone(world);
    changed.groups[0].origin = 'CRA';
    expect(airportCabContext(changed, 'G1')).not.toBe(airportCabContext(world, 'G1'));
    expect(airportCabContext({ ...world, scenario_id: 'new' }, 'G1')).not.toBe(
      airportCabContext(world, 'G1'),
    );
  });
});

describe('explicit requests and exact-key recovery', () => {
  it('does not send on preparing an action, automatically retry, or rewrite frozen quote input', async () => {
    const fetch = vi
      .fn()
      .mockRejectedValueOnce(new Error('lost response'))
      .mockResolvedValueOnce(new Response(JSON.stringify(quote())));
    vi.stubGlobal('fetch', fetch);
    const mutableInput = { ...input };
    const action = airportCabApi.quoteAction(world, mutableInput);
    expect(fetch).not.toHaveBeenCalled();
    mutableInput.handbags = 7;
    let unknown: unknown;
    try {
      await action();
    } catch (cause) {
      unknown = cause;
    }
    expect(unknown).toBeInstanceOf(TransportError);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(await (unknown as TransportError).retry()).toEqual(quote());
    expect(fetch).toHaveBeenCalledTimes(2);
    const initial = JSON.parse(fetch.mock.calls[0][1].body),
      recovered = JSON.parse(fetch.mock.calls[1][1].body);
    expect(initial).toEqual(recovered);
    expect(initial).toMatchObject({ ...input, expected_version: 7 });
    expect(initial.idempotency_key).toBeTruthy();
    expect(fetch.mock.calls[0][1].credentials).toBe('same-origin');
  });
  it('recovers an uncertain import with its original option and request ID', async () => {
    const fetch = vi
      .fn()
      .mockRejectedValueOnce(new Error('lost'))
      .mockResolvedValueOnce(new Response(JSON.stringify({ applied: true, world })));
    vi.stubGlobal('fetch', fetch);
    const action = airportCabApi.importAction(world, 'quote/1', 'CAR-1');
    let unknown: unknown;
    try {
      await action();
    } catch (cause) {
      unknown = cause;
    }
    expect(unknown).toBeInstanceOf(TransportError);
    await (unknown as TransportError).retry();
    expect(fetch.mock.calls[0][0]).toBe('/api/airport-transfers/quotes/quote%2F1/import');
    expect(fetch.mock.calls[0][1].body).toBe(fetch.mock.calls[1][1].body);
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toMatchObject({
      option_id: 'CAR-1',
      expected_version: 7,
    });
  });
  it('keeps processing searches recoverable but makes terminal unknown explicit without resending', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            applied: null,
            detail: 'Still processing',
            details: { in_progress: true },
          }),
          { status: 503 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            applied: null,
            detail: 'Source outcome is unrecoverable',
            details: { unknown_outcome: true, in_progress: false },
          }),
          { status: 503 },
        ),
      );
    vi.stubGlobal('fetch', fetch);
    let unknown: unknown;
    try {
      await airportCabApi.quoteAction(world, input)();
    } catch (cause) {
      unknown = cause;
    }
    expect(unknown).toBeInstanceOf(TransportError);
    await expect((unknown as TransportError).retry()).rejects.toBeInstanceOf(
      SearchOutcomeUnknownError,
    );
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(fetch.mock.calls[0][1].body).toBe(fetch.mock.calls[1][1].body);
  });
  it('surfaces definitive provider failure without automatic retry', async () => {
    const fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ applied: false, detail: 'Source unavailable' }), {
        status: 503,
      }),
    );
    vi.stubGlobal('fetch', fetch);
    await expect(airportCabApi.quoteAction(world, input)()).rejects.toBeInstanceOf(ApiError);
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});

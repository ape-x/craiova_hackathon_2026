import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import PlanningTransfers, { PlanningTransferResults } from './PlanningTransfers';
import { planningQuoteCurrent, planningTransferInput } from './planningTransferData';
import type { PlanningTransferForm, PlanningTransferQuote } from './planningTransferData';
import { api, ApiError, SearchOutcomeUnknownError, TransportError } from './api';
import type { World } from './types';

const now = Date.parse('2026-11-01T12:00:00Z');
const world = {
  scenario_id: 'ground-scope',
  version: 7,
  epoch: '2026-11-01T00:00:00Z',
  mode: 'live',
  groups: [
    {
      id: 'G1',
      name: 'Existing group',
      size: 2,
      origin: 'LHR',
      destination: 'OTP',
      ready_at: 1000,
      deadline: 4000,
      status: 'affected',
    },
  ],
  airports: [
    { id: 'HP-1', kind: 'hotel', name: 'Supplier property', lat: 51.48, lon: -0.49 },
    { id: 'LHR', kind: 'airport', name: 'Airport' },
  ],
  offers: [],
  resources: [],
} as unknown as World;
const form: PlanningTransferForm = {
  group_id: 'G1',
  hotel_node_id: 'HP-1',
  pickup_at: '2026-11-02T14:00',
  suitcases: '2',
  label: 'Reported pickup point',
  latitude: '51.47',
  longitude: '-0.48',
  confirmed: true,
};
const quote: PlanningTransferQuote = {
  quote_id: 'Q-1',
  state: 'ready',
  group_id: 'G1',
  hotel_node_id: 'HP-1',
  source_version: 7,
  created_at: '2026-11-01T12:00:00Z',
  usable_until: '2026-11-01T12:02:00Z',
  replayed: false,
  initial_point: {
    id: 'MP-1',
    label: 'Reported pickup point',
    latitude: 51.47,
    longitude: -0.48,
    source: 'operator_declared',
    confirmed: true,
    airport_context_id: 'LHR',
  },
  hotel: {
    node_id: 'HP-1',
    hotel_id: 'supplier-hotel',
    name: 'Supplier property',
    address: 'Source property address',
    city: 'London',
    country: 'GB',
    latitude: 51.48,
    longitude: -0.49,
  },
  transfer: {
    provider: 'APL',
    source_url: 'https://www.airport-pickups-london.com/',
    received_at: '2026-11-01T12:00:00Z',
    source_quoted_at: null,
    expires_at: null,
    request: {
      origin: 'Reported pickup point',
      destination: 'Source property address',
      pickup_at: '2026-11-02T14:00:00Z',
      passengers: 2,
      suitcases: 2,
      transfer_date: '2026-11-02',
      transfer_time: '14:00',
      timezone: 'Europe/London',
    },
    resolved_route: {
      pickup: 'Heathrow',
      dropoff: 'Source property address',
      hub: 'LHR',
      direction: 'from_hub',
      resolved_address: 'Source property address',
      pickup_terminal: null,
    },
    options: [
      {
        id: 'OPTION-1',
        car_type: 'Saloon',
        price: { amount: '76', currency: 'GBP' },
        price_scope: 'per_vehicle',
        max_passengers: 3,
        max_suitcases: 2,
        fits_request: true,
        duration_seconds: null,
        arrival_at: null,
        availability_status: 'quoted_not_reserved',
        accessibility_confirmed: false,
        eur_reference_estimate: {
          amount: '91.15',
          currency: 'EUR',
          kind: 'ecb_reference_estimate',
          rounding: 'half_even_to_cent',
        },
      },
    ],
    fx: {
      state: 'ready',
      source: 'European Central Bank',
      source_url: 'https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml',
      reference: {
        source: 'European Central Bank',
        source_url: 'https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml',
        base_currency: 'EUR',
        rate_date: '2026-10-30',
        retrieved_at: '2026-11-01T12:00:00Z',
        as_of: '2026-11-01T12:00:00Z',
        age_calendar_days: 2,
        age_timezone: 'Europe/Berlin',
        max_age_days: 3,
        source_units_per_eur: '0.83380',
      },
      cache: 'fresh',
      settlement_guaranteed: false,
      revalidation_required: true,
      message: 'Reference estimate only',
    },
    provider_duration_text: null,
    message: null,
  },
  route: {
    source: 'Google Routes',
    source_url: 'https://developers.google.com/maps/documentation/routes/',
    fetched_at: '2026-11-01T12:00:00Z',
    request: {
      origin: { latitude: 51.47, longitude: -0.48 },
      destination: { latitude: 51.48, longitude: -0.49 },
      departure_time: '2026-11-02T14:00:00Z',
      travel_mode: 'DRIVE',
      routing_preference: 'TRAFFIC_AWARE',
    },
    duration_seconds: '1638.3',
    distance_meters: 20000,
    duration_guaranteed: false,
    road_endpoints: {
      origin: { latitude: 51.47, longitude: -0.48 },
      destination: { latitude: 51.48, longitude: -0.49 },
    },
    warnings: [],
  },
  blockers: [],
  options: [{ option_id: 'OPTION-1', importable: true, blockers: [] }],
};
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});
const results = (value = quote, time = now, imported = false) =>
  renderToStaticMarkup(
    <PlanningTransferResults
      result={value}
      now={time}
      pending={false}
      importedOptionId={imported ? 'OPTION-1' : null}
      onImport={() => {}}
    />,
  );

describe('scoped private-transfer planning UI', () => {
  it('marks only the imported option as added, keeping alternative vehicle quotes unselected', () => {
    const second = { ...quote.transfer!.options[0], id: 'OPTION-2', car_type: 'Estate' };
    const value = {
      ...quote,
      transfer: { ...quote.transfer!, options: [...quote.transfer!.options, second] },
      options: [...quote.options, { option_id: 'OPTION-2', importable: true, blockers: [] }],
    };
    const html = renderToStaticMarkup(
      <PlanningTransferResults
        result={value}
        now={now}
        pending={false}
        importedOptionId="OPTION-2"
        onImport={() => {}}
      />,
    );
    expect(html.match(/Planning leg added/g)).toHaveLength(1);
    expect(html.match(/Not selected/g)).toHaveLength(1);
    expect(html.match(/disabled=""/g)).toHaveLength(2);
    expect(html.indexOf('Planning leg added')).toBeGreaterThan(html.indexOf('Estate'));
  });

  it('renders a blocked metadata response with no supplier or route result', () => {
    const html = results({
      ...quote,
      state: 'blocked',
      hotel: null,
      transfer: null,
      route: null,
      options: [],
      blockers: [
        {
          code: 'property_address_missing',
          message: 'No sourced property address; no APL or route request was sent.',
        },
      ],
    });
    expect(html).toContain('Planning quote blocked');
    expect(html).toContain('No sourced property address; no APL or route request was sent.');
    expect(html).not.toContain('Add planning leg');
  });

  it('exposes a terminal unknown search as a separate explicit-attempt decision, never automatically redispatching', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            detail: 'Earlier source dispatch has an unknown outcome and cannot be recovered.',
            details: { unknown_outcome: true },
            applied: null,
          }),
          { status: 503 },
        ),
      )
      .mockResolvedValueOnce(new Response(JSON.stringify(quote)));
    vi.stubGlobal('fetch', fetch);
    const input = planningTransferInput(form, world, now);
    await expect(api.planningTransferQuote(world, input)).rejects.toBeInstanceOf(
      SearchOutcomeUnknownError,
    );
    expect(fetch).toHaveBeenCalledTimes(1);
    // Only a later explicit API action creates a new search/key.
    await api.planningTransferQuote(world, input);
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(JSON.parse(fetch.mock.calls[1][1].body).idempotency_key).not.toBe(
      JSON.parse(fetch.mock.calls[0][1].body).idempotency_key,
    );
  });

  it('keeps an in-progress search retryable with its original idempotency key', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            detail: 'Still processing',
            details: { in_progress: true, retry_after_seconds: 2 },
            applied: null,
          }),
          { status: 503 },
        ),
      )
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...quote, replayed: true })));
    vi.stubGlobal('fetch', fetch);
    let error: unknown;
    try {
      await api.planningTransferQuote(world, planningTransferInput(form, world, now));
    } catch (cause) {
      error = cause;
    }
    expect(error).toBeInstanceOf(TransportError);
    await (error as TransportError).retry();
    expect(fetch.mock.calls[1][1].body).toBe(fetch.mock.calls[0][1].body);
  });

  it('never unlocks an uncertain import based on a search-specific unknown-outcome flag', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: 'Outcome unknown',
            details: { unknown_outcome: true },
            applied: null,
          }),
          { status: 503 },
        ),
      ),
    );
    await expect(api.importPlanningTransfer(world, 'Q-1', 'OPTION-1')).rejects.toBeInstanceOf(
      TransportError,
    );
  });

  it('sends only declared inputs and server identities, never money, passenger counts or route durations', () => {
    const input = planningTransferInput(form, world, now);
    expect(input).toEqual({
      group_id: 'G1',
      hotel_node_id: 'HP-1',
      pickup_at: '2026-11-02T14:00:00.000Z',
      suitcases: 2,
      initial_point: {
        label: 'Reported pickup point',
        latitude: 51.47,
        longitude: -0.48,
        confirmed: true,
      },
    });
    expect(input).not.toHaveProperty('passengers');
    expect(input).not.toHaveProperty('price');
    expect(input).not.toHaveProperty('duration_seconds');
    expect(world.groups[0].origin).toBe('LHR');
    expect(world.groups[0].destination).toBe('OTP');
  });

  it('requires an explicit meeting point and luggage count and refuses airports as hotel destinations', () => {
    for (const change of [
      { confirmed: false },
      { latitude: '' },
      { longitude: '181' },
      { suitcases: '' },
      { suitcases: '1.5' },
      { hotel_node_id: 'LHR' },
      { pickup_at: '2026-11-01T13:00' },
      { pickup_at: '2026-11-05T14:00' },
      { label: 'Gate\nside' },
    ])
      expect(() => planningTransferInput({ ...form, ...change }, world, now)).toThrow();
    expect(planningTransferInput({ ...form, suitcases: '0' }, world, now).suitcases).toBe(0);
  });

  it('does not overwrite confirmed travel or require a fresh room price to identify a property', () => {
    expect(() =>
      planningTransferInput(
        form,
        { ...world, offers: [{ group_id: 'G1', status: 'confirmed' }] as World['offers'] },
        now,
      ),
    ).toThrow(/recorded bookings/);
    expect(planningTransferInput(form, { ...world, resources: [] }, now).hotel_node_id).toBe(
      'HP-1',
    );
  });

  it('separates source GBP, estimated EUR, driving prediction and declared pickup in the result', () => {
    const html = results();
    expect(html).toContain('£76.00 per vehicle');
    expect(html).toContain('Approximately €91.15');
    expect(html).toContain('rate');
    expect(html).toContain('2026-10-30');
    expect(html).toContain('27.3 minutes predicted driving');
    expect(html).toContain('02 Nov 2026, 14:00:00 UTC');
    expect(html).toContain('Source property address');
    expect(html).toContain('reported by operations');
    expect(html).toContain('Supplier quote time and expiry are unknown');
    expect(html).toContain('does not book a vehicle');
    expect(html).not.toContain('disabled=""');
  });

  it('disables expired, blocked, malformed-time and already-imported quotes without hiding source prices', () => {
    expect(planningQuoteCurrent(quote, now + 120000)).toBe(false);
    expect(planningQuoteCurrent({ ...quote, usable_until: '2026-11-01T12:02:00' }, now)).toBe(
      false,
    );
    for (const html of [
      results(quote, now + 120000),
      results({
        ...quote,
        state: 'blocked',
        blockers: [
          {
            code: 'endpoint_mismatch',
            message: 'Supplier destination differs from the sourced hotel.',
          },
        ],
      }),
      results(quote, now, true),
    ]) {
      expect(html).toContain('disabled=""');
      expect(html).toContain('£76.00 per vehicle');
    }
    expect(results(quote, now, true)).toContain('Planning leg added');
    expect(results(quote, now + 120000)).toContain('Start a new explicit search');
  });

  it('exposes the required inputs with labels and leaves meeting point, suitcase count and confirmation empty', () => {
    const html = renderToStaticMarkup(
      <PlanningTransfers world={world} onApplied={() => {}} onMutationPending={() => {}} />,
    );
    expect(html).toContain('Passenger group');
    expect(html).toContain('Supplier hotel property');
    expect(html).toContain('Pickup (UTC)');
    expect(html).toContain('Initial meeting point');
    expect(html).toContain('Latitude');
    expect(html).toContain('Longitude');
    expect(html).toContain('I confirm this group will be ready');
    expect(html).not.toContain('checked=""');
    expect(html).not.toContain('value="51.47"');
  });

  it('recovers the same billed search after an unknown outcome and then exposes definitive conflict', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response('upstream interrupted', { status: 502 }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: 'Group changed; refresh first', applied: false }), {
          status: 409,
        }),
      );
    vi.stubGlobal('fetch', fetch);
    const input = planningTransferInput(form, world, now);
    let failure: unknown;
    try {
      await api.planningTransferQuote(world, input);
    } catch (cause) {
      failure = cause;
    }
    expect(failure).toBeInstanceOf(TransportError);
    input.initial_point.label = 'Edited after dispatch';
    await expect((failure as TransportError).retry()).rejects.toBeInstanceOf(ApiError);
    expect(fetch.mock.calls[1][1].body).toBe(fetch.mock.calls[0][1].body);
    expect(JSON.parse(fetch.mock.calls[1][1].body).initial_point.label).toBe(
      'Reported pickup point',
    );
    expect(JSON.parse(fetch.mock.calls[1][1].body).expected_version).toBe(7);
  });

  it('imports only the server-held option with the agreed estimate policy and retries without duplicating its request ID', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response('{', { status: 200 }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ world: { version: 8 }, applied: true, replayed: true })),
      );
    vi.stubGlobal('fetch', fetch);
    let failure: unknown;
    try {
      await api.importPlanningTransfer(world, 'quote/opaque', 'OPTION-1');
    } catch (cause) {
      failure = cause;
    }
    expect(failure).toBeInstanceOf(TransportError);
    await (failure as TransportError).retry();
    expect(fetch.mock.calls[0][0]).toBe('/api/transfers/planning-quotes/quote%2Fopaque/import');
    expect(fetch.mock.calls[1][1].body).toBe(fetch.mock.calls[0][1].body);
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({
      option_id: 'OPTION-1',
      policy_id: 'ecb_reference_v1',
      expected_version: 7,
      idempotency_key: expect.any(String),
    });
  });
});

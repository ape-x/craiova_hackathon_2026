import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import HotelRecoveryFields from './HotelRecoveryFields';
import {
  emptyHotelRecoveryForm,
  hotelRecoveryGroupIssue,
  hotelRecoveryInput,
  hotelRecoveryTimeline,
  savedHotelRecoveryInputs,
} from './hotelRecovery';
import type { HotelRecoveryForm } from './hotelRecovery';
import { api, TransportError } from './api';
import type { PassengerGroup, RecoveryJob, RecoveryJobResponse, World } from './types';
import { RecoveryJobDetails } from './RecoveryJobPanel';

const now = Date.parse('2026-11-01T12:00:00Z');
const world = {
  mode: 'live',
  version: 8,
  epoch: '2026-11-01T00:00:00Z',
  groups: [
    {
      id: 'G1',
      name: 'Explicit adult party',
      size: 2,
      adult_count: 2,
      origin: 'LHR',
      destination: 'OTP',
      ready_at: 60,
      deadline: 6000,
      status: 'affected',
      accessible: false,
    },
  ],
  airports: [{ id: 'LHR', kind: 'airport', name: 'Heathrow' }],
  offers: [],
} as unknown as World;
const form: HotelRecoveryForm = {
  pickup_at: '2026-11-02T14:00',
  checkin_at: '2026-11-02T15:00',
  checkout_at: '2026-11-03T11:00',
  rooms: '1',
  minimum_rest_minutes: '480',
  suitcases: '2',
  onward_airport_code: 'lhr',
  airport_processing_minutes: '120',
  initial_point: {
    label: 'Reported arrival meeting point',
    latitude: '51.47',
    longitude: '-0.48',
    confirmed: true,
  },
  airport_dropoff: {
    label: 'Reported departure drop-off',
    latitude: '51.471',
    longitude: '-0.481',
    confirmed: true,
  },
};
const makeInput = (
  next: Partial<HotelRecoveryForm> = {},
  groupIds = ['G1'],
  nextWorld = world,
  limit = 10,
) => hotelRecoveryInput({ ...form, ...next }, groupIds, nextWorld, limit, now);
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('explicit hotel recovery inputs', () => {
  it('requires the operator’s literal call cap and exactly one explicitly all-adult party', () => {
    expect(() => makeInput({}, ['G1'], world, 8)).toThrow('has not been increased');
    expect(() => makeInput({}, ['G1', 'G2'])).toThrow('exactly one');
    for (const changes of [
      { adult_count: null },
      { adult_count: 1 },
      { accessible: true },
      { status: 'confirmed' as const },
    ]) {
      const nextWorld = { ...world, groups: [{ ...world.groups[0], ...changes }] };
      expect(hotelRecoveryGroupIssue(nextWorld.groups[0], nextWorld)).not.toBeNull();
      expect(() => makeInput({}, ['G1'], nextWorld)).toThrow();
    }
    expect(makeInput().minimum_rest_minutes).toBe(480);
  });
  it('keeps reported points separate, sends UTC instants, and adds no invented supplier data', () => {
    const input = makeInput();
    expect(input).toEqual({
      pickup_at: '2026-11-02T14:00:00.000Z',
      checkin_at: '2026-11-02T15:00:00.000Z',
      checkout_at: '2026-11-03T11:00:00.000Z',
      rooms: 1,
      minimum_rest_minutes: 480,
      suitcases: 2,
      onward_airport_code: 'LHR',
      airport_processing_minutes: 120,
      initial_point: {
        label: form.initial_point.label,
        latitude: 51.47,
        longitude: -0.48,
        confirmed: true,
      },
      airport_dropoff: {
        label: form.airport_dropoff.label,
        latitude: 51.471,
        longitude: -0.481,
        confirmed: true,
      },
    });
    expect(input).not.toHaveProperty('hotel_id');
    expect(input).not.toHaveProperty('return_pickup_at');
    expect(input).not.toHaveProperty('price');
    expect(input).not.toHaveProperty('departure_terminal');
    expect(makeInput({ onward_airport_code: 'LGW' }).onward_airport_code).toBe('LGW'); // Server catalog can know an airport absent from this World.
  });
  it('rejects blank numeric inputs and unconfirmed/malformed points while allowing explicit zero baggage/rest', () => {
    for (const key of [
      'rooms',
      'minimum_rest_minutes',
      'suitcases',
      'airport_processing_minutes',
    ] as const)
      expect(() => makeInput({ [key]: '' })).toThrow();
    expect(makeInput({ suitcases: '0', minimum_rest_minutes: '0' })).toMatchObject({
      suitcases: 0,
      minimum_rest_minutes: 0,
    });
    expect(() => makeInput({ rooms: '3' })).toThrow();
    expect(() => makeInput({ airport_processing_minutes: '1.5' })).toThrow();
    expect(() =>
      makeInput({ airport_dropoff: { ...form.airport_dropoff, confirmed: false } }),
    ).toThrow('Confirm');
    expect(() => makeInput({ initial_point: { ...form.initial_point, latitude: '' } })).toThrow(
      'latitude',
    );
    expect(() =>
      makeInput({ initial_point: { ...form.initial_point, longitude: 'Infinity' } }),
    ).toThrow('longitude');
  });
  it('checks actual elapsed rest, readiness, ordered dates and deadline without inventing driving time', () => {
    expect(() => makeInput({ minimum_rest_minutes: '1201' })).toThrow('exceed');
    expect(() => makeInput({ pickup_at: '2026-11-02T15:00' })).toThrow('Pickup must precede');
    expect(() => makeInput({ checkin_at: '2026-02-30T15:00' })).toThrow('valid UTC');
    expect(() => makeInput({ pickup_at: '2026-11-01T11:00' })).toThrow('future');
    expect(() => makeInput({ pickup_at: '2026-11-02T14:00:30' })).toThrow('to the minute');
    expect(() =>
      makeInput({ checkout_at: '2026-11-02T20:00', minimum_rest_minutes: '120' }),
    ).toThrow('later Europe/London');
    expect(() =>
      makeInput({}, ['G1'], { ...world, groups: [{ ...world.groups[0], ready_at: 2500 }] }),
    ).toThrow('readiness');
    expect(() =>
      makeInput({}, ['G1'], { ...world, groups: [{ ...world.groups[0], deadline: 3545 }] }),
    ).toThrow('deadline');
    expect(() =>
      makeInput({}, ['G1'], { ...world, groups: [{ ...world.groups[0], deadline: 3600 }] }),
    ).toThrow('airport buffer');
  });
  it('uses the London checkout date across DST and keeps the return allowance in elapsed minutes', () => {
    expect(hotelRecoveryTimeline('2027-03-28T23:30')).toEqual({
      returnPickup: '2027-03-28T23:40:00.000Z',
      flightQueryDate: '2027-03-29',
    });
    expect(hotelRecoveryTimeline('2027-10-31T00:55')).toEqual({
      returnPickup: '2027-10-31T01:05:00.000Z',
      flightQueryDate: '2027-10-31',
    });
    expect(hotelRecoveryTimeline('2027-02-30T10:00')).toBeNull();
    const blank = emptyHotelRecoveryForm();
    expect(blank.rooms).toBe('');
    expect(blank.initial_point.confirmed).toBe(false);
    expect(blank.airport_dropoff.confirmed).toBe(false);
  });
  it('recovers an unknown start with the exact nested inputs, group scope and original call limit', async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(new Response('gateway interruption', { status: 502 }))
      .mockResolvedValueOnce(Response.json({ job: { id: 'JOB1' }, replayed: true }));
    vi.stubGlobal('fetch', fetcher);
    const input = {
      group_ids: ['G1'],
      objective: 'balanced' as const,
      modes: ['flight', 'hotel', 'bus'] as const,
      max_provider_calls: 10,
      instructions: '',
      hotel_recovery: makeInput(),
    };
    let unknown: TransportError | null = null;
    try {
      await api.startRecoveryJob(world, 'CASE1', { ...input, modes: [...input.modes] });
    } catch (cause) {
      expect(cause).toBeInstanceOf(TransportError);
      unknown = cause as TransportError;
    }
    input.hotel_recovery.initial_point.latitude = 0;
    input.max_provider_calls = 26;
    await unknown!.retry<RecoveryJobResponse>();
    expect(fetcher).toHaveBeenCalledTimes(2);
    const first = fetcher.mock.calls[0][1].body;
    expect(fetcher.mock.calls[1][1].body).toBe(first);
    expect(JSON.parse(first)).toMatchObject({
      expected_version: 8,
      group_ids: ['G1'],
      modes: ['flight', 'hotel', 'bus'],
      max_provider_calls: 10,
      hotel_recovery: { initial_point: { latitude: 51.47 } },
    });
  });
});

describe('hotel recovery presentation', () => {
  it('labels both directions, UTC inputs, declared points and the bounded source search without booking claims', () => {
    const html = renderToStaticMarkup(
      <HotelRecoveryFields value={form} onChange={() => {}} group={world.groups[0]} />,
    );
    for (const text of [
      'Initial airport pickup',
      'Required hotel stay',
      'Return to an onward flight',
      'Initial pickup (UTC)',
      'Required check-in (UTC)',
      'Required checkout (UTC)',
      'Minimum rest (minutes)',
      'Operator airport buffer (minutes)',
      'Departure terminal is unknown',
      'not airline guidance',
      'Checkout + 10 minutes',
      'one departure-date query',
      'ECB reference-estimate',
      '2026-11-03',
    ])
      expect(html).toContain(text);
    expect(html.match(/type="checkbox"/g)).toHaveLength(2);
    expect(html.match(/aria-describedby=/g)).toHaveLength(2);
    expect(html).toContain('No supplier booking or hold is made');
    expect(html).not.toContain('confirmed room');
  });
  it('does not erase the mandatory hotel requirement when displaying other waiting intervals', () => {
    const job = {
      id: 'JOB1',
      status: 'completed',
      stage: 'finished',
      message: 'A sourced journey was proposed.',
      group_ids: ['G1'],
      calls_dispatched: 10,
      max_provider_calls: 10,
      created_at: '2026-11-01T12:00:00Z',
      updated_at: '2026-11-01T12:01:00Z',
      applied_version: 9,
      steps: [],
      gaps: [],
      result: {
        recovered_passengers: 2,
        exception_passengers: 0,
        offer_ids: [],
        resource_ids: [],
        source_counts: {},
        hotel_recovery: {
          hotel_id: 'PROPERTY1',
          hotel_node_id: 'HP1',
          hotel_shortlist_limit: 1,
          flight_query_date: '2026-11-03',
          return_pickup_at: '2026-11-03T11:10:00Z',
          return_pickup_basis: 'checkout_plus_10_minute_app_allowance',
          required_provider_calls: 10,
        },
        waits: [
          {
            group_id: 'G1',
            location_id: 'LHR',
            from_at: '2026-11-03T12:00:00Z',
            to_at: '2026-11-03T14:00:00Z',
            duration_minutes: 120,
            timezone: 'Europe/London',
            overnight: false,
            accommodation_included: false,
          },
        ],
      },
    } as unknown as RecoveryJob;
    const html = renderToStaticMarkup(<RecoveryJobDetails job={job} world={world} />);
    expect(html).toContain('Hotel stay and both transfer directions');
    expect(html).toContain('outside the requested hotel interval');
    expect(html).not.toContain('no mandatory rest requirement was assumed');
    expect(html).toContain('Proposals are not supplier bookings');
  });
});

function savedGroup(
  profile: unknown = {
    schema_version: 1,
    source: 'operator_requested',
    scope_fingerprint: 'a'.repeat(64),
    inputs: makeInput(),
  },
): PassengerGroup {
  return { ...world.groups[0], provenance: { hotel_recovery_profile: profile } };
}

describe('explicit review of earlier hotel inputs', () => {
  it('copies exact saved instants into UTC form values without advancing dates, reusing confirmations or exposing a call allowance', () => {
    const inputs = {
      ...makeInput(),
      pickup_at: '2026-11-02T16:00:00+02:00',
      checkin_at: '2026-11-02T17:00:00+02:00',
      checkout_at: '2026-11-03T13:00:00+02:00',
    };
    const group = savedGroup({
      schema_version: 1,
      source: 'operator_requested',
      scope_fingerprint: 'a'.repeat(64),
      inputs,
    });
    const before = structuredClone(group);
    vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2030-01-01T00:00:00Z'));
    const saved = savedHotelRecoveryInputs(group);
    expect(saved.state).toBe('ready');
    if (saved.state !== 'ready') throw new Error('Expected saved inputs');
    expect(saved.form).toEqual({
      ...form,
      onward_airport_code: 'LHR',
      initial_point: { ...form.initial_point, confirmed: false },
      airport_dropoff: { ...form.airport_dropoff, confirmed: false },
    });
    expect(saved.form).not.toHaveProperty('max_provider_calls');
    saved.form.initial_point.label = 'Edited in this form';
    expect(group).toEqual(before);
  });

  it('requires fresh point confirmation and the current explicit typed limit when reviewed values are resubmitted', () => {
    const saved = savedHotelRecoveryInputs(savedGroup());
    if (saved.state !== 'ready') throw new Error('Expected saved inputs');
    expect(() => hotelRecoveryInput(saved.form, ['G1'], world, 10, now)).toThrow(
      'Confirm the initial meeting point',
    );
    const reconfirmed = {
      ...saved.form,
      initial_point: { ...saved.form.initial_point, confirmed: true },
      airport_dropoff: { ...saved.form.airport_dropoff, confirmed: true },
    };
    expect(() => hotelRecoveryInput(reconfirmed, ['G1'], world, 8, now)).toThrow(
      'has not been increased',
    );
    expect(hotelRecoveryInput(reconfirmed, ['G1'], world, 10, now)).toEqual(makeInput());
  });

  it('distinguishes missing profiles from malformed, unsupported or expanded profiles', () => {
    expect(savedHotelRecoveryInputs(undefined)).toEqual({ state: 'absent' });
    expect(savedHotelRecoveryInputs(world.groups[0])).toEqual({ state: 'absent' });
    const valid = {
      schema_version: 1,
      source: 'operator_requested',
      scope_fingerprint: 'a'.repeat(64),
      inputs: makeInput(),
    };
    for (const profile of [
      null,
      [],
      { ...valid, schema_version: 2 },
      { ...valid, source: 'supplier' },
      { ...valid, scope_fingerprint: 'A'.repeat(64) },
      { ...valid, job_id: 'JOB1' },
      { ...valid, max_provider_calls: 26 },
      { ...valid, inputs: { ...valid.inputs, max_provider_calls: 26 } },
      { ...valid, inputs: { ...valid.inputs, rooms: '1' } },
      { ...valid, inputs: { ...valid.inputs, rooms: undefined } },
    ]) {
      expect(savedHotelRecoveryInputs(savedGroup(profile))).toEqual({ state: 'unavailable' });
    }
  });

  it('rejects malformed instants, inconsistent intervals and unverified points rather than guessing replacements', () => {
    const valid = {
      schema_version: 1,
      source: 'operator_requested',
      scope_fingerprint: 'a'.repeat(64),
      inputs: makeInput(),
    };
    for (const change of [
      { pickup_at: '2026-11-02T14:00' },
      { pickup_at: '2026-11-02T14:00:01Z' },
      { pickup_at: '2026-11-02T14:00:00.000001Z' },
      { checkin_at: '2026-02-30T15:00:00Z' },
      { checkout_at: '2026-11-02T14:00:00Z' },
      { minimum_rest_minutes: 1201 },
      { initial_point: { ...valid.inputs.initial_point, confirmed: false } },
      { airport_dropoff: { ...valid.inputs.airport_dropoff, latitude: '51.471' } },
      { airport_dropoff: { ...valid.inputs.airport_dropoff, longitude: Infinity } },
      { airport_dropoff: { ...valid.inputs.airport_dropoff, departure_terminal: 'T5' } },
    ])
      expect(
        savedHotelRecoveryInputs(savedGroup({ ...valid, inputs: { ...valid.inputs, ...change } })),
      ).toEqual({ state: 'unavailable' });
  });

  it('renders an explicit locked review action without replacing edits or issuing requests on render', () => {
    const changed = vi.fn(),
      fetcher = vi.fn();
    vi.stubGlobal('fetch', fetcher);
    const html = renderToStaticMarkup(
      <HotelRecoveryFields
        group={savedGroup()}
        value={{ ...form, pickup_at: '2026-11-04T14:00' }}
        onChange={changed}
        disabled
      />,
    );
    expect(html).toContain('Review saved inputs');
    expect(html).toMatch(/<button[^>]+disabled=""[^>]*>Review saved inputs<\/button>/);
    expect(html).toContain('value="2026-11-04T14:00"');
    expect(html).toContain('replaces the current form values');
    expect(html).toContain('nothing is submitted or searched');
    expect(html).toContain('Dates stay exactly as saved, including past dates');
    expect(html).toContain('minimum 10 provider calls');
    expect(html).toContain('minimum 11 total calls');
    expect(html).toContain('1 interpretation + 10 recovery-job calls');
    expect(changed).not.toHaveBeenCalled();
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('gives a clear reconfigure state for invalid profiles without a working prefill action', () => {
    const html = renderToStaticMarkup(
      <HotelRecoveryFields group={savedGroup(null)} value={form} onChange={() => {}} />,
    );
    expect(html).toContain('Saved inputs are unavailable or invalid');
    expect(html).toContain('Reconfigure this group');
    expect(html).not.toContain('Review saved inputs');
  });
});

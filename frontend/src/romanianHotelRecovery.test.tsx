import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import RomanianHotelRecoveryFields from './RomanianHotelRecoveryFields';
import {
  emptyRomanianHotelRecoveryForm,
  romanianHotelGroupIssue,
  romanianHotelRecoveryInput,
  romanianHotelScope,
} from './romanianHotelRecovery';
import { RecoveryJobDetails } from './RecoveryJobPanel';
import { cloneRecoveryFormDraft } from './recoveryFormDraft';
import { emptyHotelRecoveryForm } from './hotelRecovery';
import { api, TransportError } from './api';
import type { RecoveryJob, RecoveryJobResponse, World } from './types';

const now = Date.parse('2026-11-01T12:00:00Z');
const fixture = () =>
  ({
    mode: 'live',
    scenario_id: 'OPS',
    version: 8,
    epoch: '2026-11-01T00:00:00Z',
    groups: [
      {
        id: 'G1',
        name: 'Adult party',
        size: 2,
        adult_count: 2,
        origin: 'OTP',
        destination: 'LHR',
        ready_at: 720,
        deadline: 6000,
        status: 'waiting',
        accessible: false,
        incident_id: 'CASE1',
        original_flight_id: 'W43001',
      },
    ],
    airports: ['OTP', 'LHR'].map((id) => ({ id, code: id, kind: 'airport', name: id })),
    incidents: [{ id: 'CASE1', group_ids: ['G1'] }],
    resources: [],
    offers: [],
    reservations: [],
  }) as unknown as World;
const formFor = (world = fixture()) => ({
  scope: romanianHotelScope(world, world.groups[0]),
  checkin_at: '2026-11-02T13:00',
  checkout_at: '2026-11-03T04:00',
  rooms: '1',
  minimum_rest_minutes: '480',
  suitcases: '2',
  handbags: '0',
  standard_baggage_confirmed: true,
  airport_processing_minutes: '180',
  hotel_connection_minutes: '20',
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('explicit Romania hotel package', () => {
  it('serializes exact UTC stays, explicit zero bags and the separate provider choice without moving readiness or raising the cap', () => {
    const world = fixture(),
      before = structuredClone(world);
    const input = romanianHotelRecoveryInput(formFor(world), ['G1'], world, 8, now);
    expect(input).toEqual({
      source: 'transfero_direct_aeroport',
      hotel_id: 'lpce923',
      checkin_at: '2026-11-02T13:00:00.000Z',
      checkout_at: '2026-11-03T04:00:00.000Z',
      rooms: 1,
      minimum_rest_minutes: 480,
      suitcases: 2,
      handbags: 0,
      standard_baggage_confirmed: true,
      airport_processing_minutes: 180,
      hotel_connection_minutes: 20,
    });
    expect(world).toEqual(before);
    expect(() => romanianHotelRecoveryInput(formFor(world), ['G1'], world, 7, now)).toThrow('8');
    expect(() => romanianHotelRecoveryInput(formFor(world), ['G1', 'G2'], world, 8, now)).toThrow(
      'exactly one',
    );
    expect(emptyRomanianHotelRecoveryForm()).toMatchObject({
      suitcases: '',
      handbags: '',
      checkin_at: '',
      standard_baggage_confirmed: false,
    });
  });
  it('rejects stale party/readiness scopes, unsupported adult/location scope and unresolved arrival before any search', () => {
    for (const change of [
      { origin: 'LHR' },
      { adult_count: null },
      { size: 13, adult_count: 13 },
      { accessible: true },
      { status: 'confirmed' as const },
      { incident_id: 'OTHER' },
    ]) {
      const world = fixture();
      Object.assign(world.groups[0], change);
      expect(romanianHotelGroupIssue(world.groups[0], world)).not.toBeNull();
    }
    const world = fixture(),
      old = formFor(world);
    world.groups[0].ready_at = world.groups[0].ready_at! + 1;
    expect(() => romanianHotelRecoveryInput(old, ['G1'], world, 8, now)).toThrow('changed');
    world.resources = [{ id: 'W43001', status: 'needs_diversion' }] as World['resources'];
    expect(romanianHotelGroupIssue(world.groups[0], world)).toContain('actual airport location');
  });
  it('requires explicit baggage and margins, property-local dates and enough declared stay/rest/deadline', () => {
    for (const change of [
      { handbags: '' },
      { suitcases: '3' },
      { standard_baggage_confirmed: false },
      { rooms: '3' },
      { rooms: '1.5' },
      { hotel_connection_minutes: '19' },
      { airport_processing_minutes: '179' },
      { minimum_rest_minutes: '9999' },
      { checkin_at: '2026-11-02T22:30', checkout_at: '2026-11-03T06:00' },
      { checkout_at: '2026-11-08T14:00' },
    ]) {
      const world = fixture();
      expect(() =>
        romanianHotelRecoveryInput({ ...formFor(world), ...change }, ['G1'], world, 8, now),
      ).toThrow();
    }
  });
  it('shows source boundaries, blank explicit inputs and changed-scope reset without loading UK saved fields', () => {
    const world = fixture();
    const html = renderToStaticMarkup(
      <RomanianHotelRecoveryFields
        value={emptyRomanianHotelRecoveryForm()}
        onChange={() => {}}
        world={world}
        group={world.groups[0]}
        allowance={8}
      />,
    );
    for (const text of [
      'Direct Aeroport',
      'Hotel Aro Palace',
      'UTC input',
      '12 hours',
      '20 minutes',
      '3 hours',
      '25 kg',
      '10 kg',
      'no seats, rooms or vehicles are reserved',
      'never increased automatically',
    ])
      expect(html).toContain(text);
    expect(html).not.toMatch(/260|114\.48|GBP|per vehicle/);
    const previous = formFor(world);
    world.groups[0].ready_at = world.groups[0].ready_at! + 1;
    const stale = renderToStaticMarkup(
      <RomanianHotelRecoveryFields
        value={previous}
        onChange={() => {}}
        world={world}
        group={world.groups[0]}
        allowance={8}
      />,
    );
    expect(stale).toContain('Reset Romania package inputs');
    expect(stale).toContain('disabled');
    expect(stale).toContain('2026-11-02T13:00');
  });
  it('preserves the complete Romania draft and unknown-outcome replay payload without dispatching a replacement search', async () => {
    const world = fixture();
    const draft = {
      groupIds: ['G1'],
      objective: 'balanced' as const,
      modes: ['flight' as const],
      limit: '8',
      instructions: 'keep exact',
      hotelMode: true,
      hotelPackage: 'romania_transfero' as const,
      romanianHotelFields: formFor(world),
      hotelFields: emptyHotelRecoveryForm(),
      airportTransfers: [],
      afterFlightTransfer: null,
    };
    const saved = cloneRecoveryFormDraft(draft);
    draft.romanianHotelFields.handbags = '1';
    expect(saved.romanianHotelFields?.handbags).toBe('0');
    expect(saved.limit).toBe('8');
    const fetcher = vi
      .fn()
      .mockRejectedValueOnce(new TypeError('lost response'))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ job: { id: 'JOB1' }, replayed: true }), { status: 200 }),
      );
    vi.stubGlobal('fetch', fetcher);
    const input = {
      group_ids: ['G1'],
      objective: 'balanced' as const,
      modes: ['flight', 'hotel', 'bus'] as ('flight' | 'hotel' | 'bus')[],
      max_provider_calls: 8,
      instructions: '',
      romanian_hotel_recovery: romanianHotelRecoveryInput(
        saved.romanianHotelFields!,
        ['G1'],
        world,
        8,
        now,
      ),
    };
    let unknown: TransportError | null = null;
    try {
      await api.startRecoveryJob(world, 'CASE1', input);
    } catch (error) {
      expect(error).toBeInstanceOf(TransportError);
      unknown = error as TransportError;
    }
    input.romanian_hotel_recovery.handbags = 2;
    input.max_provider_calls = 26;
    await unknown!.retry<RecoveryJobResponse>();
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(fetcher.mock.calls[1][1].body).toBe(fetcher.mock.calls[0][1].body);
    const body = JSON.parse(fetcher.mock.calls[0][1].body);
    expect(body).toMatchObject({ max_provider_calls: 8, romanian_hotel_recovery: { handbags: 0 } });
    expect(body).not.toHaveProperty('hotel_recovery');
  });
  it('keeps frozen requested setup visible on blocked jobs without claiming a complete journey', () => {
    const world = fixture();
    const job = {
      id: 'JOB1',
      incident_id: 'CASE1',
      status: 'blocked',
      stage: 'finished',
      message: 'No complete connection',
      group_ids: ['G1'],
      calls_dispatched: 8,
      max_provider_calls: 8,
      created_at: '2026-11-01T12:00:00Z',
      updated_at: '2026-11-01T12:01:00Z',
      steps: [],
      gaps: [],
      romanian_hotel_recovery: romanianHotelRecoveryInput(formFor(world), ['G1'], world, 8, now),
    } as unknown as RecoveryJob;
    const html = renderToStaticMarkup(<RecoveryJobDetails job={job} world={world} />);
    expect(html).toContain('Requested Romania hotel journey');
    expect(html).toContain('not evidence that a complete journey was found or booked');
    expect(html).not.toContain('recorded complete proposal');
  });
});

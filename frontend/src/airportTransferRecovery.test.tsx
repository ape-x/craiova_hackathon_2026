import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import AirportTransferRecoveryFields, {
  AirportTransferRequestDetails,
} from './AirportTransferRecoveryFields';
import {
  airportTransferGroupIssue,
  airportTransferInputs,
  airportTransferScope,
  emptyAirportTransferDraft,
} from './airportTransferRecovery';
import {
  arrivalReportSubmission,
  emptyArrivalRecoveryForm,
  projectedArrivalRecovery,
} from './arrivalRecoveryData';
import { RecoveryJobDetails } from './RecoveryJobPanel';
import { api, TransportError } from './api';
import type { ArrivalReportForm } from './operationalReports';
import type { RecoveryIncident, RecoveryJob, World } from './types';

const now = Date.parse('2030-11-01T12:00:00Z');
function operation(count = 2): World {
  return {
    mode: 'live',
    scenario_id: 'OP1',
    version: 4,
    epoch: '2030-11-01T00:00:00Z',
    groups: Array.from({ length: count }, (_, index) => ({
      id: `G${index + 1}`,
      incident_id: 'CASE1',
      name: `Existing group ${index + 1}`,
      size: 2,
      adult_count: 2,
      origin: 'OTP',
      destination: 'LHR',
      ready_at: 600,
      deadline: 5760,
      accessible: false,
      status: 'affected',
      budget: 1000,
    })),
    offers: [],
    reservations: [],
    resources: [],
    airports: [],
    decisions: [],
  } as unknown as World;
}
function filled(world = operation(), ids = ['G1', 'G2'], groupId = 'G1', scopeKey = '') {
  return {
    ...emptyAirportTransferDraft(groupId, airportTransferScope(world, ids, scopeKey)),
    pickup_at: '2030-11-02T14:00',
    suitcases: '2',
    handbags: '0',
  };
}
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('bounded per-group airport-transfer authorization', () => {
  it('starts with unknown pickup and baggage, and preserves explicit zero without adding other selected groups', () => {
    const world = operation(),
      blank = emptyAirportTransferDraft('G1', airportTransferScope(world, ['G1', 'G2']));
    expect(blank).toMatchObject({ pickup_at: '', suitcases: '', handbags: '' });
    expect(() =>
      airportTransferInputs([blank], world, ['G1', 'G2'], ['bus'], 8, false, '', now),
    ).toThrow('Unknown baggage is not zero');
    const result = airportTransferInputs(
      [filled(world)],
      world,
      ['G1', 'G2'],
      ['flight', 'bus'],
      8,
      false,
      '',
      now,
    );
    expect(result).toEqual([
      {
        group_id: 'G1',
        destination_airport: 'CRA',
        pickup_at: '2030-11-02T14:00:00.000Z',
        suitcases: 2,
        handbags: 0,
      },
    ]);
    expect(result[0]).not.toHaveProperty('scope');
    expect(world.groups[0].ready_at).toBe(600);
  });
  it.each([
    { suitcases: '' },
    { handbags: '' },
    { suitcases: '8' },
    { handbags: '-1' },
    { suitcases: '1.5' },
    { pickup_at: '' },
    { pickup_at: '2030-11-01T20:59' },
    { pickup_at: '2030-11-05T00:00' },
    { pickup_at: '2030-12-04T14:00' },
  ])('rejects missing or invalid inputs %j before dispatch', (change) => {
    expect(() =>
      airportTransferInputs(
        [{ ...filled(), ...change }],
        operation(),
        ['G1', 'G2'],
        ['bus'],
        8,
        false,
        '',
        now,
      ),
    ).toThrow();
  });
  it('requires ground mode and rejects hotel orchestration without silently removing any inputs', () => {
    const world = operation(),
      draft = filled(world);
    expect(() =>
      airportTransferInputs([draft], world, ['G1', 'G2'], ['flight'], 8, false, '', now),
    ).toThrow('Ground transport');
    expect(() =>
      airportTransferInputs(
        [draft],
        world,
        ['G1', 'G2'],
        ['flight', 'bus', 'hotel'],
        10,
        true,
        '',
        now,
      ),
    ).toThrow('hotel recovery');
    expect(draft.handbags).toBe('0');
  });
  it('rejects duplicate, foreign and excessive groups, and enforces two calls per group plus one decision', () => {
    const world = operation(13),
      ids = world.groups.map((group) => group.id),
      drafts = ids.map((id) => filled(world, ids, id));
    expect(() => airportTransferInputs(drafts, world, ids, ['bus'], 26, false, '', now)).toThrow(
      'at most 12',
    );
    expect(() =>
      airportTransferInputs([drafts[0], drafts[0]], world, ids, ['bus'], 26, false, '', now),
    ).toThrow('different groups');
    expect(() =>
      airportTransferInputs([drafts[1]], world, ['G1'], ['bus'], 8, false, '', now),
    ).toThrow('no longer selected');
    expect(() =>
      airportTransferInputs(drafts.slice(0, 2), world, ids, ['bus'], 4, false, '', now),
    ).toThrow('at least 5');
    expect(
      airportTransferInputs(drafts.slice(0, 12), world, ids, ['bus'], 25, false, '', now),
    ).toHaveLength(12);
  });
  it('invalidates prior setup on selected-group, origin, readiness, scenario or scope changes', () => {
    const world = operation(),
      draft = filled(world);
    for (const update of [
      (next: World) => {
        next.groups[0].origin = 'CRA';
      },
      (next: World) => {
        next.groups[0].ready_at = 700;
      },
      (next: World) => {
        next.scenario_id = 'NEW';
      },
      (next: World) => {
        next.groups[0].adult_count = 1;
      },
    ]) {
      const next = structuredClone(world);
      update(next);
      expect(() =>
        airportTransferInputs([draft], next, ['G1', 'G2'], ['bus'], 8, false, '', now),
      ).toThrow('changed');
    }
    expect(() => airportTransferInputs([draft], world, ['G1'], ['bus'], 8, false, '', now)).toThrow(
      'changed',
    );
    expect(() =>
      airportTransferInputs([draft], world, ['G1', 'G2'], ['bus'], 8, false, 'new arrival', now),
    ).toThrow('changed');
    expect(
      airportTransferInputs(
        [draft],
        { ...world, version: 5 },
        ['G1', 'G2'],
        ['bus'],
        8,
        false,
        '',
        now,
      ),
    ).toHaveLength(1);
  });
  it('excludes unsupported groups and partial recorded bookings', () => {
    const world = operation();
    for (const changes of [
      { origin: 'CRA' },
      { accessible: true },
      { adult_count: null },
      { size: 8, adult_count: 8 },
      { status: 'confirmed' },
    ]) {
      const next = structuredClone(world);
      Object.assign(next.groups[0], changes);
      expect(airportTransferGroupIssue(next, 'G1')).toBeTruthy();
    }
    world.offers = [
      { group_id: 'G1', status: 'proposed', external_bookings: [{ resource_id: 'flight' }] },
    ] as unknown as World['offers'];
    expect(airportTransferGroupIssue(world, 'G1')).toContain('recorded bookings');
  });
});

describe('projected arrival and explicit form presentation', () => {
  const form: ArrivalReportForm = {
    groupIds: ['G1'],
    airportCode: 'OTP',
    readyAt: '2030-11-02T13:00',
    description: 'Passengers reported ready after diversion.',
  };
  const incident = { id: 'CASE1', group_ids: ['G1', 'G2'] } as RecoveryIncident;
  it('uses post-report OTP readiness for the exact reported group without mutating current world', () => {
    vi.useFakeTimers();
    vi.setSystemTime(now);
    const world = operation();
    world.groups[0].origin = 'CRA';
    const before = structuredClone(world),
      projected = projectedArrivalRecovery(world, form)!;
    const draft = filled(projected.world, form.groupIds, 'G1', projected.scopeKey);
    const recovery = {
      ...emptyArrivalRecoveryForm(),
      modes: ['flight', 'bus'] as ('flight' | 'bus')[],
      airportTransfers: [draft],
    };
    const input = arrivalReportSubmission(world, incident, form, recovery, 'recover');
    expect(input.group_ids).toEqual(['G1']);
    expect(input.recovery?.airport_transfers).toEqual([
      {
        group_id: 'G1',
        destination_airport: 'CRA',
        pickup_at: '2030-11-02T14:00:00.000Z',
        suitcases: 2,
        handbags: 0,
      },
    ]);
    expect(input.recovery?.max_provider_calls).toBe(8);
    expect(world).toEqual(before);
    expect(() =>
      arrivalReportSubmission(
        world,
        incident,
        { ...form, readyAt: '2030-11-02T13:30' },
        recovery,
        'recover',
      ),
    ).toThrow('changed');
    expect(() =>
      arrivalReportSubmission(
        world,
        incident,
        { ...form, airportCode: 'LHR' },
        recovery,
        'recover',
      ),
    ).toThrow('changed');
    const updated = structuredClone(world);
    updated.groups[0].ready_at = 750;
    expect(() => arrivalReportSubmission(updated, incident, form, recovery, 'recover')).toThrow(
      'changed',
    );
  });
  it('allows report-only despite incomplete optional transfer setup and keeps legacy payloads unchanged', () => {
    const world = operation();
    const report = arrivalReportSubmission(
      world,
      incident,
      form,
      {
        ...emptyArrivalRecoveryForm(),
        airportTransfers: [emptyAirportTransferDraft('G1', 'obsolete')],
      },
      'report',
    );
    expect(report).not.toHaveProperty('recovery');
    expect(
      arrivalReportSubmission(world, incident, form, emptyArrivalRecoveryForm(), 'recover')
        .recovery,
    ).not.toHaveProperty('airport_transfers');
    expect(projectedArrivalRecovery(world, { ...form, readyAt: '' })).toBeNull();
  });
  it('shows party-total labels, explicit blanks, call cap and no vehicle guarantee', () => {
    vi.useFakeTimers();
    vi.setSystemTime(now);
    const world = operation(),
      blank = emptyAirportTransferDraft('G1', airportTransferScope(world, ['G1', 'G2']));
    const html = renderToStaticMarkup(
      <AirportTransferRecoveryFields
        world={world}
        groupIds={['G1', 'G2']}
        modes={['flight', 'bus']}
        allowance={4}
        value={[blank]}
        onChange={() => {}}
      />,
    );
    for (const label of [
      'Pickup date and time (UTC)',
      'Total suitcases for this group',
      'Total handbags for this group',
      'Unknown baggage is not zero',
      'Minimum 3 calls',
      '5 total for one transfer',
      'Your limit remains 4',
      'fleet availability is unconfirmed',
      'at most one Bucharest.CAB vehicle per plan',
      'complete onward itinerary is still required',
    ])
      expect(html).toContain(label);
    expect(html.match(/value=""/g)).toHaveLength(3);
    const stale = renderToStaticMarkup(
      <AirportTransferRecoveryFields
        world={world}
        groupIds={['G1']}
        modes={['bus']}
        allowance={8}
        value={[blank]}
        onChange={() => {}}
      />,
    );
    expect(stale).toContain('Reset transfer inputs');
    expect(stale).toContain('disabled=""');
  });
  it('shows frozen request details even on a blocked job, without implying dispatch or recovery', () => {
    const world = operation(),
      inputs = airportTransferInputs(
        [filled(world)],
        world,
        ['G1', 'G2'],
        ['bus'],
        8,
        false,
        '',
        now,
      );
    const job = {
      id: 'JOB1',
      incident_id: 'CASE1',
      group_ids: ['G1', 'G2'],
      objective: 'balanced',
      modes: ['bus'],
      max_provider_calls: 8,
      airport_transfers: inputs,
      status: 'blocked',
      stage: 'finished',
      message: 'No complete itinerary',
      created_at: '2030-11-01T12:00:00Z',
      updated_at: '2030-11-01T12:00:10Z',
      calls_dispatched: 2,
      steps: [],
      gaps: [],
      result: null,
    } as unknown as RecoveryJob;
    const html = renderToStaticMarkup(<RecoveryJobDetails job={job} world={world} />);
    expect(html).toContain('Requested Bucharest.CAB searches');
    expect(html).toContain('Frozen request inputs');
    expect(html).toContain('2 suitcases · 0 handbags');
    expect(html).toContain('not availability or a booking');
    expect(renderToStaticMarkup(<AirportTransferRequestDetails inputs={[]} world={world} />)).toBe(
      '',
    );
  });
});

describe('serialized recovery scope and uncertainty', () => {
  it('retries the original job payload with exact luggage, pickup, group scope and unchanged cap', async () => {
    const world = operation(),
      transfers = airportTransferInputs(
        [filled(world)],
        world,
        ['G1', 'G2'],
        ['bus'],
        8,
        false,
        '',
        now,
      );
    const fetch = vi
      .fn()
      .mockRejectedValueOnce(new Error('connection lost'))
      .mockResolvedValueOnce(new Response(JSON.stringify({ job: { id: 'JOB1' }, replayed: true })));
    vi.stubGlobal('fetch', fetch);
    let unknown: unknown;
    try {
      await api.startRecoveryJob(world, 'CASE1', {
        group_ids: ['G1', 'G2'],
        objective: 'balanced',
        modes: ['flight', 'bus'],
        max_provider_calls: 8,
        instructions: '',
        airport_transfers: transfers,
      });
    } catch (cause) {
      unknown = cause;
    }
    expect(unknown).toBeInstanceOf(TransportError);
    expect(fetch).toHaveBeenCalledTimes(1);
    transfers[0].handbags = 7;
    await (unknown as TransportError).retry();
    expect(fetch.mock.calls[0][1].body).toBe(fetch.mock.calls[1][1].body);
    const payload = JSON.parse(fetch.mock.calls[1][1].body);
    expect(payload.airport_transfers[0].handbags).toBe(0);
    expect(payload.group_ids).toEqual(['G1', 'G2']);
    expect(payload.max_provider_calls).toBe(8);
    expect(payload.airport_transfers[0]).not.toHaveProperty('scope');
  });
});

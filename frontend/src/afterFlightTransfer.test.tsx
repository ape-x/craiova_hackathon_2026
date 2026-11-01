import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import {
  afterFlightTransferGroupIssue,
  afterFlightTransferInput,
  emptyAfterFlightTransfer,
} from './afterFlightTransfer';
import AfterFlightTransferFields from './AfterFlightTransferFields';
import { airportTransferScope } from './airportTransferRecovery';
import {
  arrivalReportSubmission,
  emptyArrivalRecoveryForm,
  projectedArrivalRecovery,
} from './arrivalRecoveryData';
import { RecoveryJobDetails } from './RecoveryJobPanel';
import { ArrivalRecoveryFields } from './ArrivalRecoveryFields';
import { api, TransportError } from './api';
import type { RecoveryIncident, RecoveryJob, World } from './types';
import type { ArrivalReportForm } from './operationalReports';

const now = Date.parse('2030-11-01T12:00:00Z');
function operation(): World {
  return {
    mode: 'live',
    scenario_id: 'OP1',
    version: 7,
    epoch: '2030-11-01T00:00:00Z',
    sim_time: 720,
    groups: [
      {
        id: 'G1',
        incident_id: 'CASE1',
        name: 'Inbound party',
        size: 2,
        adult_count: 2,
        origin: 'LHR',
        destination: 'CRA',
        ready_at: 600,
        deadline: 5760,
        accessible: false,
        status: 'affected',
        original_flight_id: 'FL1',
      },
    ],
    airports: ['LHR', 'OTP', 'CRA'].map((id) => ({ id, code: id, kind: 'airport', name: id })),
    offers: [],
    reservations: [],
    resources: [],
    decisions: [],
  } as unknown as World;
}
function filled(world = operation(), ids = ['G1'], extraScope = '') {
  return {
    ...emptyAfterFlightTransfer('G1', airportTransferScope(world, ids, extraScope)),
    pickup_window_start: '2030-11-01T13:00',
    pickup_window_end: '2030-11-02T20:00',
    airport_processing_minutes: '90',
    suitcases: '2',
    handbags: '0',
  };
}
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('explicit future flight arrival and transfer authorization', () => {
  it('keeps times, bags and processing unknown until explicitly entered; never opts in by default', () => {
    const world = operation(),
      blank = emptyAfterFlightTransfer('G1', airportTransferScope(world, ['G1']));
    expect(blank).toMatchObject({
      pickup_window_start: '',
      pickup_window_end: '',
      airport_processing_minutes: '',
      suitcases: '',
      handbags: '',
      max_inbound_options: '2',
    });
    expect(afterFlightTransferInput(null, world, ['G1'], ['flight', 'bus'], 8)).toBeUndefined();
    expect(() =>
      afterFlightTransferInput(blank, world, ['G1'], ['flight', 'bus'], 8, false, '', now),
    ).toThrow('Unknown values are not zero');
  });
  it('serializes exactly one group without changing position, readiness, cap or the candidate upper bound', () => {
    const world = operation(),
      before = structuredClone(world);
    const input = afterFlightTransferInput(
      filled(world),
      world,
      ['G1'],
      ['flight', 'bus'],
      6,
      false,
      '',
      now,
    )!;
    expect(input).toEqual({
      group_id: 'G1',
      via_airport: 'OTP',
      destination_airport: 'CRA',
      pickup_window_start: '2030-11-01T13:00:00.000Z',
      pickup_window_end: '2030-11-02T20:00:00.000Z',
      airport_processing_minutes: 90,
      suitcases: 2,
      handbags: 0,
      max_inbound_options: 2,
    });
    expect(input).not.toHaveProperty('scope');
    expect(input).not.toHaveProperty('pickup_at');
    expect(world).toEqual(before);
    // An early window start is permitted: only the derived source pickup must meet advance rules.
    expect(Date.parse(input.pickup_window_start)).toBeLessThan(now + 9 * 3_600_000);
  });
  it.each([
    { suitcases: '' },
    { handbags: '' },
    { suitcases: '8' },
    { handbags: '-1' },
    { airport_processing_minutes: '' },
    { airport_processing_minutes: '0' },
    { airport_processing_minutes: '721' },
    { airport_processing_minutes: '1.5' },
    { max_inbound_options: '3' },
    { pickup_window_start: '' },
    { pickup_window_end: '' },
    { pickup_window_start: '2030-11-01T11:59' },
    { pickup_window_end: '2030-11-01T12:59' },
    { pickup_window_end: '2030-11-05T00:01' },
  ])('rejects invalid or missing explicit values %j', (change) => {
    const draft = { ...filled(), ...change } as ReturnType<typeof filled>;
    expect(() =>
      afterFlightTransferInput(draft, operation(), ['G1'], ['flight', 'bus'], 8, false, '', now),
    ).toThrow();
  });
  it('bounds the window to seven days even when the group deadline is later', () => {
    const world = operation();
    world.groups[0].deadline = 20_000;
    const draft = { ...filled(world), pickup_window_end: '2030-11-08T12:01' };
    expect(() =>
      afterFlightTransferInput(draft, world, ['G1'], ['flight', 'bus'], 8, false, '', now),
    ).toThrow('seven days');
  });
  it('accepts a window ending at the deadline while leaving actual driving eligibility to the source check', () => {
    const world = operation();
    expect(
      afterFlightTransferInput(
        { ...filled(world), pickup_window_end: '2030-11-05T00:00' },
        world,
        ['G1'],
        ['flight', 'bus'],
        8,
        false,
        '',
        now,
      )?.pickup_window_end,
    ).toBe('2030-11-05T00:00:00.000Z');
  });
  it.each([
    { modes: ['flight'] },
    { modes: ['bus'] },
    { modes: ['flight', 'bus', 'hotel'] },
  ] as const)('does not silently change chosen services %j', ({ modes }) => {
    expect(() =>
      afterFlightTransferInput(filled(), operation(), ['G1'], [...modes], 8, false, '', now),
    ).toThrow();
  });
  it('blocks mixed initial setup and insufficient allowance without dropping inputs', () => {
    expect(() =>
      afterFlightTransferInput(filled(), operation(), ['G1'], ['flight', 'bus'], 8, true, '', now),
    ).toThrow('initial airport-transfer');
    expect(() =>
      afterFlightTransferInput(filled(), operation(), ['G1'], ['flight', 'bus'], 5, false, '', now),
    ).toThrow('at least 6');
  });
  it.each([
    { origin: 'OTP' },
    { origin: 'UNKNOWN' },
    { destination: 'LHR' },
    { adult_count: null },
    { size: 8, adult_count: 8 },
    { accessible: true },
    { status: 'confirmed' },
  ])('blocks ineligible group facts %j', (change) => {
    const world = operation();
    Object.assign(world.groups[0], change);
    expect(afterFlightTransferGroupIssue(world, ['G1'])).not.toBeNull();
  });
  it('requires one selected group and actual reported location before an unresolved diversion', () => {
    const world = operation();
    expect(afterFlightTransferGroupIssue(world, ['G1', 'G2'])).toContain('exactly one');
    world.resources.push({
      id: 'FL1',
      status: 'needs_diversion',
      destination: 'CRA',
    } as World['resources'][number]);
    expect(afterFlightTransferGroupIssue(world, ['G1'])).toContain('arrival is unresolved');
  });
  it('invalidates changed selection, readiness and route while preserving the visible draft', () => {
    const world = operation(),
      draft = filled(world);
    world.groups[0].ready_at = (world.groups[0].ready_at ?? 0) + 1;
    expect(() =>
      afterFlightTransferInput(draft, world, ['G1'], ['flight', 'bus'], 8, false, '', now),
    ).toThrow('Reset');
    const html = renderToStaticMarkup(
      <AfterFlightTransferFields
        world={world}
        groupIds={['G1']}
        modes={['flight', 'bus']}
        allowance={8}
        value={draft}
        onChange={() => {}}
      />,
    );
    expect(html).toContain('Reset after-flight inputs');
    expect(html).toContain('2030-11-01T13:00');
    expect(html).toContain('disabled=""');
  });
  it('shows source limits, uncertain connection and explicit fields without calling or submitting', () => {
    vi.useFakeTimers();
    vi.setSystemTime(now);
    const onChange = vi.fn(),
      world = operation();
    const html = renderToStaticMarkup(
      <AfterFlightTransferFields
        world={world}
        groupIds={['G1']}
        modes={['flight', 'bus']}
        allowance={6}
        value={filled(world)}
        onChange={onChange}
      />,
    );
    expect(html).toContain('Earliest permitted pickup (UTC)');
    expect(html).toContain('Latest permitted pickup (UTC)');
    expect(html).toContain('Arrival processing allowance');
    expect(html).toContain('limit remains 6');
    expect(html).toContain('at most one candidate');
    expect(html).toContain('does not move passengers or report a landing');
    expect(html).toContain('connection protection are unverified');
    expect(html).toContain('pickup is not moved just to meet those rules');
    expect(onChange).not.toHaveBeenCalled();
  });
  it('uses the projected actual arrival for an atomic request and preserves report-only behavior', () => {
    vi.useFakeTimers();
    vi.setSystemTime(now);
    const world = operation();
    world.groups[0].origin = 'CRA';
    world.resources.push({
      id: 'FL1',
      status: 'needs_diversion',
      destination: 'CRA',
    } as World['resources'][number]);
    const form: ArrivalReportForm = {
      groupIds: ['G1'],
      airportCode: 'LHR',
      readyAt: '2030-11-01T12:30',
      description: 'Operator reports readiness at Heathrow.',
    };
    const projected = projectedArrivalRecovery(world, form)!;
    const recovery = {
      ...emptyArrivalRecoveryForm(),
      modes: ['flight', 'bus'] as const as unknown as ('flight' | 'bus')[],
      afterFlightTransfer: filled(projected.world, projected.groupIds, projected.scopeKey),
    };
    const incident = { id: 'CASE1', group_ids: ['G1'] } as RecoveryIncident;
    const result = arrivalReportSubmission(world, incident, form, recovery, 'recover');
    expect(result.recovery?.after_flight_transfer?.group_id).toBe('G1');
    expect(result.airport_code).toBe('LHR');
    expect(world.groups[0].origin).toBe('CRA');
    expect(world.resources[0].status).toBe('needs_diversion');
    expect(world.groups[0].provenance?.arrival_reports).toBeUndefined();
    expect(afterFlightTransferGroupIssue(projected.world, projected.groupIds)).toContain(
      'arrival is unresolved',
    );
    expect(result.recovery?.after_flight_transfer).not.toHaveProperty('reportedArrivalGroupIds');
    expect(result.recovery).not.toHaveProperty('airport_transfers');
    expect(arrivalReportSubmission(world, incident, form, recovery, 'report')).not.toHaveProperty(
      'recovery',
    );
    const stale = { ...form, readyAt: '2030-11-01T12:45' };
    expect(() => arrivalReportSubmission(world, incident, stale, recovery, 'recover')).toThrow(
      'Reset',
    );
    const html = renderToStaticMarkup(
      <ArrivalRecoveryFields value={recovery} onChange={() => {}} transferContext={projected} />,
    );
    expect(html).toContain('Flight to OTP + transfer to CRA');
  });
  it('permits a new airport only in an explicit arrival preview without fabricating its world node or geography', () => {
    vi.useFakeTimers();
    vi.setSystemTime(now);
    const world = operation(),
      before = structuredClone(world);
    const form: ArrivalReportForm = {
      groupIds: ['G1'],
      airportCode: 'FCO',
      readyAt: '2030-11-01T12:30',
      description: 'Operator reports passengers ready at FCO.',
    };
    const projected = projectedArrivalRecovery(world, form)!;
    const recovery = {
      ...emptyArrivalRecoveryForm(),
      modes: ['flight', 'bus'] as ('flight' | 'bus')[],
      afterFlightTransfer: filled(projected.world, projected.groupIds, projected.scopeKey),
    };
    const incident = { id: 'CASE1', group_ids: ['G1'] } as RecoveryIncident;
    expect(afterFlightTransferGroupIssue(projected.world, ['G1'])).toContain(
      'recorded origin airport',
    );
    expect(
      afterFlightTransferGroupIssue(projected.world, ['G1'], projected.reportedArrivalGroupIds),
    ).toBeNull();
    const result = arrivalReportSubmission(world, incident, form, recovery, 'recover');
    expect(result.airport_code).toBe('FCO');
    expect(result.recovery?.after_flight_transfer?.group_id).toBe('G1');
    expect(projected.world.airports).toEqual(world.airports);
    expect(projected.world.airports.some((airport) => airport.id === 'FCO')).toBe(false);
    expect(world).toEqual(before);
    world.airports.push({ id: 'FCO', code: 'FCO', kind: 'hotel' } as World['airports'][number]);
    const conflict = projectedArrivalRecovery(world, form)!;
    expect(
      afterFlightTransferGroupIssue(conflict.world, ['G1'], conflict.reportedArrivalGroupIds),
    ).toContain('recorded origin airport');
    world.airports.at(-1)!.kind = 'airport';
    world.airports.at(-1)!.code = 'OTHER';
    const alias = projectedArrivalRecovery(world, form)!;
    expect(
      afterFlightTransferGroupIssue(alias.world, ['G1'], alias.reportedArrivalGroupIds),
    ).toContain('recorded origin airport');
  });
  it('retains authorization in a blocked job without claiming a journey or current quote', () => {
    const world = operation(),
      input = afterFlightTransferInput(
        filled(world),
        world,
        ['G1'],
        ['flight', 'bus'],
        8,
        false,
        '',
        now,
      );
    const job = {
      id: 'JOB1',
      incident_id: 'CASE1',
      group_ids: ['G1'],
      objective: 'balanced',
      modes: ['flight', 'bus'],
      max_provider_calls: 8,
      after_flight_transfer: input,
      status: 'blocked',
      stage: 'finished',
      message: 'No connecting quote',
      created_at: '2030-11-01T12:00:00Z',
      updated_at: '2030-11-01T12:00:10Z',
      calls_dispatched: 4,
      steps: [],
      gaps: [],
      result: null,
    } as unknown as RecoveryJob;
    const html = renderToStaticMarkup(<RecoveryJobDetails job={job} world={world} />);
    expect(html).toContain('Frozen authorization, not a landed position or booking');
    expect(html).toContain('arrival processing: 90 minutes');
    expect(html).toContain('2 suitcases');
    expect(html).toContain('At most 2 inbound options');
    job.result = {
      decision_id: 'D1',
      plan_id: 'P1',
      recovered_passengers: 2,
      exception_passengers: 0,
      offer_ids: ['O1'],
      resource_ids: ['FL1', 'CAB1'],
      source_counts: {},
      after_flight_transfer: {
        inputs: input!,
        incoming_resource_id: 'FL1',
        transfer_resource_id: 'CAB1',
      },
    };
    expect(renderToStaticMarkup(<RecoveryJobDetails job={job} world={world} />)).not.toContain(
      'Connection chosen in this completed attempt',
    );
    job.status = 'completed';
    expect(renderToStaticMarkup(<RecoveryJobDetails job={job} world={world} />)).toContain(
      'Connection chosen in this completed attempt',
    );
    job.result.resource_ids = ['CAB1'];
    expect(renderToStaticMarkup(<RecoveryJobDetails job={job} world={world} />)).toContain(
      'Chosen connection is not confirmed',
    );
  });
  it('retries an uncertain submission with the original exact payload and no automatic retry', async () => {
    const world = operation(),
      input = afterFlightTransferInput(
        filled(world),
        world,
        ['G1'],
        ['flight', 'bus'],
        6,
        false,
        '',
        now,
      )!;
    const fetch = vi
      .fn()
      .mockRejectedValueOnce(new Error('lost'))
      .mockResolvedValueOnce(new Response(JSON.stringify({ job: { id: 'JOB1' }, replayed: true })));
    vi.stubGlobal('fetch', fetch);
    let unknown: unknown;
    try {
      await api.startRecoveryJob(world, 'CASE1', {
        group_ids: ['G1'],
        objective: 'balanced',
        modes: ['flight', 'bus'],
        max_provider_calls: 6,
        instructions: '',
        after_flight_transfer: input,
      });
    } catch (cause) {
      unknown = cause;
    }
    expect(unknown).toBeInstanceOf(TransportError);
    expect(fetch).toHaveBeenCalledTimes(1);
    input.handbags = 7;
    await (unknown as TransportError).retry();
    expect(fetch.mock.calls[0][1].body).toBe(fetch.mock.calls[1][1].body);
    const body = JSON.parse(fetch.mock.calls[1][1].body);
    expect(body.after_flight_transfer.handbags).toBe(0);
    expect(body.after_flight_transfer.max_inbound_options).toBe(2);
    expect(body.max_provider_calls).toBe(6);
    expect(body.group_ids).toEqual(['G1']);
  });
});

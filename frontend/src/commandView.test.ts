import { describe, expect, it } from 'vitest';
import { commandView } from './commandView';
import { resourceArrivalReceipt } from './resourceArrivalReceipt.testSupport';
import type {
  AuditEvent,
  MutationResult,
  OperationalReportReceipt,
  RecoveryJob,
  World,
} from './types';

function world(overrides: Partial<World> = {}): World {
  return {
    scenario_id: 'SCENE',
    mode: 'simulation',
    version: 10,
    airports: [
      { id: 'CRA', code: 'CRA', kind: 'airport' },
      { id: 'OTP', code: 'OTP', kind: 'airport' },
    ],
    resources: [
      { id: 'BUS1', kind: 'bus', origin: 'CRA', destination: 'OTP' },
      { id: 'BUS2', kind: 'bus', origin: 'OTP', destination: 'LHR' },
      { id: 'H-CRA', kind: 'hotel', origin: 'CRA', destination: 'CRA' },
    ],
    groups: [
      { id: 'G1', incident_id: 'CASE1' },
      { id: 'G2', incident_id: 'CASE2' },
    ],
    observations: [],
    incidents: [
      { id: 'CASE1', group_ids: ['G1'] },
      { id: 'CASE2', group_ids: ['G2'] },
    ],
    events: [],
    ...overrides,
  } as World;
}

function event(
  id: string,
  type: string,
  target_id: string | null = null,
  data: Record<string, unknown> = {},
  version = 11,
): AuditEvent {
  return {
    id,
    type,
    target_id,
    version,
    data,
    timestamp: '2026-11-01T10:00:00Z',
    description: type,
  };
}

function follow(before: World, result: MutationResult) {
  return commandView(before, result, result.world);
}

describe('command view follows confirmed action targets', () => {
  it.each([
    ['G1', 'passenger'],
    ['CRA', 'airport'],
    ['OTP', 'region'],
    ['BUS1', 'airport'],
    ['BUS2', 'region'],
    ['H-CRA', 'airport'],
  ] as const)('reveals a valid explicit %s focus in the appropriate scene', (id, mode) => {
    const before = world();
    expect(follow(before, { world: before, applied: false, focus_id: id })).toEqual({
      selectedId: id,
      mode,
    });
  });

  it('uses airport view for live airports, resources and observed aircraft', () => {
    const current = world({
      mode: 'live',
      observations: [{ fr24_id: 'OBS1' }] as World['observations'],
    });
    for (const id of ['OTP', 'BUS2', 'OBS1'])
      expect(follow(current, { world: current, applied: false, focus_id: id })).toEqual({
        selectedId: id,
        mode: 'airport',
      });
  });

  it('falls back from an invalid top-level focus to a valid decision focus', () => {
    const before = world();
    const result = {
      world: before,
      applied: false,
      focus_id: 'DELETED',
      decision: { focus_id: 'G1' },
    } as MutationResult;
    expect(follow(before, result)).toEqual({ selectedId: 'G1', mode: 'passenger' });
  });

  it('derives the latest affected target and ignores later plan effects and old audit entries', () => {
    const old = event('old-airport', 'airport_closed', 'OTP', {}, 10);
    const before = world({ events: [old] });
    const current = world({
      version: 11,
      events: [
        old,
        event('schedule', 'resource_schedule', 'BUS1'),
        event('budget', 'group_budget', 'G1'),
        event('invalidated', 'offer_invalidated', 'BUS2'),
        event('confirmed', 'confirmation_affected', 'G2'),
        event('plan', 'plan_applied', 'OTP'),
      ],
    });
    expect(follow(before, { world: current, applied: true })).toEqual({
      selectedId: 'G1',
      mode: 'passenger',
    });
  });

  it('ignores unknown targets and audit entries from other versions', () => {
    const before = world();
    const current = world({
      version: 11,
      events: [
        event('schedule', 'resource_schedule', 'BUS1'),
        event('removed', 'resource_unavailable', 'DELETED'),
        event('history', 'airport_closed', 'OTP', {}, 10),
        event('future', 'group_budget', 'G2', {}, 12),
      ],
    });
    expect(follow(before, { world: current, applied: true, focus_id: 'MISSING' })).toEqual({
      selectedId: 'BUS1',
      mode: 'airport',
    });
  });

  it('does not mistake unchanged audit history for a new action', () => {
    const old = event('same-event', 'airport_weather', 'OTP');
    const before = world({ events: [old] });
    expect(
      follow(before, { world: world({ version: 11, events: [old] }), applied: true }),
    ).toBeNull();
  });

  it('follows newly added passengers from their explicit group IDs', () => {
    const before = world();
    const current = world({
      version: 11,
      events: [event('passengers', 'passengers_added', null, { group_ids: ['GONE', 'G1', 'G2'] })],
    });
    expect(follow(before, { world: current, applied: true })).toEqual({
      selectedId: 'G1',
      mode: 'passenger',
    });
  });

  it('does not derive a target for an unconfirmed or no-change reply', () => {
    const before = world();
    const current = world({ version: 11, events: [event('budget', 'group_budget', 'G1')] });
    expect(follow(before, { world: current })).toBeNull();
    expect(follow(before, { world: current, applied: false })).toBeNull();
  });

  it('accepts a decision receipt only when its source and applied versions match', () => {
    const before = world();
    const current = world({ version: 11, events: [event('budget', 'group_budget', 'G1')] });
    const result = {
      world: current,
      decision: { source_version: 10, applied_version: 11 },
    } as MutationResult;
    expect(follow(before, result)).toEqual({ selectedId: 'G1', mode: 'passenger' });
    expect(
      follow(before, { ...result, decision: { ...result.decision!, source_version: 9 } }),
    ).toBeNull();
    expect(follow(before, { ...result, applied: false })).toBeNull();
  });

  it('does not move the view for a response superseded by the accepted world', () => {
    const before = world();
    const response = world({ version: 11 });
    const result = { world: response, applied: true, focus_id: 'G1' };
    expect(commandView(before, result, world({ version: 12 }))).toBeNull();
    expect(commandView(before, result, world({ version: 11, scenario_id: 'OTHER' }))).toBeNull();
    expect(commandView(world({ version: 12 }), result, response)).toBeNull();
  });

  it('follows a newly authored scenario even when its entity IDs were reused', () => {
    const before = world();
    const current = world({
      scenario_id: 'NEW',
      version: 11,
      events: [
        event('reset', 'scenario_reset', null, { source: 'simulation' }),
        event('new-bus', 'resource_added', 'BUS1'),
      ],
    });
    expect(follow(before, { world: current, applied: true })).toEqual({
      selectedId: 'BUS1',
      mode: 'airport',
    });
  });

  it('clears an old selection after a confirmed empty reset', () => {
    const before = world();
    const current = world({
      scenario_id: 'EMPTY',
      version: 11,
      airports: [],
      resources: [],
      groups: [],
      events: [event('reset', 'scenario_reset', null, { source: 'simulation' })],
    });
    expect(follow(before, { world: current, applied: true })).toEqual({
      selectedId: null,
      mode: 'region',
    });
  });

  it('does not follow an unrelated replacement or an old/reset-unconfirmed marker', () => {
    const before = world();
    const current = world({ scenario_id: 'OTHER', version: 11 });
    expect(follow(before, { world: current, applied: true, focus_id: 'G1' })).toBeNull();
    const stale = {
      ...current,
      events: [event('reset', 'scenario_reset', null, { source: 'simulation' }, 10)],
    };
    expect(follow(before, { world: stale, applied: true, focus_id: 'G1' })).toBeNull();
    const reset = {
      ...current,
      events: [event('reset', 'scenario_reset', null, { source: 'simulation' })],
    };
    expect(follow(before, { world: reset, applied: false, focus_id: 'G1' })).toBeNull();
  });
});

describe('command view respects scoped operational reports', () => {
  it('follows the validated combined arrival, not affected groups from other cases', () => {
    const result = resourceArrivalReceipt();
    const before = { ...result.world, version: 7, events: [] };
    expect(follow(before, result)).toEqual({ selectedId: 'OTP', mode: 'airport' });
    result.recovery_blocked = {
      code: 'airport_transfer_setup_required',
      explanation: 'Review pickup inputs.',
    };
    expect(follow(before, result)).toEqual({ selectedId: 'OTP', mode: 'airport' });
  });

  it('never falls back to focus or audit for an invalid combined report', () => {
    const result = resourceArrivalReceipt();
    result.reports = result.reports!.slice(0, 1);
    result.world.events.push(event('extra', 'group_budget', 'G1', {}, result.world.version));
    const before = { ...result.world, version: 7, events: [] };
    expect(follow(before, result)).toBeNull();
  });

  it('follows only the current arrival receipt, including a still-current replay', () => {
    const { reports: _reports, ...result } = resourceArrivalReceipt();
    const before = { ...result.world, version: 7, events: [] };
    expect(follow(before, { ...result, replayed: true })).toEqual({
      selectedId: 'OTP',
      mode: 'airport',
    });
    const current = structuredClone(result.world);
    current.groups[0].provenance = { arrival_reports: [{ id: 'LATER' }] };
    expect(commandView(before, result, current)).toBeNull();
    expect(follow(before, { ...result, applied: false })).toBeNull();
  });

  it('follows a singleton airport restriction only while that report is current', () => {
    const report: OperationalReportReceipt = {
      id: 'RESTRICTION1',
      type: 'airport_restriction',
      source: 'operator_report',
      received_at: '2026-11-01T10:00:00Z',
      description: 'Arrivals restricted.',
      affected_group_ids: ['G2'],
      affected_offer_ids: [],
      incident_id: 'CASE1',
      airport_code: 'OTP',
      operations: ['arrivals'],
      action: 'restrict',
    };
    const before = world({ mode: 'live' });
    const current = world({
      mode: 'live',
      version: 11,
      incidents: [
        { id: 'CASE1', group_ids: ['G1'], airport_restriction_reports: [report] },
      ] as unknown as World['incidents'],
    });
    const result = { world: current, applied: true, report, focus_id: 'G2' };
    expect(follow(before, result)).toEqual({ selectedId: 'OTP', mode: 'airport' });
    const superseded = structuredClone(current);
    superseded.incidents![0].airport_restriction_reports!.push({
      ...report,
      id: 'RESTRICTION2',
      action: 'clear',
    });
    expect(commandView(before, result, superseded)).toBeNull();
    expect(follow(before, { ...result, applied: false })).toBeNull();
  });

  it('follows a current resource-unavailability receipt without borrowing its affected groups', () => {
    const result = resourceArrivalReceipt();
    const resourceReport = result.reports![0];
    const singleton = {
      ...result,
      reports: undefined,
      report: resourceReport,
      focus_id: 'OTHER-CASE-GROUP',
    };
    const before = { ...result.world, version: 7, events: [] };
    expect(follow(before, singleton)).toEqual({ selectedId: 'BUS1', mode: 'airport' });
    const current = structuredClone(result.world);
    current.resources[0].provenance = { unavailability_reports: [{ id: 'LATER' }] };
    expect(commandView(before, singleton, current)).toBeNull();
  });

  it('uses a recovery job group belonging to the exact case, even for a status-only reply', () => {
    const current = world({ mode: 'live' });
    const result = {
      world: current,
      applied: false,
      incident_id: 'CASE1',
      affected_group_ids: ['G2'],
      recovery_job: { id: 'JOB1', incident_id: 'CASE1', group_ids: ['G1'] } as RecoveryJob,
    };
    expect(follow(current, result)).toEqual({ selectedId: 'G1', mode: 'passenger' });
    expect(
      follow(current, { ...result, recovery_job: { ...result.recovery_job, group_ids: ['G2'] } }),
    ).toBeNull();
    expect(
      follow(current, {
        ...result,
        recovery_job: { ...result.recovery_job, incident_id: 'CASE2' },
      }),
    ).toBeNull();
    expect(follow(current, { ...result, recovery_job: null })).toBeNull();
  });
});

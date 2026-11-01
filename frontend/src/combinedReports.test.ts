import { describe, expect, it } from 'vitest';
import { combinedReports, UNCONFIRMED_COMBINED_REPORT } from './combinedReports';
import {
  commandNeedsRegionalView,
  recoveryCaseForResult,
  verifiedCommandResult,
} from './commandContext';
import { reportedArrivalFocus } from './arrivalFocus';
import { voiceCommandAcknowledgement } from './liveVoice';
import { resourceArrivalReceipt } from './resourceArrivalReceipt.testSupport';
import {
  beginVoiceRequestReview,
  settleVoiceRequestReview,
  voiceReviewCanCorrect,
  voiceReviewStatusLabel,
} from './voiceRequestReview';
import type {
  MutationResult,
  OperationalReportReceipt,
  RecoveryBlockedReceipt,
  RecoveryJob,
  World,
} from './types';

function receipt(): MutationResult {
  const arrival: OperationalReportReceipt = {
    id: 'ARRIVAL1',
    type: 'arrival_report',
    source: 'operator_report',
    incident_id: 'CASE1',
    received_at: '2026-11-01T09:00:00Z',
    description: 'Passengers reported at OTP.',
    airport_code: 'OTP',
    group_ids: ['G1'],
    ready_at: '2026-11-01T12:30:00+02:00',
    affected_group_ids: ['G1'],
    affected_offer_ids: [],
  };
  const restriction: OperationalReportReceipt = {
    id: 'RESTRICTION1',
    type: 'airport_restriction',
    source: 'operator_report',
    incident_id: 'CASE1',
    received_at: '2026-11-01T09:00:00Z',
    description: 'Operator restricts case arrivals.',
    airport_code: 'CRA',
    operations: ['arrivals'],
    action: 'restrict',
    recovery_group_ids: [],
    affected_group_ids: [],
    affected_offer_ids: [],
    manual_review_required: false,
    confirmed_conflicts: [],
  };
  const world = {
    scenario_id: 'OP1',
    mode: 'live',
    version: 8,
    incidents: [{ id: 'CASE1' }],
    airports: [
      { id: 'OTP', code: 'OTP', kind: 'airport' },
      { id: 'CRA', code: 'CRA', kind: 'airport' },
    ],
    groups: [
      {
        id: 'G1',
        incident_id: 'CASE1',
        origin: 'OTP',
        provenance: { arrival_reports: [{ id: 'ARRIVAL1' }] },
      },
    ],
    events: [{ id: 'EVENT1', type: 'airport_closed', target_id: 'CRA' }],
  } as unknown as World;
  return {
    world,
    applied: true,
    report: arrival,
    reports: [restriction, structuredClone(arrival)],
    incident_id: 'CASE1',
    focus_id: 'OTP',
    manual_review_required: false,
    confirmed_conflicts: [],
    command_interpretation_calls: 1,
  };
}
function withRecovery(result = receipt(), status: RecoveryJob['status'] = 'queued') {
  if (result.reports?.[0].type !== 'airport_restriction') throw new Error('Invalid fixture');
  result.reports[0].recovery_group_ids = ['G1'];
  result.recovery_job = {
    id: 'JOB1',
    incident_id: 'CASE1',
    group_ids: ['G1'],
    status,
    stage: 'queued',
    calls_dispatched: 0,
  } as RecoveryJob;
  result.recovery_total_call_limit = 8;
  return result;
}
const compact = (result: MutationResult) => {
  const speech = voiceCommandAcknowledgement(result);
  expect(new TextEncoder().encode(speech).byteLength).toBeLessThanOrEqual(480);
  expect(speech).not.toContain('…');
  expect(speech).toContain('CRA');
  expect(speech).toContain('grup la OTP');
  expect(speech).toContain('1 noiembrie 2026, ora 10:30 UTC');
  expect(speech).toContain('nu confirmă aterizarea, permisiuni de zbor sau rezervări');
  return speech;
};

describe('correlated atomic operational report receipts', () => {
  it('accepts both report facts while retaining only the grounded arrival focus and matching case', () => {
    const result = receipt();
    expect(combinedReports(result).state).toBe('combined');
    expect(reportedArrivalFocus(result, result.world)).toBe('OTP');
    expect(recoveryCaseForResult(result, result.world)).toBe('CASE1');
    expect(commandNeedsRegionalView({ ...result.world, events: [] }, result)).toBe(false);
    const detail = verifiedCommandResult(result);
    expect(detail).toContain('restricția cazului CASE1 pentru CRA, aterizări, aplicată');
    expect(detail).toContain('grupurile G1 raportate la OTP');
    expect(detail).toContain('2026-11-01T12:30:00+02:00');
    expect(compact(result)).toContain('Nu am pornit o căutare');
  });
  it('compares the primary arrival by values rather than object key insertion order', () => {
    const result = receipt();
    result.report = Object.fromEntries(
      Object.entries(result.report!).reverse(),
    ) as OperationalReportReceipt;
    expect(combinedReports(result).state).toBe('combined');
  });
  it('retains both recorded reports in voice review without enabling a new corrected submission', () => {
    const result = receipt();
    const original = 'Restricționează aterizările la CRA.\nG1 este pregătit la OTP.';
    const review = beginVoiceRequestReview(
      { text: original, delegationId: 'D1', context: '' },
      'OP1',
      1,
    );
    const next = settleVoiceRequestReview(review, review, 'OP1', { kind: 'response', result })!;
    expect(next.text).toBe(original);
    expect(next.status).toBe('reports_recorded');
    expect(voiceReviewStatusLabel[next.status]).toBe('Operational reports recorded');
    expect(next.detail).toContain('restricția cazului CASE1 pentru CRA');
    expect(next.detail).toContain('grupurile G1 raportate la OTP');
    expect(voiceReviewCanCorrect(next)).toBe(false);
  });
  it.each([
    [
      'absent array',
      (r: MutationResult) => {
        r.reports = null as unknown as OperationalReportReceipt[];
      },
    ],
    [
      'empty array',
      (r: MutationResult) => {
        r.reports = [];
      },
    ],
    [
      'missing child',
      (r: MutationResult) => {
        r.reports = [r.report!];
      },
    ],
    [
      'extra child',
      (r: MutationResult) => {
        r.reports!.push(r.report!);
      },
    ],
    [
      'duplicate kind',
      (r: MutationResult) => {
        r.reports![0] = structuredClone(r.report!);
      },
    ],
    [
      'wrong order',
      (r: MutationResult) => {
        r.reports!.reverse();
      },
    ],
    [
      'duplicate ID',
      (r: MutationResult) => {
        r.reports![0].id = r.report!.id;
      },
    ],
    [
      'foreign case',
      (r: MutationResult) => {
        Object.assign(r.reports![0], { incident_id: 'OTHER' });
      },
    ],
    [
      'primary mismatch',
      (r: MutationResult) => {
        Object.assign(r.report!, { ready_at: '2026-11-02T12:30:00+02:00' });
      },
    ],
    [
      'primary missing',
      (r: MutationResult) => {
        r.report = undefined;
      },
    ],
    [
      'invalid direction',
      (r: MutationResult) => {
        Object.assign(r.reports![0], { operations: ['taxi'] });
      },
    ],
    [
      'duplicate direction',
      (r: MutationResult) => {
        Object.assign(r.reports![0], { operations: ['arrivals', 'arrivals'] });
      },
    ],
    [
      'invalid action',
      (r: MutationResult) => {
        Object.assign(r.reports![0], { action: 'open' });
      },
    ],
    [
      'naive readiness',
      (r: MutationResult) => {
        Object.assign(r.report!, { ready_at: '2026-11-01T12:30:00' });
        r.reports![1] = structuredClone(r.report!);
      },
    ],
    [
      'empty groups',
      (r: MutationResult) => {
        Object.assign(r.report!, { group_ids: [] });
        r.reports![1] = structuredClone(r.report!);
      },
    ],
    [
      'duplicate groups',
      (r: MutationResult) => {
        Object.assign(r.report!, { group_ids: ['G1', 'G1'] });
        r.reports![1] = structuredClone(r.report!);
      },
    ],
    [
      'foreign recovery scope',
      (r: MutationResult) => {
        Object.assign(r.reports![0], { recovery_group_ids: ['G2'] });
      },
    ],
    [
      'overlarge arrival scope',
      (r: MutationResult) => {
        Object.assign(r.report!, {
          group_ids: Array.from({ length: 25 }, (_, index) => `G${index}`),
        });
        r.reports![1] = structuredClone(r.report!);
      },
    ],
    [
      'missing recovery outcome',
      (r: MutationResult) => {
        Object.assign(r.reports![0], { recovery_group_ids: ['G1'] });
      },
    ],
    [
      'job has foreign case',
      (r: MutationResult) => {
        withRecovery(r);
        r.recovery_job!.incident_id = 'OTHER';
      },
    ],
    [
      'job has foreign groups',
      (r: MutationResult) => {
        withRecovery(r);
        r.recovery_job!.group_ids = ['G2'];
      },
    ],
    [
      'job has duplicate groups',
      (r: MutationResult) => {
        withRecovery(r);
        r.recovery_job!.group_ids = ['G1', 'G1'];
      },
    ],
    [
      'conflicts have malformed IDs',
      (r: MutationResult) => {
        const conflicts = [{ group_id: '', offer_id: null, resource_ids: ['F1'] }];
        Object.assign(r.reports![0], { confirmed_conflicts: conflicts });
        r.confirmed_conflicts = conflicts;
      },
    ],
    [
      'hidden confirmed conflict',
      (r: MutationResult) => {
        const conflicts = [{ group_id: 'G1', offer_id: null, resource_ids: ['F1'] }];
        Object.assign(r.reports![0], { confirmed_conflicts: conflicts });
        r.confirmed_conflicts = conflicts;
      },
    ],
    [
      'unrecorded',
      (r: MutationResult) => {
        r.applied = false;
      },
    ],
    [
      'focus mismatch',
      (r: MutationResult) => {
        r.focus_id = 'CRA';
      },
    ],
    [
      'conflicting review flag',
      (r: MutationResult) => {
        r.manual_review_required = true;
      },
    ],
  ] as const)('does not silently acknowledge only the arrival for %s', (_name, mutate) => {
    const result = receipt();
    mutate(result);
    expect(combinedReports(result).state).toBe('invalid');
    expect(voiceCommandAcknowledgement(result)).toBe(UNCONFIRMED_COMBINED_REPORT);
    expect(verifiedCommandResult(result)).toBe(UNCONFIRMED_COMBINED_REPORT);
    expect(reportedArrivalFocus(result, result.world)).toBeNull();
    expect(recoveryCaseForResult(result, result.world)).toBeNull();
    expect(commandNeedsRegionalView(result.world, result)).toBe(false);
    const review = beginVoiceRequestReview(
      { text: 'Original exact request', delegationId: 'D1', context: '' },
      'OP1',
      1,
    );
    const next = settleVoiceRequestReview(review, review, 'OP1', { kind: 'response', result })!;
    expect(next.status).toBe('unknown');
    expect(voiceReviewCanCorrect(next)).toBe(false);
  });
  it('keeps replay historical and only focuses an arrival that remains current', () => {
    const result = withRecovery();
    result.replayed = true;
    expect(compact(result)).toContain('Rapoarte deja înregistrate');
    expect(compact(result)).toContain('Căutare deja înregistrată');
    expect(compact(result)).not.toMatch(/în coadă|am pornit|finalizată/);
    const current = structuredClone(result.world);
    current.groups[0].provenance = { arrival_reports: [{ id: 'NEWER' }] };
    expect(reportedArrivalFocus(result, current)).toBeNull();
    expect(recoveryCaseForResult(result, current)).toBe('CASE1');
    expect(recoveryCaseForResult(result, { ...current, scenario_id: 'NEW' })).toBeNull();
  });
  it('distinguishes queued, blocked and predecessor waiting without sacrificing either report', () => {
    const result = withRecovery();
    expect(compact(result)).toContain('Căutare înregistrată');
    expect(compact(result)).toContain('Limită: 8 apeluri cu interpretarea');
    result.recovery_job!.stage = 'waiting_for_previous';
    expect(compact(result)).toContain('aștepta încercările anterioare; 0 apeluri proprii');
    result.recovery_job = null;
    result.recovery_blocked = {
      code: 'insufficient_call_allowance',
      explanation: 'Limit too small',
    };
    expect(compact(result)).toContain('Căutarea nu a pornit: limită insuficientă');
  });
  it('retains clear/restrict directions, full readiness and conflict warning within the byte cap', () => {
    const result = withRecovery();
    Object.assign(result.reports![0], {
      operations: ['arrivals', 'departures'],
      action: 'clear',
      manual_review_required: true,
      confirmed_conflicts: [{ group_id: 'G1', offer_id: 'O1', resource_ids: ['F1'] }],
    });
    result.manual_review_required = true;
    result.confirmed_conflicts = (
      result.reports![0] as Extract<OperationalReportReceipt, { type: 'airport_restriction' }>
    ).confirmed_conflicts;
    result.recovery_job!.stage = 'waiting_for_previous';
    const speech = compact(result);
    expect(speech).toContain('aterizări și decolări, restricție eliminată în caz');
    expect(speech).toContain('Rezervări în conflict; intervenție necesară');
    expect(speech).not.toMatch(/aeroportul este deschis|aterizare confirmată|sosire observată/);
  });
  it('does not treat a terminal or contradictory search record as newly started', () => {
    const result = withRecovery(undefined, 'completed');
    expect(compact(result)).toContain('Căutare consemnată: încheiată');
    expect(compact(result)).not.toContain('Căutare înregistrată');
    result.recovery_blocked = { code: 'recovery_prerequisite_failed', explanation: 'Conflict' };
    expect(voiceCommandAcknowledgement(result)).toBe(UNCONFIRMED_COMBINED_REPORT);
    expect(recoveryCaseForResult(result, result.world)).toBeNull();
  });

  const outcomes: [string, (r: MutationResult) => void, string][] = [
    [
      'report only',
      (r) => {
        r.recovery_job = null;
        Object.assign(r.reports![0], { recovery_group_ids: [] });
      },
      'Nu am pornit o căutare',
    ],
    ['queued', () => {}, 'Căutare deja înregistrată'],
    [
      'running',
      (r) => {
        r.recovery_job!.status = 'running';
      },
      'Căutare deja înregistrată',
    ],
    [
      'predecessor waiting',
      (r) => {
        r.recovery_job!.stage = 'waiting_for_previous';
      },
      'aștepta încercările anterioare; 0 apeluri proprii',
    ],
    [
      'contradictory running wait',
      (r) => {
        r.recovery_job!.stage = 'waiting_for_previous';
        r.recovery_job!.status = 'running';
      },
      'Așteptare contradictorie',
    ],
    [
      'contradictory dispatched wait',
      (r) => {
        r.recovery_job!.stage = 'waiting_for_previous';
        r.recovery_job!.calls_dispatched = 1;
      },
      'Așteptare contradictorie',
    ],
    [
      'superseded',
      (r) => {
        r.recovery_job!.superseded_by_job_id = 'JOB2';
      },
      'oprirea nu e confirmată',
    ],
    [
      'ancestor review',
      (r) => {
        r.recovery_job!.status = 'needs_review';
        r.recovery_job!.blocking_job_ids = ['ANCESTOR'];
      },
      'revizuirea încercărilor anterioare; 0 apeluri proprii',
    ],
    [
      'predecessor review',
      (r) => {
        r.recovery_job!.status = 'needs_review';
        r.recovery_job!.supersedes_job_ids = ['PREVIOUS'];
      },
      'revizuirea încercărilor anterioare; 0 apeluri proprii',
    ],
    ...(
      ['completed', 'blocked', 'failed', 'cancelled', 'needs_review', 'unknown_outcome'] as const
    ).map((status): [string, (r: MutationResult) => void, string] => [
      status,
      (r) => {
        r.recovery_job!.status = status;
      },
      'Căutare consemnată:',
    ]),
    ...(
      [
        'insufficient_call_allowance',
        'hotel_setup_required',
        'airport_transfer_setup_required',
        'ai_not_configured',
        'recovery_service_unavailable',
        'recovery_prerequisite_failed',
        'recovery_queue_unavailable',
      ] as RecoveryBlockedReceipt['code'][]
    ).map((code): [string, (r: MutationResult) => void, string] => [
      code,
      (r) => {
        r.recovery_job = null;
        r.recovery_blocked = { code, explanation: 'Recorded blocker' };
      },
      'Căutarea nu a pornit:',
    ]),
  ];
  it.each(outcomes)(
    'preserves both facts and %s within 480 UTF-8 bytes at the accepted speech bounds',
    (_label, mutate, expected) => {
      for (const action of ['restrict', 'clear'] as const) {
        const result = withRecovery();
        const groupIds = Array.from({ length: 24 }, (_, index) => `G${index}`);
        const conflicts = [{ group_id: 'G1', offer_id: 'O1', resource_ids: ['F1'] }];
        result.replayed = true;
        result.focus_id = 'LROP';
        Object.assign(result.report!, {
          airport_code: 'LROP',
          ready_at: '9999-09-30T23:59:00Z',
          group_ids: groupIds,
        });
        result.reports![1] = structuredClone(result.report!);
        Object.assign(result.reports![0], {
          airport_code: 'LRCV',
          operations: ['arrivals', 'departures'],
          action,
          recovery_group_ids: groupIds,
          manual_review_required: true,
          confirmed_conflicts: conflicts,
        });
        result.manual_review_required = true;
        result.confirmed_conflicts = conflicts;
        result.recovery_job!.group_ids = groupIds;
        result.recovery_total_call_limit = 27;
        mutate(result);
        expect(combinedReports(result).state).toBe('combined');
        const speech = voiceCommandAcknowledgement(result);
        expect(new TextEncoder().encode(speech).byteLength).toBeLessThanOrEqual(480);
        expect(speech).toContain(
          `LRCV, aterizări și decolări, restricție ${action === 'clear' ? 'eliminată' : 'aplicată'} în caz`,
        );
        expect(speech).toContain(
          '24 grupuri la LROP, pregătite de la 30 septembrie 9999, ora 23:59 UTC',
        );
        expect(speech).toContain(expected);
        expect(speech).toContain('Rezervări în conflict; intervenție necesară');
        expect(speech).toContain(
          'Date raportate; nu confirmă aterizarea, permisiuni de zbor sau rezervări',
        );
        expect(speech).not.toContain('…');
      }
    },
  );
  it('leaves existing single-report behavior unchanged', () => {
    const result = receipt();
    delete result.reports;
    expect(combinedReports(result)).toEqual({ state: 'none' });
    expect(voiceCommandAcknowledgement(result)).toContain('Raport înregistrat: 1 grup la OTP');
  });
});

describe('atomic service unavailability and passenger arrival receipts', () => {
  const withJob = (result = resourceArrivalReceipt()) => {
    if (result.report?.type !== 'arrival_report') throw new Error('Invalid fixture');
    result.recovery_job = {
      id: 'JOB1',
      incident_id: 'CASE1',
      group_ids: [...result.report.group_ids],
      status: 'queued',
      stage: 'queued',
      calls_dispatched: 0,
    } as RecoveryJob;
    result.recovery_total_call_limit = 8;
    return result;
  };
  it.each(['bus', 'flight', 'hotel'] as const)(
    'accepts a recorded %s outage with wider effects than the arrival and recovery scope',
    (resourceKind) => {
      const result = withJob(resourceArrivalReceipt({ resourceKind }));
      expect(combinedReports(result)).toMatchObject({
        state: 'combined',
        kind: 'resource_and_arrival',
        resource: { affected_group_ids: ['G1', 'OTHER-CASE-GROUP'] },
        arrival: { incident_id: 'CASE1', group_ids: ['G1'] },
      });
      expect(recoveryCaseForResult(result, result.world)).toBe('CASE1');
      expect(reportedArrivalFocus(result, result.world)).toBe('OTP');
      expect(commandNeedsRegionalView({ ...result.world, events: [] }, result)).toBe(false);
      expect(result.world.groups.find((group) => group.id === 'OTHER-CASE-GROUP')!.origin).toBe(
        'CRA',
      );
      const speech = voiceCommandAcknowledgement(result);
      expect(speech).toContain('serviciul BUS1 raportat indisponibil pentru planificare');
      expect(speech).toContain('1 grup la OTP, pregătit de la 1 noiembrie 2026, ora 10:30 UTC');
      expect(speech).toContain('Căutare înregistrată; verifică progresul actual');
      expect(speech).toContain(
        'nu confirmă aterizarea, disponibilitatea la furnizor sau rezervări',
      );
      expect(speech).not.toContain('2 grupuri la OTP');
    },
  );

  const invalid: [string, (result: MutationResult) => void][] = [
    ['reversed children', (r) => r.reports!.reverse()],
    [
      'only one child',
      (r) => {
        r.reports = [r.report!];
      },
    ],
    [
      'duplicate report IDs',
      (r) => {
        r.reports![0].id = r.report!.id;
      },
    ],
    [
      'mismatched primary',
      (r) => {
        r.report!.description = 'Different report';
      },
    ],
    [
      'unknown child type',
      (r) => {
        Object.assign(r.reports![0], { type: 'other' });
      },
    ],
    [
      'invalid previous availability',
      (r) => {
        Object.assign(r.reports![0], { previous_available: 'true' });
      },
    ],
    [
      'unrecorded resource',
      (r) => {
        r.world.resources = [];
      },
    ],
    [
      'duplicate resource',
      (r) => {
        r.world.resources.push(structuredClone(r.world.resources[0]));
      },
    ],
    [
      'resource still available',
      (r) => {
        r.world.resources[0].available = true;
      },
    ],
    [
      'missing unavailability history',
      (r) => {
        r.world.resources[0].provenance = {};
      },
    ],
    [
      'different unavailability tail',
      (r) => {
        r.world.resources[0].provenance!.unavailability_reports = [
          { ...r.reports![0], description: 'Different' },
        ];
      },
    ],
    [
      'later resource report',
      (r) => {
        (r.world.resources[0].provenance!.unavailability_reports as unknown[]).push({
          ...r.reports![0],
          id: 'NEWER',
        });
      },
    ],
    [
      'missing audit event',
      (r) => {
        r.world.events.pop();
      },
    ],
    [
      'different response revision',
      (r) => {
        r.world.version += 1;
      },
    ],
    [
      'different service revision',
      (r) => {
        r.world.events[0].version -= 1;
      },
    ],
    [
      'different arrival revision',
      (r) => {
        r.world.events[1].version -= 1;
      },
    ],
    [
      'different audit target',
      (r) => {
        r.world.events[0].target_id = 'OTHER';
      },
    ],
    [
      'different audit facts',
      (r) => {
        r.world.events[0].data.previous_available = false;
      },
    ],
    [
      'different audit type',
      (r) => {
        r.world.events[0].type = 'operator_arrival_report';
      },
    ],
    [
      'duplicate audit identity',
      (r) => {
        r.world.events[1].id = r.world.events[0].id;
      },
    ],
    [
      'duplicate matching event',
      (r) => {
        r.world.events.push(structuredClone(r.world.events[0]));
      },
    ],
    [
      'missing arrival case',
      (r) => {
        r.world.incidents = r.world.incidents!.filter((item) => item.id !== 'CASE1');
      },
    ],
    [
      'different case membership',
      (r) => {
        r.world.incidents![0].group_ids = ['UNSELECTED'];
      },
    ],
    [
      'different group case',
      (r) => {
        r.world.groups[0].incident_id = 'CASE2';
      },
    ],
    [
      'different group location',
      (r) => {
        r.world.groups[0].origin = 'CRA';
      },
    ],
    [
      'different group readiness',
      (r) => {
        r.world.groups[0].ready_at = 61;
      },
    ],
    [
      'different original flight',
      (r) => {
        r.world.groups[0].original_flight_id = 'OTHER-FLIGHT';
      },
    ],
    [
      'different scenario binding',
      (r) => {
        r.world.scenario_id = 'OTHER';
      },
    ],
    [
      'different epoch binding',
      (r) => {
        r.world.epoch = '2026-11-01T08:00:00Z';
      },
    ],
    [
      'missing arrival history',
      (r) => {
        r.world.groups[0].provenance = {};
      },
    ],
    [
      'unverified airport node',
      (r) => {
        r.world.airports[0].kind = 'station';
      },
    ],
    [
      'missing airport node',
      (r) => {
        r.world.airports = [];
      },
    ],
    [
      'wrong focus',
      (r) => {
        r.focus_id = 'BUS1';
      },
    ],
    [
      'wrong interpretation count',
      (r) => {
        r.command_interpretation_calls = 2;
      },
    ],
    [
      'wrong response mode',
      (r) => {
        delete r.mode;
      },
    ],
    [
      'wrong total affected scope',
      (r) => {
        r.affected_group_ids = ['G1'];
      },
    ],
    [
      'wrong invalidated offers',
      (r) => {
        r.invalidated_offer_ids = [];
      },
    ],
    [
      'unrelated confirmation conflict',
      (r) => {
        r.manual_review_required = true;
      },
    ],
    [
      'recovery in other case',
      (r) => {
        r.recovery_job!.incident_id = 'CASE2';
      },
    ],
    [
      'recovery widened to service effects',
      (r) => {
        r.recovery_job!.group_ids.push('OTHER-CASE-GROUP');
      },
    ],
    [
      'recovery contradicts blocker',
      (r) => {
        r.recovery_blocked = { code: 'recovery_prerequisite_failed', explanation: 'Blocked' };
      },
    ],
  ];
  it.each(invalid)('rejects %s without falling back to the arrival child', (_label, mutate) => {
    const result = withJob();
    mutate(result);
    expect(combinedReports(result)).toEqual({ state: 'invalid' });
    expect(voiceCommandAcknowledgement(result)).toBe(UNCONFIRMED_COMBINED_REPORT);
    expect(verifiedCommandResult(result)).toBe(UNCONFIRMED_COMBINED_REPORT);
    expect(recoveryCaseForResult(result, result.world)).toBeNull();
    expect(reportedArrivalFocus(result, result.world)).toBeNull();
  });
  it('can record a repeated outage without claiming that it first changed availability', () => {
    const result = resourceArrivalReceipt();
    Object.assign(result.reports![0], { previous_available: false });
    result.world.resources[0].provenance!.unavailability_reports = [
      structuredClone(result.reports![0]),
    ];
    result.world.events[0].data = structuredClone(result.reports![0]);
    expect(combinedReports(result).state).toBe('combined');
    expect(voiceCommandAcknowledgement(result)).toContain('Nu am pornit o căutare');
  });
  it('allows a disjoint service impact and keeps it out of the arrival selection', () => {
    const result = withJob();
    result.reports![0].affected_group_ids = ['OTHER-CASE-GROUP'];
    result.world.resources[0].provenance!.unavailability_reports = [
      structuredClone(result.reports![0]),
    ];
    result.world.events[0].data = structuredClone(result.reports![0]);
    result.affected_group_ids = ['OTHER-CASE-GROUP'];
    expect(combinedReports(result).state).toBe('combined');
    expect(result.recovery_job!.group_ids).toEqual(['G1']);
    expect(voiceCommandAcknowledgement(result)).toContain('1 grup la OTP');
  });
  it('treats replay as an immutable observation and does not focus a superseded arrival', () => {
    const result = withJob();
    result.replayed = true;
    const current = structuredClone(result.world);
    current.version += 2;
    current.resources[0].available = true;
    current.groups[0].provenance = { arrival_reports: [{ id: 'NEWER' }] };
    expect(combinedReports(result).state).toBe('combined');
    expect(reportedArrivalFocus(result, current)).toBeNull();
    expect(recoveryCaseForResult(result, current)).toBe('CASE1');
    expect(voiceCommandAcknowledgement(result)).toContain('Rapoarte deja înregistrate');
    expect(voiceCommandAcknowledgement(result)).toContain('Căutare deja înregistrată');
    expect(voiceCommandAcknowledgement(result)).not.toMatch(
      /am pornit|în coadă|rezervat|a aterizat/,
    );
  });
  it('preserves a relative readiness basis in the correlated arrival evidence', () => {
    const result = resourceArrivalReceipt();
    const basis = {
      kind: 'command_relative_minutes',
      reference: 'command_received_at',
      command_received_at: result.world.epoch,
      anchor_at: result.world.epoch,
      rounding: 'ceil_minute',
      minutes: 60,
    };
    Object.assign(result.report!, { readiness_basis: basis });
    Object.assign(result.reports![1], { readiness_basis: basis });
    Object.assign((result.world.groups[0].provenance!.arrival_reports as object[])[0], {
      readiness_basis: basis,
    });
    Object.assign(result.world.events[1].data, { readiness_basis: basis });
    expect(combinedReports(result).state).toBe('combined');
    Object.assign(result.world.events[1].data, { readiness_basis: { ...basis, minutes: 30 } });
    expect(combinedReports(result).state).toBe('invalid');
  });
  it('keeps both facts and every job/blocker status below the voice byte cap', () => {
    const outcomes: ((r: MutationResult) => void)[] = [
      (r) => {
        r.recovery_job = null;
      },
      (r) => {
        r.recovery_job!.stage = 'waiting_for_previous';
      },
      (r) => {
        r.recovery_job!.superseded_by_job_id = 'NEWER';
      },
      (r) => {
        r.recovery_job!.status = 'needs_review';
        r.recovery_job!.blocking_job_ids = ['ANCESTOR'];
      },
      ...(
        [
          'queued',
          'running',
          'completed',
          'blocked',
          'failed',
          'cancelled',
          'needs_review',
          'unknown_outcome',
        ] as const
      ).map((status) => (r: MutationResult) => {
        r.recovery_job!.status = status;
      }),
      ...(
        [
          'insufficient_call_allowance',
          'hotel_setup_required',
          'airport_transfer_setup_required',
          'ai_not_configured',
          'recovery_service_unavailable',
          'recovery_prerequisite_failed',
          'recovery_queue_unavailable',
        ] as const
      ).map((code) => (r: MutationResult) => {
        r.recovery_job = null;
        r.recovery_blocked = { code, explanation: 'See case' };
      }),
    ];
    for (const resourceId of ['R'.repeat(40), '🚌'.repeat(10), 'R'.repeat(128)]) {
      for (const mutate of outcomes) {
        const result = withJob(
          resourceArrivalReceipt({
            resourceId,
            airportCode: 'LROP',
            groupIds: Array.from({ length: 24 }, (_, index) => `G${index}`),
            readyAt: '9999-09-30T23:59:00Z',
          }),
        );
        result.replayed = true;
        result.recovery_total_call_limit = 27;
        mutate(result);
        expect(combinedReports(result).state).toBe('combined');
        const speech = voiceCommandAcknowledgement(result);
        expect(new TextEncoder().encode(speech).byteLength).toBeLessThanOrEqual(480);
        expect(speech).toContain('raportat indisponibil pentru planificare');
        expect(speech).toContain(
          '24 grupuri la LROP, pregătite de la 30 septembrie 9999, ora 23:59 UTC',
        );
        expect(speech).toContain(
          'Date raportate; nu confirmă aterizarea, disponibilitatea la furnizor sau rezervări.',
        );
        expect(speech).not.toContain('…');
      }
    }
  });
});

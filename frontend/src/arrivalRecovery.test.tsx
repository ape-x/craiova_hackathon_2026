import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { api, TransportError } from './api';
import { ArrivalRecoveryFields, ArrivalRecoveryReceipt } from './ArrivalRecoveryFields';
import {
  arrivalRecoveryIssue,
  arrivalRecoveryReceipt,
  arrivalReportSubmission,
  emptyArrivalRecoveryForm,
} from './arrivalRecoveryData';
import type { ArrivalRecoveryForm } from './arrivalRecoveryData';
import type { ArrivalReportForm } from './operationalReports';
import { mergeRecoveryJobs } from './recoveryJobs';
import type { MutationResult, RecoveryIncident, RecoveryJob, World } from './types';

const incident = { id: 'CASE1', group_ids: ['G1'] } as RecoveryIncident;
const world = {
  mode: 'live',
  scenario_id: 'OPS',
  version: 4,
  epoch: '2026-11-01T00:00:00Z',
  groups: [{ id: 'G1', incident_id: 'CASE1', status: 'offered' }],
  offers: [],
  reservations: [],
} as unknown as World;
const form: ArrivalReportForm = {
  groupIds: ['G1'],
  airportCode: 'OTP',
  readyAt: '2026-11-01T12:00',
  description: 'Operator-reported passengers ready at OTP.',
};
const job = {
  id: 'JOB2',
  incident_id: 'CASE1',
  group_ids: ['G1'],
  status: 'queued',
  stage: 'waiting_for_previous',
  calls_dispatched: 0,
  max_provider_calls: 8,
  supersedes_job_ids: ['JOB1'],
  created_at: '2026-11-01T11:00:00Z',
  updated_at: '2026-11-01T11:00:00Z',
} as RecoveryJob;
const receipt = (overrides: Partial<MutationResult> = {}): MutationResult => ({
  world,
  applied: true,
  incident_id: incident.id,
  focus_id: 'OTP',
  recovery_job: job,
  report: {
    id: 'REPORT1',
    type: 'arrival_report',
    source: 'operator_report',
    incident_id: 'CASE1',
    group_ids: ['G1'],
    airport_code: 'OTP',
    ready_at: '2026-11-01T12:00:00Z',
    received_at: '2026-11-01T11:00:00Z',
    description: form.description,
    affected_group_ids: ['G1'],
    affected_offer_ids: [],
  },
  ...overrides,
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('explicit structured arrival recovery', () => {
  it('keeps report-only free of recovery regardless of unfinished or incompatible recovery settings', () => {
    const settings: ArrivalRecoveryForm = {
      objective: 'balanced',
      modes: ['hotel'],
      maxProviderCalls: 'bad',
    };
    const report = arrivalReportSubmission(world, incident, form, settings, 'report');
    expect(report).toEqual({
      group_ids: ['G1'],
      airport_code: 'OTP',
      ready_at: '2026-11-01T12:00:00.000Z',
      description: form.description,
    });
    expect(report).not.toHaveProperty('recovery');
  });

  it('inherits exact reported scope and submits every selected supported service with an unchanged cap', () => {
    const settings = {
      ...emptyArrivalRecoveryForm(),
      modes: ['flight', 'bus'] as const,
      maxProviderCalls: '6',
    };
    const input = arrivalReportSubmission(
      world,
      incident,
      form,
      { ...settings, modes: [...settings.modes] },
      'recover',
    );
    expect(input.recovery).toEqual({
      objective: 'balanced',
      modes: ['flight', 'bus'],
      max_provider_calls: 6,
    });
    expect(input.recovery).not.toHaveProperty('group_ids');
    expect(input.recovery).not.toHaveProperty('instructions');
    expect(input.recovery).not.toHaveProperty('hotel_recovery');
    form.groupIds.push('G2');
    expect(input.group_ids).toEqual(['G1']);
    form.groupIds.pop();
  });

  it.each(['', '2', '27', '3.5', '-3', '1e1', ' 8'])(
    'rejects invalid requested recovery cap %j without applying report-only as a fallback',
    (maxProviderCalls) => {
      expect(() =>
        arrivalReportSubmission(
          world,
          incident,
          form,
          { ...emptyArrivalRecoveryForm(), maxProviderCalls },
          'recover',
        ),
      ).toThrow('call limit');
    },
  );

  it('requires fresh hotel setup instead of dropping hotels or consuming a saved profile', () => {
    const settings: ArrivalRecoveryForm = {
      ...emptyArrivalRecoveryForm(),
      modes: ['flight', 'hotel', 'bus'],
    };
    expect(() => arrivalReportSubmission(world, incident, form, settings, 'recover')).toThrow(
      'fresh stay dates',
    );
    expect(settings.modes).toEqual(['flight', 'hotel', 'bus']);
    expect(arrivalRecoveryIssue(settings, 1)).toContain('Saved hotel inputs will not be reused');
    expect(arrivalRecoveryIssue({ ...emptyArrivalRecoveryForm(), modes: [] }, 1)).toContain(
      'Choose the services',
    );
    expect(arrivalRecoveryIssue(emptyArrivalRecoveryForm(), 25)).toContain('1–24');
  });

  it('uses one versioned report request and retries its exact combined payload only on explicit recovery', async () => {
    const fetcher = vi
      .fn()
      .mockRejectedValueOnce(new TypeError('Connection lost'))
      .mockResolvedValueOnce(Response.json(receipt({ replayed: true })));
    vi.stubGlobal('fetch', fetcher);
    const input = arrivalReportSubmission(
      world,
      incident,
      form,
      emptyArrivalRecoveryForm(),
      'recover',
    );
    let unknown: TransportError | null = null;
    try {
      await api.reportArrival(world, incident.id, input);
    } catch (cause) {
      expect(cause).toBeInstanceOf(TransportError);
      unknown = cause as TransportError;
    }
    expect(fetcher).toHaveBeenCalledTimes(1);
    input.recovery!.modes.push('hotel');
    input.group_ids.push('G2');
    const result = await unknown!.retry<MutationResult>();
    expect(result.recovery_job?.id).toBe('JOB2');
    expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
      '/api/incidents/CASE1/arrival-report',
      '/api/incidents/CASE1/arrival-report',
    ]);
    expect(fetcher.mock.calls[1][1].body).toBe(fetcher.mock.calls[0][1].body);
    expect(JSON.parse(fetcher.mock.calls[1][1].body)).toMatchObject({
      expected_version: 4,
      group_ids: ['G1'],
      recovery: { modes: ['flight'], max_provider_calls: 8 },
    });
  });
});

describe('recorded successor and blocked outcomes', () => {
  it('discloses waiting, original usage and replay without claiming current completion', () => {
    const html = renderToStaticMarkup(
      <ArrivalRecoveryReceipt result={receipt({ replayed: true })} />,
    );
    expect(html).toContain('Arrival recorded · recovery waiting');
    expect(html).toContain('At recording, this request was waiting for earlier attempts to finish');
    expect(html).toContain('0 calls dispatched at recording · limit 8');
    expect(html).toContain('no new submission was made');
    expect(html).toContain('does not confirm a completed itinerary or booking');
  });

  it('shows a saved report with blocked recovery and does not manufacture a successor', () => {
    const result = receipt({
      recovery_job: null,
      recovery_blocked: {
        code: 'hotel_setup_required',
        explanation: 'Fresh hotel pickup inputs are required.',
      },
    });
    const html = renderToStaticMarkup(<ArrivalRecoveryReceipt result={result} />);
    expect(html).toContain('Arrival recorded · recovery not started');
    expect(html).toContain('Fresh hotel pickup inputs are required');
    expect(html).not.toContain('JOB2');
    expect(arrivalRecoveryReceipt(receipt({ recovery_job: null }))).toBeNull();
  });

  it.each([
    { recovery_job: { ...job, incident_id: 'OTHER' } },
    { recovery_job: { ...job, group_ids: ['G2'] } },
    { applied: false },
    {
      recovery_blocked: {
        code: 'recovery_queue_unavailable' as const,
        explanation: 'Queue unavailable.',
      },
    },
  ])(
    'does not announce successful handoff from contradictory or mismatched receipt %j',
    (overrides) => {
      expect(arrivalRecoveryReceipt(receipt(overrides))?.title).toBe(
        'Recovery receipt needs review',
      );
    },
  );

  it('retains a received successor across an older poll and keeps newer progress ahead of creation receipts', () => {
    const prior = {
      ...job,
      id: 'JOB1',
      created_at: '2026-11-01T10:00:00Z',
      updated_at: '2026-11-01T10:00:00Z',
    };
    expect(mergeRecoveryJobs([job], [prior]).some((item) => item.id === 'JOB2')).toBe(true);
    const running = {
      ...job,
      status: 'running' as const,
      stage: 'searching_flights' as const,
      calls_dispatched: 1,
      updated_at: '2026-11-01T11:01:00Z',
    };
    expect(mergeRecoveryJobs([running], [job])[0]).toEqual(running);
  });

  it('labels explicit service choices and ground references honestly without running searches on render', () => {
    const changed = vi.fn();
    const html = renderToStaticMarkup(
      <ArrivalRecoveryFields value={emptyArrivalRecoveryForm()} onChange={changed} />,
    );
    expect(html).toContain('Maximum recovery calls · 3–26');
    expect(html).toContain('Ground transport');
    expect(html).toContain('Hotels · fresh setup required');
    expect(html).toContain('fare references do not establish seats');
    expect(html).toContain('No interpretation call is added');
    expect(changed).not.toHaveBeenCalled();
  });
});

import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { api, ApiError, TransportError } from './api';
import {
  AirportRestrictionPanel,
  AirportRestrictionReceipt,
  CaseAirportRestrictions,
} from './AirportRestrictionPanel';
import { CaseSummary } from './RecoveryCases';
import { emptyArrivalRecoveryForm } from './arrivalRecoveryData';
import {
  activeCaseRestrictions,
  airportOperationsLabel,
  airportRestrictionRecoveryIssue,
  airportRestrictionRecoveryReceipt,
  airportRestrictionSubmission,
  emptyAirportRestrictionForm,
  restrictionRecoveryGroupIssue,
} from './airportRestrictionData';
import type { AirportRestrictionForm } from './airportRestrictionData';
import type { MutationResult, RecoveryIncident, RecoveryJob, World } from './types';

const incident: RecoveryIncident = {
  id: 'CASE1',
  source: 'operator_report',
  created_at: '2026-11-01T08:00:00Z',
  kind: 'destination_unavailable',
  description: 'Original destination constraint.',
  requested_flight_number: 'RO321',
  date_utc: '2026-11-01',
  flight_ref: { lookup_id: 'lookup', selection_id: 'leg' },
  unavailable_airport_code: 'OTP',
  group_ids: ['G1'],
  status: 'open',
  flight: null,
  flight_context_state: 'expired',
};
const world = {
  mode: 'live',
  scenario_id: 'OPS',
  version: 4,
  epoch: '2026-11-01T00:00:00Z',
  groups: [
    {
      id: 'G1',
      name: 'Reported party',
      incident_id: 'CASE1',
      size: 2,
      status: 'offered',
      origin: 'CRA',
      destination: 'FCO',
    },
  ],
  airports: [{ id: 'CRA', code: 'CRA', kind: 'airport', name: 'Craiova Airport' }],
  incidents: [incident],
  offers: [],
  reservations: [],
  resources: [],
} as unknown as World;
const form: AirportRestrictionForm = {
  airportCode: 'sbz',
  operations: ['arrivals', 'departures'],
  action: 'restrict',
  description: 'Operator reports airport restrictions for this case.',
  groupIds: ['G1'],
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
} as RecoveryJob;
const receipt = (overrides: Partial<MutationResult> = {}): MutationResult => ({
  world,
  applied: true,
  incident_id: 'CASE1',
  recovery_job: job,
  manual_review_required: false,
  confirmed_conflicts: [],
  report: {
    id: 'REPORT',
    type: 'airport_restriction',
    source: 'operator_report',
    incident_id: 'CASE1',
    airport_code: 'SBZ',
    operations: ['arrivals'],
    action: 'restrict',
    description: form.description,
    received_at: '2026-11-01T09:00:00Z',
    affected_offer_ids: ['O1'],
    affected_group_ids: ['G2'],
    recovery_group_ids: ['G1'],
  },
  ...overrides,
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('case-scoped active airport restrictions', () => {
  it('presents an unmigrated legacy airport in both directions with the original operator provenance', () => {
    const before = structuredClone(incident);
    expect(
      activeCaseRestrictions(incident).map((item) => [item.airport_code, item.operation]),
    ).toEqual([
      ['OTP', 'arrivals'],
      ['OTP', 'departures'],
    ]);
    const html = renderToStaticMarkup(<CaseAirportRestrictions incident={incident} />);
    expect(html).toContain('2 active directions');
    expect(html).toContain('OTP · Arrivals restricted');
    expect(html).toContain('OTP · Departures restricted');
    expect(html).toContain('Original incident constraint');
    expect(html).toContain('Operator report');
    expect(incident).toEqual(before);
  });

  it('preserves the original incident after a legacy clear and shows only remaining active directions', () => {
    const migrated: RecoveryIncident = {
      ...incident,
      unavailable_airport_code: null,
      original_unavailable_airport_code: 'OTP',
      airport_restrictions: [
        {
          airport_code: 'OTP',
          operation: 'departures',
          source: 'operator_report',
          report_id: 'LEGACY',
          received_at: incident.created_at,
          description: incident.description,
          inherited_from_legacy: true,
        },
      ],
      airport_restriction_reports: [
        {
          id: 'CLEAR',
          type: 'airport_restriction',
          source: 'operator_report',
          received_at: '2026-11-01T09:00:00Z',
          airport_code: 'OTP',
          operations: ['arrivals'],
          action: 'clear',
          description: 'Clear this case arrivals constraint.',
        },
      ],
    };
    const html = renderToStaticMarkup(<CaseSummary world={world} incident={migrated} />);
    expect(html).toContain('Original incident · Destination unavailable');
    expect(html).toContain(' · OTP');
    expect(html).toContain('1 active directions');
    expect(html).toContain('OTP · Departures restricted');
    expect(html).not.toContain('OTP · Arrivals restricted');
    expect(html).toContain('OTP · Arrivals · Case restriction cleared');
    expect(html).toContain('Flightradar24 observations are separate');
    const cleared = { ...migrated, airport_restrictions: [] };
    expect(activeCaseRestrictions(cleared)).toEqual([]);
    expect(renderToStaticMarkup(<CaseAirportRestrictions incident={cleared} />)).toContain(
      'No active airport restriction recorded for this case',
    );
  });
});

describe('explicit restriction and recalculation boundaries', () => {
  it('records a restriction without a passenger selection or unfinished recovery settings', () => {
    const input = airportRestrictionSubmission(
      world,
      incident,
      { ...form, groupIds: [] },
      { objective: 'balanced', modes: ['hotel'], maxProviderCalls: '' },
      'report',
    );
    expect(input).toEqual({
      airport_code: 'SBZ',
      operations: ['arrivals', 'departures'],
      action: 'restrict',
      description: form.description,
    });
    expect(input).not.toHaveProperty('recovery');
    expect(input).not.toHaveProperty('group_ids');
    expect(emptyAirportRestrictionForm().groupIds).toEqual([]);
  });
  it('submits the exact chosen directions, group scope, modes and unchanged call limit', () => {
    const currentForm = structuredClone(form);
    const settings = {
      ...emptyArrivalRecoveryForm(),
      modes: ['flight', 'bus'] as ('flight' | 'bus')[],
      objective: 'fastest' as const,
      maxProviderCalls: '6',
    };
    const before = structuredClone(world);
    const input = airportRestrictionSubmission(world, incident, currentForm, settings, 'recover');
    currentForm.groupIds.push('G2');
    currentForm.operations.pop();
    settings.modes.pop();
    expect(input.recovery).toEqual({
      group_ids: ['G1'],
      objective: 'fastest',
      modes: ['flight', 'bus'],
      max_provider_calls: 6,
    });
    expect(input.operations).toEqual(['arrivals', 'departures']);
    expect(input).not.toHaveProperty('ready_at');
    expect(input.recovery).not.toHaveProperty('instructions');
    expect(world).toEqual(before);
  });
  it.each([
    { operations: [] },
    { operations: ['arrivals', 'arrivals'] },
    { operations: ['landing'] },
    { airportCode: 'London' },
    { action: 'open' },
    { description: 'Unsafe\ncontrol' },
  ])('rejects invalid explicit report fields %j', (changes) => {
    expect(() =>
      airportRestrictionSubmission(
        world,
        incident,
        { ...form, ...changes } as AirportRestrictionForm,
        emptyArrivalRecoveryForm(),
        'report',
      ),
    ).toThrow();
  });
  it('rejects stale, duplicate, foreign or booked selected groups without narrowing the request', () => {
    for (const groupIds of [[], ['G1', 'G1'], ['G1', 'MISSING']])
      expect(() =>
        airportRestrictionSubmission(
          world,
          incident,
          { ...form, groupIds },
          emptyArrivalRecoveryForm(),
          'recover',
        ),
      ).toThrow();
    for (const changes of [{ incident_id: 'OTHER' }, { status: 'confirmed' }]) {
      const changed = { ...world, groups: [{ ...world.groups[0], ...changes }] } as World;
      expect(restrictionRecoveryGroupIssue(changed, incident, changed.groups[0])).not.toBeNull();
      expect(() =>
        airportRestrictionSubmission(
          changed,
          incident,
          form,
          emptyArrivalRecoveryForm(),
          'recover',
        ),
      ).toThrow();
      expect(
        airportRestrictionSubmission(changed, incident, form, emptyArrivalRecoveryForm(), 'report')
          .recovery,
      ).toBeUndefined();
    }
  });
  it('blocks hotel recalculation rather than dropping selected services or reusing a saved profile', () => {
    const settings = { ...emptyArrivalRecoveryForm(), modes: ['flight', 'hotel', 'bus'] as const };
    expect(() =>
      airportRestrictionSubmission(
        world,
        incident,
        form,
        { ...settings, modes: [...settings.modes] },
        'recover',
      ),
    ).toThrow('fresh stay');
    expect(
      airportRestrictionRecoveryIssue({ ...settings, modes: [...settings.modes] }, 1),
    ).toContain('Saved hotel inputs will not be reused');
    expect(airportRestrictionRecoveryIssue(emptyArrivalRecoveryForm(), 0)).toContain(
      'does not require a group selection',
    );
    expect(settings.modes).toEqual(['flight', 'hotel', 'bus']);
  });
  it.each(['2', '27', '3.5', '', '1e1'])(
    'rejects invalid call limit %j on a combined action',
    (maxProviderCalls) => {
      expect(() =>
        airportRestrictionSubmission(
          world,
          incident,
          form,
          { ...emptyArrivalRecoveryForm(), maxProviderCalls },
          'recover',
        ),
      ).toThrow('call limit');
    },
  );
  it('shows report-only, both explicit operation choices and optional exact-group recalculation', () => {
    const booked = { ...world, groups: [{ ...world.groups[0], status: 'confirmed' as const }] };
    const html = renderToStaticMarkup(
      <AirportRestrictionPanel
        world={booked}
        incident={incident}
        onApplied={() => {}}
        onRefresh={async () => {}}
        onMutationPending={() => {}}
      />,
    );
    expect(html).toContain('Arrivals');
    expect(html).toContain('Departures');
    expect(html).toContain('Clear case restriction');
    expect(html).toContain('Record restriction update only');
    expect(html).toContain('Record and recalculate selected groups');
    const combined = html.match(/<button[^>]*value="recover"[^>]*>/)?.[0];
    expect(combined).toContain('name="restriction_action"');
    expect(combined).toContain('disabled=""');
    expect(html).toContain('type="checkbox" disabled=""');
    expect(html).toContain('Recorded booking');
    expect(html).toContain('does not confirm the airport is open');
    expect(html).toContain('Passenger locations stay unchanged');
  });
});

describe('restriction mutation receipts and uncertainty', () => {
  it('retries only on explicit recovery with the same key, version, directions and selected scope', async () => {
    const fetcher = vi
      .fn()
      .mockRejectedValueOnce(new TypeError('Connection lost'))
      .mockResolvedValueOnce(Response.json(receipt({ replayed: true })));
    vi.stubGlobal('fetch', fetcher);
    const input = airportRestrictionSubmission(
      world,
      incident,
      form,
      emptyArrivalRecoveryForm(),
      'recover',
    );
    let unknown: TransportError | null = null;
    try {
      await api.reportAirportRestriction(world, 'CASE/1', input);
    } catch (cause) {
      expect(cause).toBeInstanceOf(TransportError);
      unknown = cause as TransportError;
    }
    expect(fetcher).toHaveBeenCalledTimes(1);
    input.recovery!.group_ids.push('G2');
    input.operations.pop();
    input.action = 'clear';
    const replay = await unknown!.retry<MutationResult>();
    expect(replay.replayed).toBe(true);
    expect(fetcher.mock.calls[1][0]).toBe('/api/incidents/CASE%2F1/airport-restrictions');
    expect(fetcher.mock.calls[1][1].body).toBe(fetcher.mock.calls[0][1].body);
    expect(JSON.parse(fetcher.mock.calls[1][1].body)).toMatchObject({
      expected_version: 4,
      action: 'restrict',
      operations: ['arrivals', 'departures'],
      recovery: { group_ids: ['G1'], max_provider_calls: 8 },
    });
  });
  it('keeps a definite conflict visible without retrying or recording fallback recovery', async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(
        new Response(
          JSON.stringify({ detail: 'Case changed; refresh before updating.', applied: false }),
          { status: 409 },
        ),
      );
    vi.stubGlobal('fetch', fetcher);
    await expect(
      api.reportAirportRestriction(
        world,
        incident.id,
        airportRestrictionSubmission(world, incident, form, emptyArrivalRecoveryForm(), 'report'),
      ),
    ).rejects.toBeInstanceOf(ApiError);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('distinguishes immutable waiting and blocked receipts from current itinerary completion', () => {
    const result = receipt({ replayed: true });
    expect(airportRestrictionRecoveryReceipt(result)?.title).toContain('recovery waiting');
    const html = renderToStaticMarkup(<AirportRestrictionReceipt result={result} />);
    expect(html).toContain('At recording');
    expect(html).toContain('0 calls dispatched at recording');
    expect(html).toContain('does not confirm an itinerary or booking');
    expect(html).toContain('Recovered original response');
    const blocked = receipt({
      recovery_job: null,
      recovery_blocked: {
        code: 'hotel_setup_required',
        explanation: 'Fresh hotel inputs required.',
      },
    });
    expect(airportRestrictionRecoveryReceipt(blocked)?.title).toContain('recovery not started');
    expect(renderToStaticMarkup(<AirportRestrictionReceipt result={blocked} />)).toContain(
      'Fresh hotel inputs required',
    );
  });
  it('validates recovery scope independently of groups whose pending proposals were affected', () => {
    expect(airportRestrictionRecoveryReceipt(receipt())?.warning).toBe(false);
    expect(
      airportRestrictionRecoveryReceipt(receipt({ recovery_job: { ...job, group_ids: ['G2'] } }))
        ?.title,
    ).toContain('needs review');
    expect(
      airportRestrictionRecoveryReceipt(receipt({ recovery_job: { ...job, incident_id: 'OTHER' } }))
        ?.title,
    ).toContain('needs review');
    expect(
      airportRestrictionRecoveryReceipt(
        receipt({
          recovery_blocked: { code: 'recovery_queue_unavailable', explanation: 'Queue blocked.' },
        }),
      )?.title,
    ).toContain('needs review');
  });
  it('shows proposal invalidation and confirmed booking conflicts without claiming cancellation', () => {
    const result = receipt({
      manual_review_required: true,
      confirmed_conflicts: [{ group_id: 'G2', offer_id: 'CONFIRMED1', resource_ids: ['F1'] }],
    });
    const html = renderToStaticMarkup(<AirportRestrictionReceipt result={result} />);
    expect(html).toContain('1 pending proposals invalidated');
    expect(html).toContain('Proposal references: O1');
    expect(html).toContain('Recorded bookings require operator review');
    expect(html).toContain('These bookings were not changed or cancelled');
    expect(html).toContain('Group G2 · offer CONFIRMED1 · services F1');
  });
  it('describes clearing only this case constraint and does not render an unconfirmed change as recorded', () => {
    const result = receipt({ recovery_job: null });
    if (result.report?.type !== 'airport_restriction') throw new Error('Invalid fixture');
    result.report.action = 'clear';
    result.report.operations = ['arrivals', 'departures'];
    const html = renderToStaticMarkup(<AirportRestrictionReceipt result={result} />);
    expect(html).toContain('Arrivals and departures · Case restriction cleared');
    expect(html).toContain('Airport opening and operating permission remain unconfirmed');
    expect(html).toContain('No recovery search was included');
    const rejected = renderToStaticMarkup(
      <AirportRestrictionReceipt result={{ ...result, applied: false }} />,
    );
    expect(rejected).toContain('does not confirm this case update');
    expect(rejected).not.toContain('Case restriction cleared');
    expect(airportOperationsLabel(['departures'])).toBe('Departures');
  });
});

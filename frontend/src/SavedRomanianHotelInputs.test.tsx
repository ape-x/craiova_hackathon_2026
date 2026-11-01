import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import SavedRomanianHotelInputs, {
  SavedRomanianHotelInputReview,
} from './SavedRomanianHotelInputs';
import {
  loadSavedRomanianHotelInput,
  romanianHotelProfileLoadIssue,
  romanianHotelProfileReason,
  romanianHotelProfileScope,
  RomanianHotelProfileReadGate,
  verifyRomanianHotelProfile,
  type RomanianHotelProfileLoadScope,
} from './savedRomanianHotelData';
import {
  emptyRomanianHotelRecoveryForm,
  romanianHotelRecoveryInput,
  romanianHotelScope,
} from './romanianHotelRecovery';
import { api, ApiError, TransportError } from './api';
import type { RomanianHotelRecoveryProfile, World } from './types';

const now = Date.parse('2030-11-01T12:00:00Z');
function operation(): World {
  return {
    mode: 'live',
    scenario_id: 'OPS',
    version: 4,
    epoch: '2030-11-01T00:00:00Z',
    currency: 'EUR',
    sim_time: 720,
    groups: ['G1', 'G2'].map((id) => ({
      id,
      name: `Party ${id}`,
      members: [`Adult ${id}A`, `Adult ${id}B`],
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
    })),
    airports: ['OTP', 'LHR'].map((id) => ({ id, code: id, kind: 'airport', name: id })),
    incidents: [{ id: 'CASE1', group_ids: ['G1', 'G2'] }],
    resources: [],
    offers: [],
    reservations: [],
  } as unknown as World;
}
function saved(): Extract<RomanianHotelRecoveryProfile, { configured: true }> {
  return {
    world_version: 4,
    incident_id: 'CASE1',
    group_id: 'G1',
    configured: true,
    source_job_id: 'RO-JOB1',
    saved_at: '2030-10-30T12:00:00Z',
    inputs: {
      source: 'transfero_direct_aeroport',
      hotel_id: 'lpce923',
      checkin_at: '2030-10-31T15:00:00+02:00',
      checkout_at: '2030-11-01T10:00:00+02:00',
      rooms: 1,
      minimum_rest_minutes: 480,
      suitcases: 2,
      handbags: 0,
      standard_baggage_confirmed: true,
      airport_processing_minutes: 180,
      hotel_connection_minutes: 20,
    },
  };
}
function args() {
  const world = operation();
  const scope: RomanianHotelProfileLoadScope = {
    world,
    incident: world.incidents![0],
    groupIds: ['G1'],
    packageSelected: true,
    incompatibleSetup: false,
    allowance: 8,
    draft: emptyRomanianHotelRecoveryForm(),
  };
  return {
    ...scope,
    profile: saved(),
    reviewedScope: romanianHotelProfileScope(scope),
    replacementConfirmed: false,
    disabled: false,
  };
}
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('saved Romanian hotel inputs', () => {
  it('loads exact historical instants, counts and margins for review without changing the party, modes, allowance or source data', () => {
    const input = args(),
      before = structuredClone(input);
    const form = loadSavedRomanianHotelInput(input);
    expect(form).toEqual({
      scope: romanianHotelScope(input.world, input.world.groups[0]),
      checkin_at: '2030-10-31T13:00',
      checkout_at: '2030-11-01T08:00',
      rooms: '1',
      minimum_rest_minutes: '480',
      suitcases: '2',
      handbags: '0',
      standard_baggage_confirmed: true,
      airport_processing_minutes: '180',
      hotel_connection_minutes: '20',
    });
    expect(input).toEqual(before);
    expect(() =>
      romanianHotelRecoveryInput(form, input.groupIds, input.world, input.allowance, now),
    ).toThrow('future');
    expect(form).not.toHaveProperty('price');
    expect(form).not.toHaveProperty('max_provider_calls');
    expect(form).not.toHaveProperty('modes');
  });
  it('requires explicit replacement of a nonempty matching draft and never overwrites an incompatible draft', () => {
    const input = args();
    input.draft = {
      ...input.draft,
      scope: romanianHotelScope(input.world, input.world.groups[0]),
      checkin_at: 'unfinished current draft',
      handbags: '1',
    };
    input.reviewedScope = romanianHotelProfileScope(input);
    const before = structuredClone(input.draft);
    expect(() => loadSavedRomanianHotelInput(input)).toThrow('Confirm replacing');
    expect(loadSavedRomanianHotelInput({ ...input, replacementConfirmed: true }).handbags).toBe(
      '0',
    );
    expect(input.draft).toEqual(before);
    input.draft.scope = 'previous party scope';
    input.reviewedScope = romanianHotelProfileScope(input);
    expect(() => loadSavedRomanianHotelInput({ ...input, replacementConfirmed: true })).toThrow(
      'Reset',
    );
  });
  it('rejects changed cases, selected groups, world versions, party identity, readiness, allowance or draft after review', () => {
    const mutations = [
      (input: ReturnType<typeof args>) => {
        input.world.version++;
      },
      (input: ReturnType<typeof args>) => {
        input.world.scenario_id = 'OTHER';
      },
      (input: ReturnType<typeof args>) => {
        input.incident = { ...input.incident, id: 'OTHER' };
      },
      (input: ReturnType<typeof args>) => {
        input.groupIds = ['G2'];
      },
      (input: ReturnType<typeof args>) => {
        input.world.groups[0].members = ['Different adult', 'Another adult'];
      },
      (input: ReturnType<typeof args>) => {
        input.world.groups[0].ready_at = 721;
      },
      (input: ReturnType<typeof args>) => {
        input.allowance = 9;
      },
      (input: ReturnType<typeof args>) => {
        input.draft.rooms = '2';
      },
    ];
    for (const mutate of mutations) {
      const input = args();
      mutate(input);
      expect(() => loadSavedRomanianHotelInput(input)).toThrow('changed');
    }
    for (const profile of [
      { ...saved(), world_version: 5 },
      { ...saved(), incident_id: 'OTHER' },
      { ...saved(), group_id: 'G2' },
    ])
      expect(() => loadSavedRomanianHotelInput({ ...args(), profile })).toThrow(
        'different operation version',
      );
  });
  it('keeps package, current-party, scope and pending-mutation gates strict while allowing old dates to be reviewed', () => {
    for (const changes of [
      { packageSelected: false },
      { incompatibleSetup: true },
      { groupIds: [] },
      { groupIds: ['G1', 'G2'] },
    ]) {
      const input = { ...args(), ...changes };
      expect(romanianHotelProfileLoadIssue(input)).not.toBeNull();
      input.reviewedScope = romanianHotelProfileScope(input);
      expect(() => loadSavedRomanianHotelInput(input)).toThrow();
    }
    for (const changes of [
      { origin: 'LHR' },
      { adult_count: null },
      { status: 'confirmed' as const },
      { accessible: true },
    ]) {
      const input = args();
      Object.assign(input.world.groups[0], changes);
      input.reviewedScope = romanianHotelProfileScope(input);
      expect(() => loadSavedRomanianHotelInput(input)).toThrow();
    }
    expect(() => loadSavedRomanianHotelInput({ ...args(), disabled: true })).toThrow(
      'pending operation',
    );
    expect(() =>
      loadSavedRomanianHotelInput({
        ...args(),
        profile: { ...saved(), inputs: { ...saved().inputs, suitcases: 3 } },
      }),
    ).toThrow('current party');
    const low = args();
    low.allowance = 3;
    low.reviewedScope = romanianHotelProfileScope(low);
    expect(loadSavedRomanianHotelInput(low).rooms).toBe('1');
    expect(low.allowance).toBe(3);
  });
  it('rejects malformed or mixed-profile receipts, ambiguous timestamps and invented/changed saved fields', () => {
    const bad = [
      null,
      [],
      {},
      { ...saved(), world_version: '4' },
      { ...saved(), configured: 1 },
      { ...saved(), configured: false, reason: 'missing' },
      { ...saved(), source_job_id: '' },
      { ...saved(), saved_at: '2030-10-30T12:00' },
      ...[
        { source: 'uk_apl' },
        { hotel_id: 'different' },
        { checkin_at: '2030-10-31T15:00' },
        { checkout_at: '2030-11-01T10:00:30+02:00' },
        { rooms: 1.5 },
        { minimum_rest_minutes: 9999 },
        { suitcases: -1 },
        { handbags: '0' },
        { standard_baggage_confirmed: false },
        { airport_processing_minutes: 179 },
        { hotel_connection_minutes: 19 },
        { fare: 'old price' },
      ].map((change) => ({ ...saved(), inputs: { ...saved().inputs, ...change } })),
    ];
    for (const value of bad) expect(() => verifyRomanianHotelProfile(value)).toThrow();
    expect(verifyRomanianHotelProfile(saved())).toEqual(saved());
    expect(
      verifyRomanianHotelProfile({
        world_version: 4,
        incident_id: 'CASE1',
        group_id: 'G1',
        configured: false,
        reason: 'missing',
      }),
    ).toMatchObject({ configured: false });
  });
  it('does not accept an earlier response after a later read, changed scope or disposal', () => {
    const gate = new RomanianHotelProfileReadGate(),
      scope = args().reviewedScope;
    const first = gate.begin(),
      second = gate.begin();
    expect(gate.accepts(first, scope, scope)).toBe(false);
    expect(gate.accepts(second, scope, scope)).toBe(true);
    expect(gate.accepts(second, scope, 'changed')).toBe(false);
    gate.invalidate();
    expect(gate.accepts(second, scope, scope)).toBe(false);
  });
  it('uses one authenticated GET only, encodes the exact case/group and never automatically retries failures', async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(saved()), { status: 200 }))
      .mockRejectedValueOnce(new TypeError('offline'));
    vi.stubGlobal('fetch', fetcher);
    expect(await api.romanianHotelRecoveryProfile('CASE/1', 'G 1')).toEqual(saved());
    expect(fetcher.mock.calls[0][0]).toBe(
      '/api/incidents/CASE%2F1/groups/G%201/romanian-hotel-recovery-profile',
    );
    expect(fetcher.mock.calls[0][1]).toMatchObject({
      method: 'GET',
      credentials: 'same-origin',
      body: undefined,
    });
    await expect(api.romanianHotelRecoveryProfile('CASE1', 'G1')).rejects.toBeInstanceOf(
      TransportError,
    );
    expect(fetcher).toHaveBeenCalledTimes(2);
    fetcher.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'Case linkage changed' }), { status: 409 }),
    );
    await expect(api.romanianHotelRecoveryProfile('CASE1', 'G1')).rejects.toBeInstanceOf(ApiError);
    expect(fetcher).toHaveBeenCalledTimes(3);
  });
  it('renders historical source inputs and replacement consent with non-submit buttons, without fetching on mount', () => {
    vi.spyOn(Date, 'now').mockReturnValue(now);
    const fetcher = vi.fn(),
      change = vi.fn();
    vi.stubGlobal('fetch', fetcher);
    const input = args();
    input.draft.rooms = '2';
    input.draft.scope = romanianHotelScope(input.world, input.world.groups[0]);
    const html = renderToStaticMarkup(
      <SavedRomanianHotelInputReview
        {...input}
        profile={saved()}
        replacementConfirmed={false}
        onReplacementChange={() => {}}
        onLoad={() => {}}
      />,
    );
    for (const text of [
      'RO-JOB1',
      '2030',
      '1 rooms',
      '2 hold bags',
      '0 hand bags',
      'standard baggage declaration saved',
      'not current fares',
      'future',
      'Replace the current Romania inputs',
      'type="button"',
      'disabled',
    ])
      expect(html).toContain(text);
    const mounted = renderToStaticMarkup(
      <SavedRomanianHotelInputs {...args()} onChange={change} />,
    );
    expect(mounted).toContain('Review saved Romania inputs');
    expect(mounted).toContain('Past dates stay exact');
    expect(fetcher).not.toHaveBeenCalled();
    expect(change).not.toHaveBeenCalled();
    const locked = renderToStaticMarkup(
      <SavedRomanianHotelInputReview
        {...args()}
        disabled={true}
        profile={saved()}
        replacementConfirmed={true}
        onReplacementChange={() => {}}
        onLoad={() => {}}
      />,
    );
    expect(locked).toMatch(/<button[^>]*disabled/);
  });
  it.each(['missing', 'stale', 'invalid', 'scope', 'future_code'])(
    'explains the %s state without offering to load',
    (reason) => {
      const profile: RomanianHotelRecoveryProfile = {
        world_version: 4,
        incident_id: 'CASE1',
        group_id: 'G1',
        configured: false,
        reason,
      };
      const html = renderToStaticMarkup(
        <SavedRomanianHotelInputReview
          {...args()}
          profile={profile}
          replacementConfirmed={false}
          onReplacementChange={() => {}}
          onLoad={() => {}}
        />,
      );
      expect(html).toContain(romanianHotelProfileReason(reason));
      expect(html).not.toContain('<button');
    },
  );
});

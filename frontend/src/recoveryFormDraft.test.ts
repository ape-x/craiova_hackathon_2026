import { afterEach, describe, expect, it, vi } from 'vitest';
import { emptyHotelRecoveryForm } from './hotelRecovery';
import { cloneRecoveryFormDraft, restoreRecoveryFormDraft } from './recoveryFormDraft';
import type { RecoveryFormDraft } from './recoveryFormDraft';
import type { RecoveryDataContext } from './recoveryDataContext';
import type { RecoveryIncident, World } from './types';

const fixture = (): World =>
  ({
    mode: 'live',
    scenario_id: 'OPS',
    version: 5,
    epoch: '2026-11-01T00:00:00Z',
    groups: ['G1', 'G2'].map((id) => ({
      id,
      incident_id: 'CASE-1',
      status: 'waiting',
      origin: 'OTP',
      destination: 'CRA',
      ready_at: 600,
    })),
    incidents: [{ id: 'CASE-1', group_ids: ['G1', 'G2'], flight_context_state: 'expired' }],
    offers: [],
    reservations: [],
    resources: [],
  }) as unknown as World;

function draft(limit = '3'): RecoveryFormDraft {
  return {
    groupIds: ['G1'],
    objective: 'lowest_cost',
    modes: ['bus', 'flight', 'hotel'],
    limit,
    instructions: '  Keep this party together.\nReview all original limits.  ',
    hotelMode: true,
    hotelFields: {
      ...emptyHotelRecoveryForm(),
      checkin_at: '2026-11-01T12:00',
      checkout_at: 'not yet decided',
      rooms: '02',
      minimum_rest_minutes: 'not a number',
      initial_point: {
        label: 'Terminal meeting point',
        latitude: '51.4700',
        longitude: '-0.4543',
        confirmed: true,
      },
      pickup_at: '2026-11-02T04:05',
      suitcases: '0',
      onward_airport_code: 'LHR',
      airport_dropoff: {
        label: 'Declared drop-off',
        latitude: 'invalid latitude',
        longitude: '',
        confirmed: false,
      },
      airport_processing_minutes: '090',
    },
    airportTransfers: [
      {
        group_id: 'G1',
        pickup_at: '2026-11-02T12:00+02:00',
        suitcases: '0',
        handbags: '',
        scope: 'unchanged-original-physical-scope',
      },
    ],
    afterFlightTransfer: {
      group_id: 'G1',
      scope: 'unchanged-after-flight-scope',
      pickup_window_start: '2026-11-03T17:35',
      pickup_window_end: 'unfinished end',
      airport_processing_minutes: '25',
      suitcases: '0',
      handbags: '2',
      max_inbound_options: '1',
    },
  };
}

function context(form = draft()): RecoveryDataContext {
  return {
    scenarioId: 'OPS',
    incidentId: 'CASE-1',
    groupIds: [...form.groupIds],
    searchDraft: cloneRecoveryFormDraft(form),
  };
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('complete recovery form preservation across operation data', () => {
  it.each(['3', '9', '03', '', 'not a number'])(
    'preserves the exact %j call limit and every other editable field',
    (limit) => {
      const world = fixture();
      const form = draft(limit);
      const saved = context(form);
      const before = structuredClone(world);
      const result = restoreRecoveryFormDraft(world, world.incidents![0], saved);
      expect(result).toEqual({ draft: form, issue: null });
      expect(result.draft?.limit).toBe(limit);
      expect(result.draft?.modes).toEqual(['bus', 'flight', 'hotel']);
      expect(result.draft?.hotelMode).toBe(true);
      expect(result.draft?.airportTransfers).toEqual(form.airportTransfers);
      expect(result.draft?.afterFlightTransfer).toEqual(form.afterFlightTransfer);
      expect(world).toEqual(before);
    },
  );

  it('deeply detaches both the departure snapshot and the restored form', () => {
    const world = fixture();
    const original = draft();
    const saved = context(original);
    original.limit = '26';
    original.modes.push('flight');
    original.hotelFields.initial_point.label = 'Edited later';
    original.airportTransfers[0].suitcases = '7';
    original.afterFlightTransfer!.max_inbound_options = '2';
    const restored = restoreRecoveryFormDraft(world, world.incidents![0], saved).draft!;
    expect(restored).toEqual(draft());
    restored.groupIds.push('G2');
    restored.hotelFields.initial_point.confirmed = false;
    restored.hotelFields.airport_dropoff.longitude = '9';
    restored.airportTransfers[0].pickup_at = 'later';
    restored.afterFlightTransfer!.pickup_window_start = 'shifted';
    expect(saved.searchDraft).toEqual(draft());
    expect(saved.groupIds).toEqual(['G1']);
  });

  it('does not shift the clock, refresh physical fingerprints or reinterpret retained invalid service setup', () => {
    const world = fixture();
    const saved = context();
    world.version += 1;
    world.groups[0].ready_at = 800;
    vi.spyOn(Date, 'now').mockImplementation(() => {
      throw new Error('Draft restoration must not read the clock');
    });
    const fetch = vi.fn(() => {
      throw new Error('Draft restoration must not source data');
    });
    vi.stubGlobal('fetch', fetch);
    expect(restoreRecoveryFormDraft(world, world.incidents![0], saved)).toEqual({
      draft: draft(),
      issue: null,
    });
    expect(fetch).not.toHaveBeenCalled();
  });

  it('preserves empty selection, disabled workflows and empty strings without inventing defaults', () => {
    const world = fixture();
    const form: RecoveryFormDraft = {
      groupIds: [],
      objective: 'balanced',
      modes: [],
      limit: '',
      instructions: '',
      hotelMode: false,
      hotelFields: emptyHotelRecoveryForm(),
      airportTransfers: [],
      afterFlightTransfer: null,
    };
    expect(restoreRecoveryFormDraft(world, world.incidents![0], context(form))).toEqual({
      draft: form,
      issue: null,
    });
  });

  it.each([
    ['different ordered scope', ['G1', 'G2'], ['G2', 'G1']],
    ['missing requested group', ['G1', 'G2'], ['G1']],
    ['extra draft group', ['G1'], ['G1', 'G2']],
    ['normalized-looking identifier', ['G1'], ['g1']],
  ])('rejects %s without a subset or replacement draft', (_, selected, draftGroups) => {
    const world = fixture();
    const saved = context();
    saved.groupIds = selected;
    saved.searchDraft!.groupIds = draftGroups;
    expect(restoreRecoveryFormDraft(world, world.incidents![0], saved)).toEqual({
      draft: null,
      issue: expect.stringContaining('selection differ'),
    });
  });

  it.each(['scenario', 'case', 'missing', 'foreign', 'confirmed', 'duplicate'] as const)(
    'refuses the entire saved form when the current scope is %s',
    (change) => {
      const world = fixture();
      const saved = context({ ...draft(), groupIds: ['G1', 'G2'] });
      if (change === 'scenario') world.scenario_id = 'OTHER';
      else if (change === 'case') saved.incidentId = 'OTHER-CASE';
      else if (change === 'missing') world.groups.pop();
      else if (change === 'foreign') world.groups[1].incident_id = 'OTHER-CASE';
      else if (change === 'confirmed') world.groups[1].status = 'confirmed';
      else saved.groupIds = ['G1', 'G1'];
      expect(restoreRecoveryFormDraft(world, world.incidents![0], saved)).toEqual({
        draft: null,
        issue: expect.any(String),
      });
      expect(saved.searchDraft!.limit).toBe('3');
    },
  );

  it('cannot move the saved form to a different requested case component', () => {
    const world = fixture();
    const other = { id: 'CASE-2', group_ids: [] } as unknown as RecoveryIncident;
    expect(restoreRecoveryFormDraft(world, other, context())).toEqual({
      draft: null,
      issue: expect.any(String),
    });
  });

  it('leaves ordinary case navigation without a saved draft to the existing selection flow', () => {
    const world = fixture();
    expect(restoreRecoveryFormDraft(world, world.incidents![0])).toEqual({
      draft: null,
      issue: null,
    });
    expect(
      restoreRecoveryFormDraft(world, world.incidents![0], {
        scenarioId: 'OPS',
        incidentId: 'CASE-1',
        groupIds: ['G1'],
      }),
    ).toEqual({ draft: null, issue: null });
  });
});

import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import SavedAfterFlightInputs, { SavedAfterFlightInputOptions } from './SavedAfterFlightInputs';
import {
  AfterFlightProfileReadGate,
  afterFlightProfileLoadIssue,
  afterFlightProfileReason,
  afterFlightProfileScope,
  loadSavedAfterFlightInput,
  verifyAfterFlightProfiles,
  type AfterFlightProfileLoadScope,
} from './savedAfterFlightData';
import { afterFlightTransferInput, emptyAfterFlightTransfer } from './afterFlightTransfer';
import { airportTransferScope } from './airportTransferRecovery';
import { api, TransportError } from './api';
import type { AfterFlightTransferProfiles, RecoveryIncident, World } from './types';

const now = Date.parse('2030-11-01T12:00:00Z');
const incident = { id: 'CASE1', group_ids: ['G1', 'G2'] } as RecoveryIncident;
function operation(): World {
  return {
    mode: 'live',
    scenario_id: 'OP1',
    version: 4,
    epoch: '2030-11-01T00:00:00Z',
    sim_time: 720,
    groups: ['G1', 'G2'].map((id) => ({
      id,
      incident_id: 'CASE1',
      name: `Party ${id}`,
      size: 2,
      adult_count: 2,
      origin: 'LHR',
      destination: 'CRA',
      ready_at: 600,
      deadline: 5760,
      accessible: false,
      status: 'affected',
      budget: 1000,
      original_flight_id: 'FL1',
    })),
    offers: [],
    reservations: [],
    resources: [],
    airports: ['LHR', 'OTP', 'CRA'].map((id) => ({ id, code: id, kind: 'airport', name: id })),
    decisions: [],
    observations: [],
    events: [],
    provider_status: {},
    stats: {},
  } as unknown as World;
}
function saved(): AfterFlightTransferProfiles {
  return {
    world_version: 4,
    profiles: [
      {
        group_id: 'G1',
        configured: true,
        inputs: {
          group_id: 'G1',
          via_airport: 'OTP',
          destination_airport: 'CRA',
          pickup_window_start: '2030-10-31T16:00:00+02:00',
          pickup_window_end: '2030-10-31T20:00:00+02:00',
          airport_processing_minutes: 90,
          suitcases: 2,
          handbags: 0,
          max_inbound_options: 2,
        },
        source_job_id: 'AF-JOB1',
        saved_at: '2030-10-30T12:30:00Z',
      },
      { group_id: 'G2', configured: false, reason: 'missing' },
    ],
  };
}
function loadArguments() {
  const args: AfterFlightProfileLoadScope = {
    world: operation(),
    incident,
    groupIds: ['G1'],
    modes: ['flight', 'bus'],
    incompatibleSetup: false,
    draft: null,
  };
  return {
    ...args,
    profile: saved().profiles[0],
    responseVersion: 4,
    reviewedScope: afterFlightProfileScope(
      args.world,
      args.incident,
      args.groupIds,
      args.modes,
      args.incompatibleSetup,
    ),
  };
}
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('separately saved after-flight setup', () => {
  it('copies both exact instants and explicit inputs without advancing dates, searching, or changing the operation', () => {
    const args = loadArguments(),
      before = structuredClone(args);
    const draft = loadSavedAfterFlightInput(args);
    expect(draft).toEqual({
      group_id: 'G1',
      scope: airportTransferScope(args.world, ['G1']),
      pickup_window_start: '2030-10-31T14:00',
      pickup_window_end: '2030-10-31T18:00',
      airport_processing_minutes: '90',
      suitcases: '2',
      handbags: '0',
      max_inbound_options: '2',
    });
    expect(Date.parse(`${draft.pickup_window_start}:00Z`)).toBe(
      Date.parse('2030-10-31T16:00:00+02:00'),
    );
    expect(Date.parse(`${draft.pickup_window_end}:00Z`)).toBe(
      Date.parse('2030-10-31T20:00:00+02:00'),
    );
    expect(args).toEqual(before);
    // Copying for review is allowed, but a subsequent ordinary submit rejects this past window.
    expect(() =>
      afterFlightTransferInput(draft, args.world, args.groupIds, args.modes, 8, false, '', now),
    ).toThrow('future pickup window');
  });
  it('preserves the selected cap and alternative upper bound; no silent service selection or cap increase', () => {
    const args = loadArguments();
    if (!args.profile.configured) throw new Error('fixture');
    args.profile.inputs.pickup_window_start = '2030-11-02T14:00:00Z';
    args.profile.inputs.pickup_window_end = '2030-11-02T18:00:00Z';
    const draft = loadSavedAfterFlightInput(args);
    expect(() =>
      afterFlightTransferInput(draft, args.world, args.groupIds, args.modes, 5, false, '', now),
    ).toThrow('at least 6 calls');
    expect(
      afterFlightTransferInput(draft, args.world, args.groupIds, args.modes, 6, false, '', now)
        ?.max_inbound_options,
    ).toBe(2);
    args.modes = ['bus'];
    args.reviewedScope = afterFlightProfileScope(
      args.world,
      args.incident,
      args.groupIds,
      args.modes,
      false,
    );
    expect(() => loadSavedAfterFlightInput(args)).toThrow('Select Flights and Ground transport');
    expect(args.modes).toEqual(['bus']);
  });
  it('rechecks current readiness/deadline after loading instead of silently adjusting the saved window', () => {
    for (const change of [{ ready_at: 2400 }, { deadline: 2000 }]) {
      const args = loadArguments();
      if (!args.profile.configured) throw new Error('fixture');
      args.profile.inputs.pickup_window_start = '2030-11-02T14:00:00Z';
      args.profile.inputs.pickup_window_end = '2030-11-02T18:00:00Z';
      Object.assign(args.world.groups[0], change);
      args.reviewedScope = afterFlightProfileScope(
        args.world,
        args.incident,
        args.groupIds,
        args.modes,
        false,
      );
      const draft = loadSavedAfterFlightInput(args);
      expect(draft.pickup_window_start).toBe('2030-11-02T14:00');
      expect(() =>
        afterFlightTransferInput(draft, args.world, args.groupIds, args.modes, 8, false, '', now),
      ).toThrow('recorded readiness');
    }
  });
  it('allows explicit replacement of a compatible draft but never mutates it', () => {
    const args = loadArguments();
    args.draft = {
      ...emptyAfterFlightTransfer('G1', airportTransferScope(args.world, ['G1'])),
      suitcases: '7',
      handbags: '7',
    };
    const before = structuredClone(args.draft),
      replacement = loadSavedAfterFlightInput(args);
    expect(replacement.suitcases).toBe('2');
    expect(args.draft).toEqual(before);
  });
  it('requires incompatible initial/hotel and stale or differently scoped after-flight setup to be removed explicitly', () => {
    for (const mutate of [
      (args: ReturnType<typeof loadArguments>) => {
        args.incompatibleSetup = true;
      },
      (args: ReturnType<typeof loadArguments>) => {
        args.modes.push('hotel');
      },
      (args: ReturnType<typeof loadArguments>) => {
        args.draft = emptyAfterFlightTransfer('OTHER', airportTransferScope(args.world, ['G1']));
      },
      (args: ReturnType<typeof loadArguments>) => {
        args.draft = emptyAfterFlightTransfer('G1', 'old-readiness');
      },
    ]) {
      const args = loadArguments();
      mutate(args);
      args.reviewedScope = afterFlightProfileScope(
        args.world,
        args.incident,
        args.groupIds,
        args.modes,
        args.incompatibleSetup,
      );
      const before = structuredClone(args);
      expect(afterFlightProfileLoadIssue(args)).toMatch(/Remove|reset/);
      expect(() => loadSavedAfterFlightInput(args)).toThrow();
      expect(args).toEqual(before);
    }
  });
  it('rejects obsolete response versions and case, scenario, selection, readiness or service changes', () => {
    for (const mutate of [
      (args: ReturnType<typeof loadArguments>) => {
        args.responseVersion = 3;
      },
      (args: ReturnType<typeof loadArguments>) => {
        args.world.version++;
      },
      (args: ReturnType<typeof loadArguments>) => {
        args.world.scenario_id = 'OTHER';
      },
      (args: ReturnType<typeof loadArguments>) => {
        args.world.groups[0].ready_at = 800;
      },
      (args: ReturnType<typeof loadArguments>) => {
        args.incident = { ...incident, id: 'OTHER' };
      },
      (args: ReturnType<typeof loadArguments>) => {
        args.groupIds = ['G2'];
      },
      (args: ReturnType<typeof loadArguments>) => {
        args.modes = ['flight'];
      },
      (args: ReturnType<typeof loadArguments>) => {
        args.incompatibleSetup = true;
      },
    ]) {
      const args = loadArguments();
      mutate(args);
      expect(() => loadSavedAfterFlightInput(args)).toThrow('changed');
    }
  });
  it('never adds an unselected/unlinked group, changes its origin, or replaces the initial workflow', () => {
    for (const mutate of [
      (args: ReturnType<typeof loadArguments>) => {
        args.groupIds = ['G2'];
      },
      (args: ReturnType<typeof loadArguments>) => {
        args.groupIds = ['G1', 'G2'];
      },
      (args: ReturnType<typeof loadArguments>) => {
        args.incident = { ...incident, group_ids: ['G2'] };
      },
      (args: ReturnType<typeof loadArguments>) => {
        args.world.groups[0].incident_id = 'OTHER';
      },
      (args: ReturnType<typeof loadArguments>) => {
        args.world.groups[0].origin = 'OTP';
      },
      (args: ReturnType<typeof loadArguments>) => {
        args.world.groups[0].status = 'confirmed';
      },
    ]) {
      const args = loadArguments();
      mutate(args);
      args.reviewedScope = afterFlightProfileScope(
        args.world,
        args.incident,
        args.groupIds,
        args.modes,
        args.incompatibleSetup,
      );
      expect(() => loadSavedAfterFlightInput(args)).toThrow();
    }
  });
  it.each([
    { group_id: 'OTHER' },
    { via_airport: 'LHR' },
    { destination_airport: 'OTP' },
    { suitcases: null },
    { handbags: '0' },
    { handbags: 8 },
    { airport_processing_minutes: 0 },
    { airport_processing_minutes: 721 },
    { airport_processing_minutes: 1.5 },
    { max_inbound_options: 0 },
    { max_inbound_options: '2' },
    { pickup_window_start: '2030-10-31T14:00' },
    { pickup_window_start: '2030-02-30T14:00:00Z' },
    { pickup_window_start: '2030-10-31T14:00:01Z' },
    { pickup_window_end: '2030-10-31T13:59:00Z' },
  ])('does not repair malformed or incomplete saved inputs %j', (change) => {
    const response = saved();
    if (response.profiles[0].configured) Object.assign(response.profiles[0].inputs, change);
    expect(() => verifyAfterFlightProfiles(response)).toThrow();
  });
  it('rejects an initial CAB profile, duplicate groups, missing provenance and unknown configuration state', () => {
    const response = saved();
    if (!response.profiles[0].configured) throw new Error('fixture');
    const initial = {
      group_id: 'G1',
      destination_airport: 'CRA',
      pickup_at: '2030-11-02T14:00:00Z',
      suitcases: 2,
      handbags: 0,
    };
    expect(() =>
      verifyAfterFlightProfiles({
        ...response,
        profiles: [{ ...response.profiles[0], inputs: initial }],
      } as unknown as AfterFlightTransferProfiles),
    ).toThrow();
    expect(() =>
      verifyAfterFlightProfiles({
        ...response,
        profiles: [response.profiles[0], response.profiles[0]],
      }),
    ).toThrow();
    for (const change of [{ source_job_id: '' }, { saved_at: 'invalid' }, { configured: null }]) {
      expect(() =>
        verifyAfterFlightProfiles({
          ...response,
          profiles: [{ ...response.profiles[0], ...change }],
        } as unknown as AfterFlightTransferProfiles),
      ).toThrow();
    }
  });
  it('discards late reads after newer reads, selection changes and unmount, even when the old group returns', async () => {
    const args = loadArguments(),
      gate = new AfterFlightProfileReadGate(),
      first = gate.begin(),
      second = gate.begin();
    await Promise.resolve(saved());
    expect(gate.accepts(first, args.reviewedScope, args.reviewedScope)).toBe(false);
    expect(gate.accepts(second, args.reviewedScope, args.reviewedScope)).toBe(true);
    expect(
      gate.accepts(
        second,
        args.reviewedScope,
        afterFlightProfileScope(args.world, incident, ['G2'], args.modes, false),
      ),
    ).toBe(false);
    gate.invalidate();
    expect(gate.accepts(second, args.reviewedScope, args.reviewedScope)).toBe(false);
  });
  it('uses the separate authenticated read endpoint without a source call, mutation or automatic retry', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(saved()), { status: 200 }))
      .mockRejectedValueOnce(new Error('offline'));
    vi.stubGlobal('fetch', fetchMock);
    const controller = new AbortController();
    expect(await api.afterFlightTransferProfiles('CASE/1', controller.signal)).toEqual(saved());
    expect(fetchMock.mock.calls[0][0]).toBe(
      '/api/incidents/CASE%2F1/after-flight-transfer-profiles',
    );
    expect(fetchMock.mock.calls[0][1]).toMatchObject({
      method: 'GET',
      credentials: 'same-origin',
      body: undefined,
      signal: controller.signal,
    });
    await expect(api.afterFlightTransferProfiles('CASE1')).rejects.toBeInstanceOf(TransportError);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
  it('shows historical window, provenance, review errors and explicit replacement with a non-submit control', () => {
    vi.useFakeTimers();
    vi.setSystemTime(now);
    const args = loadArguments();
    args.draft = emptyAfterFlightTransfer('G1', airportTransferScope(args.world, ['G1']));
    const html = renderToStaticMarkup(
      <SavedAfterFlightInputOptions
        {...args}
        allowance={8}
        response={saved()}
        disabled={false}
        onLoad={() => {}}
      />,
    );
    for (const text of [
      'Party G1',
      '31 Oct 2030, 14:00:00 UTC',
      '31 Oct 2030, 18:00:00 UTC',
      '90',
      '2',
      '0',
      'AF-JOB1',
      'Replace this group',
      'Review before searching:',
      'future pickup window',
      'do not reuse a flight, fare or reservation',
    ])
      expect(html).toContain(text);
    expect(html).toContain('type="button"');
    expect(html).not.toContain('type="submit"');
    expect(html).not.toContain('disabled=""'); // Past windows can be copied for editing, never silently shifted.
    const blocked = renderToStaticMarkup(
      <SavedAfterFlightInputOptions
        {...args}
        incompatibleSetup
        allowance={8}
        response={saved()}
        disabled={false}
        onLoad={() => {}}
      />,
    );
    expect(blocked).toContain('Remove hotel and initial airport-transfer setup explicitly');
    expect(blocked).toContain('disabled=""');
    const intro = renderToStaticMarkup(
      <SavedAfterFlightInputs {...args} allowance={8} disabled={false} onChange={() => {}} />,
    );
    expect(intro).toContain('Past dates stay exact');
    expect(intro).toContain('without searching or changing your call limit');
  });
  it.each(['missing', 'stale', 'invalid', 'scope', 'unknown_future_code'])(
    'shows useful unavailable-configuration guidance for %s',
    (reason) => {
      const args = loadArguments(),
        profile = { group_id: 'G1', configured: false as const, reason };
      const html = renderToStaticMarkup(
        <SavedAfterFlightInputOptions
          {...args}
          allowance={8}
          response={{ world_version: 4, profiles: [profile] }}
          disabled={false}
          onLoad={() => {}}
        />,
      );
      expect(html).toContain(afterFlightProfileReason(reason));
      expect(html).not.toContain('<button');
      expect(() => loadSavedAfterFlightInput({ ...args, profile })).toThrow(
        afterFlightProfileReason(reason),
      );
    },
  );
});

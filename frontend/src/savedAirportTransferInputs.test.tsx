import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import {
  airportTransferProfileScope,
  AirportTransferProfileReadGate,
  loadSavedAirportTransferInput,
  verifyAirportTransferProfiles,
} from './savedAirportTransferData';
import SavedAirportTransferInputs, {
  SavedAirportTransferInputOptions,
} from './SavedAirportTransferInputs';
import { airportTransferInputs, emptyAirportTransferDraft } from './airportTransferRecovery';
import { api, TransportError } from './api';
import Inspector from './Inspector';
import type { AirportTransferProfiles, RecoveryIncident, World } from './types';

const incident = { id: 'CASE1', group_ids: ['G1', 'G2'] } as RecoveryIncident;
function operation(): World {
  return {
    mode: 'live',
    scenario_id: 'OP1',
    version: 4,
    epoch: '2030-11-01T00:00:00Z',
    groups: ['G1', 'G2'].map((id) => ({
      id,
      incident_id: 'CASE1',
      name: `Group ${id}`,
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
    observations: [],
    events: [],
    provider_status: {},
    stats: {},
  } as unknown as World;
}
function saved(): AirportTransferProfiles {
  return {
    world_version: 4,
    profiles: [
      {
        group_id: 'G1',
        configured: true,
        inputs: {
          group_id: 'G1',
          destination_airport: 'CRA',
          pickup_at: '2030-10-31T16:00:00+02:00',
          suitcases: 2,
          handbags: 0,
        },
        source_job_id: 'JOB1',
        saved_at: '2030-10-30T12:30:00Z',
      },
      { group_id: 'G2', configured: false, reason: 'missing' },
    ],
  };
}
function loadArguments() {
  const world = operation();
  return {
    profile: saved().profiles[0],
    responseVersion: 4,
    reviewedScope: airportTransferProfileScope(world, incident, ['G1', 'G2']),
    world,
    incident,
    groupIds: ['G1', 'G2'],
    drafts: [emptyAirportTransferDraft('G2', 'old-scope')],
  };
}
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('saved airport-transfer inputs', () => {
  it('copies the exact pickup instant and explicit zero bags without mutating other drafts or the operation', () => {
    const args = loadArguments(),
      before = structuredClone(args);
    const drafts = loadSavedAirportTransferInput(args);
    expect(drafts[0]).toBe(args.drafts[0]);
    expect(drafts[1]).toMatchObject({
      group_id: 'G1',
      pickup_at: '2030-10-31T14:00',
      suitcases: '2',
      handbags: '0',
    });
    expect(Date.parse(`${drafts[1].pickup_at}:00Z`)).toBe(Date.parse('2030-10-31T16:00:00+02:00'));
    expect(args).toEqual(before);
    // Review is allowed for old dates; dispatch still rejects the unchanged past pickup.
    expect(() =>
      airportTransferInputs(
        [drafts[1]],
        args.world,
        args.groupIds,
        ['bus'],
        8,
        false,
        '',
        Date.parse('2030-11-01T12:00:00Z'),
      ),
    ).toThrow('at least 9 hours');
  });
  it('replaces only the explicitly loaded group without dropping another stale setup', () => {
    const args = loadArguments();
    args.drafts.push({
      ...emptyAirportTransferDraft('G1', 'old'),
      pickup_at: '2031-01-01T00:00',
      suitcases: '7',
      handbags: '7',
    });
    const result = loadSavedAirportTransferInput(args);
    expect(result).toHaveLength(2);
    expect(result[0].scope).toBe('old-scope');
    expect(result[1].suitcases).toBe('2');
    expect(args.drafts[1].suitcases).toBe('7');
  });
  it('rejects stale world/case/selection/scenario/readiness responses even when the group identifier remains', () => {
    for (const mutate of [
      (args: ReturnType<typeof loadArguments>) => {
        args.world.version++;
      },
      (args: ReturnType<typeof loadArguments>) => {
        args.world.scenario_id = 'NEW';
      },
      (args: ReturnType<typeof loadArguments>) => {
        args.world.groups[0].ready_at = 700;
      },
      (args: ReturnType<typeof loadArguments>) => {
        args.incident = { ...incident, id: 'OTHER' };
      },
      (args: ReturnType<typeof loadArguments>) => {
        args.groupIds = ['G1'];
      },
    ]) {
      const args = loadArguments();
      mutate(args);
      expect(() => loadSavedAirportTransferInput(args)).toThrow('changed');
    }
  });
  it('does not add unselected, unlinked, booked or unsupported groups', () => {
    for (const mutate of [
      (args: ReturnType<typeof loadArguments>) => {
        args.groupIds = ['G2'];
      },
      (args: ReturnType<typeof loadArguments>) => {
        args.incident = { ...incident, group_ids: ['G2'] };
      },
      (args: ReturnType<typeof loadArguments>) => {
        args.world.groups[0].incident_id = 'OTHER';
      },
      (args: ReturnType<typeof loadArguments>) => {
        args.world.groups[0].origin = 'CRA';
      },
      (args: ReturnType<typeof loadArguments>) => {
        args.world.groups[0].status = 'confirmed';
      },
    ]) {
      const args = loadArguments();
      mutate(args);
      args.reviewedScope = airportTransferProfileScope(args.world, args.incident, args.groupIds);
      expect(() => loadSavedAirportTransferInput(args)).toThrow();
    }
  });
  it.each([
    { group_id: 'OTHER' },
    { destination_airport: 'OTP' },
    { suitcases: null },
    { handbags: '0' },
    { handbags: 8 },
    { pickup_at: '2030-11-02T14:00' },
    { pickup_at: '2030-02-30T14:00:00Z' },
    { pickup_at: '2030-11-02T14:00:01Z' },
  ])('does not silently repair malformed saved inputs %j', (change) => {
    const response = saved();
    if (response.profiles[0].configured) Object.assign(response.profiles[0].inputs, change);
    expect(() => verifyAirportTransferProfiles(response)).toThrow();
  });
  it('rejects duplicate profiles and unknown configuration state', () => {
    const response = saved();
    response.profiles.push(response.profiles[0]);
    expect(() => verifyAirportTransferProfiles(response)).toThrow();
    expect(() =>
      verifyAirportTransferProfiles({
        world_version: 4,
        profiles: [{ group_id: 'G1' }],
      } as AirportTransferProfiles),
    ).toThrow();
  });
  it('discards late reads across newer reads, changed scope, and unmount', async () => {
    const gate = new AirportTransferProfileReadGate();
    const args = loadArguments(),
      scope = args.reviewedScope;
    const first = gate.begin(),
      second = gate.begin();
    const response = await Promise.resolve(saved());
    expect(gate.accepts(first, scope, scope)).toBe(false);
    expect(gate.accepts(second, scope, scope)).toBe(true);
    expect(
      gate.accepts(
        second,
        scope,
        airportTransferProfileScope(
          { ...args.world, version: response.world_version + 1 },
          incident,
          args.groupIds,
        ),
      ),
    ).toBe(false);
    gate.invalidate();
    expect(gate.accepts(second, scope, scope)).toBe(false);
  });
  it('reads the protected case endpoint without a mutation, provider search or automatic retry', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(saved()), { status: 200 }))
      .mockRejectedValueOnce(new Error('offline'));
    vi.stubGlobal('fetch', fetchMock);
    expect(await api.airportTransferProfiles('CASE/1')).toEqual(saved());
    expect(fetchMock.mock.calls[0][0]).toBe('/api/incidents/CASE%2F1/airport-transfer-profiles');
    expect(fetchMock.mock.calls[0][1]).toMatchObject({
      method: 'GET',
      credentials: 'same-origin',
      body: undefined,
    });
    await expect(api.airportTransferProfiles('CASE1')).rejects.toBeInstanceOf(TransportError);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
  it('shows exact saved values, unavailable groups and explicit replacement without submit buttons', () => {
    const html = renderToStaticMarkup(
      <SavedAirportTransferInputOptions
        world={operation()}
        groupIds={['G1', 'G2']}
        value={[emptyAirportTransferDraft('G1', 'old')]}
        response={saved()}
        disabled={false}
        onLoad={() => {}}
      />,
    );
    for (const text of [
      '2 suitcases',
      '0 handbags',
      '31 Oct 2030',
      '14:00',
      'JOB1',
      'not a fare or a reservation',
      'Replace this group',
      'No saved transfer inputs for this group',
    ])
      expect(html).toContain(text);
    expect(html).toContain('type="button"');
    expect(html).not.toContain('type="submit"');
    const selectedOnly = renderToStaticMarkup(
      <SavedAirportTransferInputOptions
        world={operation()}
        groupIds={['G2']}
        value={[]}
        response={saved()}
        disabled={false}
        onLoad={() => {}}
      />,
    );
    expect(selectedOnly).not.toContain('JOB1');
    expect(selectedOnly).not.toContain('<button');
    const intro = renderToStaticMarkup(
      <SavedAirportTransferInputs
        world={operation()}
        incident={incident}
        groupIds={['G1']}
        value={[]}
        onChange={() => {}}
        disabled={false}
      />,
    );
    expect(intro).toContain('Past dates stay exactly as saved');
    expect(intro).toContain('without searching');
  });
  it.each([
    ['missing', 'No saved transfer inputs for this group. Enter pickup and bag totals'],
    [
      'stale',
      'Saved inputs belong to a different party, route or case. Enter fresh transfer inputs',
    ],
    ['invalid', 'Saved transfer inputs could not be verified. Enter them again'],
    ['scope', 'This group is not consistently linked to this case. Review its case linkage'],
    ['new_internal_reason', 'Saved transfer inputs are unavailable. Review this group'],
  ])(
    'explains unavailable profile %s without exposing its internal code',
    (reason, explanation) => {
      const profile = { group_id: 'G1', configured: false as const, reason };
      const html = renderToStaticMarkup(
        <SavedAirportTransferInputOptions
          world={operation()}
          groupIds={['G1']}
          value={[]}
          response={{ world_version: 4, profiles: [profile] }}
          disabled={false}
          onLoad={() => {}}
        />,
      );
      expect(html).toContain(explanation);
      expect(html).not.toContain(`>${reason}<`);
      expect(html).not.toContain('<button');
      expect(() => loadSavedAirportTransferInput({ ...loadArguments(), profile })).toThrow(
        explanation,
      );
    },
  );
});

it('directs an expired live proposal to fresh sourcing without claiming replan refreshes quotes', () => {
  const world = operation();
  world.decisions = [
    {
      id: 'DEC1',
      mode: 'solver',
      source_version: 3,
      applied_version: 4,
      selected_plan_id: 'PLAN1',
      latency_ms: 20,
      provider: 'solver',
      candidates: [
        {
          id: 'PLAN1',
          strategy: { label: 'Complete journey' },
          total_cost: 200,
          recovered_passengers: 2,
          allocations: [{ group_id: 'G1', itinerary: { legs: [], total_cost: 200 } }],
          exceptions: [],
        },
      ],
    },
  ] as unknown as World['decisions'];
  const render = () =>
    renderToStaticMarkup(
      <Inspector
        world={world}
        selectedId={null}
        onSelect={() => {}}
        onClose={() => {}}
        onReplan={() => {}}
        onScenario={() => {}}
        busy={false}
        showLog={false}
        config={{}}
      />,
    );
  expect(render()).toContain('start a new provider search');
  expect(render()).toContain('Replanning alone does not refresh supplier quotes');
  world.mode = 'simulation';
  expect(render()).toContain('Replan to refresh the proposed resources');
});

import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import LiveDataDialog from './LiveDataDialog';
import { recoveryDataReturnIssue, resolveRecoveryDataContext } from './recoveryDataContext';
import type { RecoveryDataContext } from './recoveryDataContext';
import type { World } from './types';

vi.mock('react-dom', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-dom')>()),
  createPortal: (children: ReactNode) => children,
}));

const context: RecoveryDataContext = {
  scenarioId: 'OPS',
  incidentId: 'CASE-2',
  groupIds: ['G2'],
};
const fixture = (): World =>
  ({
    mode: 'live',
    scenario_id: 'OPS',
    version: 5,
    epoch: '2026-11-01T00:00:00Z',
    airports: [],
    resources: [],
    offers: [],
    reservations: [],
    groups: [
      { id: 'G1', name: 'Other case party', incident_id: 'CASE-1', status: 'waiting' },
      {
        id: 'G2',
        name: 'Target party',
        incident_id: 'CASE-2',
        status: 'offered',
        token: 'PRIVATE_PASSENGER_TOKEN',
      },
    ],
    incidents: [
      { id: 'CASE-1', group_ids: ['G1'], requested_flight_number: 'RO111' },
      {
        id: 'CASE-2',
        group_ids: ['G2'],
        requested_flight_number: 'RO222',
        status: 'open',
        flight: null,
        flight_context_state: 'expired',
      },
    ],
  }) as unknown as World;

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('case-scoped return from operation data', () => {
  it('retains exactly the requested case and group, independently of list order or expired FR24 context', () => {
    const world = fixture();
    const original = structuredClone(world);
    const resolved = resolveRecoveryDataContext(world, context);
    expect(resolved).toEqual({
      kind: 'ready',
      incident: world.incidents![1],
      groups: [world.groups[1]],
    });
    expect(world).toEqual(original);
    expect(context.groupIds).toEqual(['G2']);
  });

  it('does not treat an inventory mutation or passenger exception as a new navigation scope', () => {
    const world = fixture();
    world.version += 1;
    world.groups[1].status = 'exception';
    expect(recoveryDataReturnIssue(world, context, false)).toBeNull();
  });

  it('permits reviewing the same confirmed group without claiming search eligibility', () => {
    const world = fixture();
    world.groups[1].status = 'confirmed';
    expect(recoveryDataReturnIssue(world, context, false)).toBeNull();
  });

  it('allows a case without selected passengers but never selects the first existing group', () => {
    const resolved = resolveRecoveryDataContext(fixture(), { ...context, groupIds: [] });
    expect(resolved.kind).toBe('ready');
    if (resolved.kind === 'ready') expect(resolved.groups).toEqual([]);
  });

  it.each([
    [
      'replaced operation',
      (world: World) => {
        world.scenario_id = 'OTHER-OPS';
      },
    ],
    [
      'simulation mode',
      (world: World) => {
        world.mode = 'simulation';
      },
    ],
    [
      'missing case',
      (world: World) => {
        world.incidents = world.incidents!.slice(0, 1);
      },
    ],
    [
      'missing group',
      (world: World) => {
        world.groups = world.groups.slice(0, 1);
      },
    ],
    [
      'group linked elsewhere',
      (world: World) => {
        world.groups[1].incident_id = 'CASE-1';
      },
    ],
    [
      'case no longer links group',
      (world: World) => {
        world.incidents![1].group_ids = [];
      },
    ],
    [
      'ambiguous case',
      (world: World) => {
        world.incidents!.push({ ...world.incidents![1] });
      },
    ],
    [
      'ambiguous group',
      (world: World) => {
        world.groups.push({ ...world.groups[1] });
      },
    ],
  ])('blocks a stale return for %s without substituting another case or group', (_, mutate) => {
    const world = fixture();
    mutate(world);
    expect(resolveRecoveryDataContext(world, context).kind).toBe('stale');
    expect(recoveryDataReturnIssue(world, context, false)).toBeTypeOf('string');
  });

  it.each([['G2', 'G2'], ['g2'], ['G1'], ['G2', 'MISSING']])(
    'does not normalize or partially keep the requested group selection %j',
    (...groupIds) => {
      expect(resolveRecoveryDataContext(fixture(), { ...context, groupIds }).kind).toBe('stale');
    },
  );

  it('requires pending and unknown submissions to be recovered before returning', () => {
    expect(recoveryDataReturnIssue(fixture(), context, true)).toContain('pending submission');
    expect(recoveryDataReturnIssue(fixture(), context, false)).toBeNull();
    expect(recoveryDataReturnIssue(fixture(), undefined, false)).toContain('No recovery case');
    expect(resolveRecoveryDataContext(fixture())).toEqual({ kind: 'general' });
  });

  it('renders the exact case context and an explicit return without provider calls or private identifiers', () => {
    vi.stubGlobal('document', { body: {} });
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    const onReturn = vi.fn();
    const onClose = vi.fn();
    const onApplied = vi.fn();
    const html = renderToStaticMarkup(
      <LiveDataDialog
        world={fixture()}
        recoveryContext={context}
        onReturnToCase={onReturn}
        onClose={onClose}
        onApplied={onApplied}
      />,
    );
    expect(html).toContain('Recovery case CASE-2 · RO222');
    expect(html).toContain('Selected groups: Target party (G2)');
    expect(html).not.toContain('Other case party');
    expect(html).not.toContain('PRIVATE_PASSENGER_TOKEN');
    expect(html).toContain('it does not link passengers or start a provider search');
    expect(html).toMatch(/<button[^>]*>Return to recovery case<\/button>/);
    expect(html).not.toMatch(/<button[^>]*disabled[^>]*>Return to recovery case<\/button>/);
    expect(fetch).not.toHaveBeenCalled();
    expect(onReturn).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect(onApplied).not.toHaveBeenCalled();
  });

  it('shows a stale-context reason and disables return while leaving general inventory available', () => {
    vi.stubGlobal('document', { body: {} });
    const html = renderToStaticMarkup(
      <LiveDataDialog
        world={{ ...fixture(), scenario_id: 'REPLACED' }}
        recoveryContext={context}
        onReturnToCase={() => {}}
        onClose={() => {}}
        onApplied={() => {}}
      />,
    );
    expect(html).toContain('The operation has changed');
    expect(html).toMatch(/<button[^>]*disabled[^>]*>Return to recovery case<\/button>/);
    expect(html).toContain('Passenger manifest');
    expect(html).not.toContain('Selected groups:');
  });

  it('keeps the general inventory entry free of a return or inferred case', () => {
    vi.stubGlobal('document', { body: {} });
    const html = renderToStaticMarkup(
      <LiveDataDialog world={fixture()} onClose={() => {}} onApplied={() => {}} />,
    );
    expect(html).not.toContain('Recovery case context');
    expect(html).not.toContain('Return to recovery case');
    expect(html).toContain('aria-selected="true">Passenger manifest');
  });
});

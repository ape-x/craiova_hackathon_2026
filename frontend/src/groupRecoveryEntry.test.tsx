import { Children, isValidElement, type ReactElement } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import Inspector, { GroupRecoveryAction } from './Inspector';
import { groupRecoveryEntry, isGroupSupplierConfirmed } from './groupRecoveryEntry';
import type { Offer, RecoveryIncident, Reservation, Resource, World } from './types';

function operation(): World {
  return {
    scenario_id: 'OP1',
    version: 4,
    mode: 'live',
    epoch: '2030-11-01T00:00:00Z',
    sim_time: 720,
    currency: 'EUR',
    budget: 1000,
    groups: ['G1', 'G2'].map((id) => ({
      id,
      incident_id: 'CASE1',
      name: `Party ${id}`,
      size: 2,
      members: [],
      adult_count: 2,
      origin: 'LHR',
      destination: 'CRA',
      ready_at: 720,
      deadline: 2880,
      accessible: false,
      status: 'affected',
      budget: 1000,
      original_flight_id: 'W43001',
      token: 'token',
      priority: 1,
    })),
    incidents: [
      {
        id: 'CASE1',
        group_ids: ['G1', 'G2'],
        requested_flight_number: 'W43001',
        flight: null,
        flight_context_state: 'expired',
      } as RecoveryIncident,
    ],
    airports: [],
    resources: [],
    offers: [],
    reservations: [],
    events: [],
    decisions: [],
    observations: [],
    stats: {},
    provider_status: {},
  };
}
const offer = (status: Offer['status'] = 'proposed'): Offer => ({
  id: 'O1',
  group_id: 'G1',
  status,
  expires_at: '2030-11-01T12:02:00Z',
  created_version: 4,
  confirmed_at: null,
  reason: null,
  itinerary: {
    legs: [
      {
        resource_id: 'R1',
        kind: 'flight',
        origin: 'LHR',
        destination: 'CRA',
        departure: 800,
        arrival: 1000,
        cost: 200,
      },
    ],
    arrival: 1000,
    total_cost: 200,
  },
});
const resource = (booking_status: Resource['booking_status'] = 'confirmed'): Resource => ({
  id: 'R1',
  name: 'Supplier flight',
  kind: 'flight',
  source: 'Amadeus',
  origin: 'LHR',
  destination: 'CRA',
  departure: 800,
  arrival: 1000,
  capacity: 2,
  accessible: false,
  available: true,
  cost_per_person: 100,
  room_capacity: 0,
  vehicle_id: null,
  status: 'quoted',
  booking_status,
});
const reservation = (status: Reservation['status'] = 'confirmed'): Reservation => ({
  id: 'RES1',
  offer_id: 'O1',
  group_id: 'G1',
  resource_id: 'R1',
  quantity: 2,
  status,
  expires_at: '2030-11-01T12:02:00Z',
});
const inspector = (world: World) =>
  renderToStaticMarkup(
    <Inspector
      world={world}
      selectedId="G1"
      onSelect={() => {}}
      onClose={() => {}}
      onReplan={() => {}}
      onGroupRecovery={() => {}}
      onScenario={() => {}}
      busy={false}
      config={{ public_base_url: 'https://example.invalid' }}
      showLog={false}
    />,
  );
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('passenger recovery navigation', () => {
  it('selects only the exact reciprocal case and group, even when source flight context expired', () => {
    const world = operation(),
      before = structuredClone(world);
    expect(groupRecoveryEntry(world, 'G1')?.intent).toEqual({
      kind: 'recover',
      scenarioId: 'OP1',
      sourceVersion: 4,
      groupId: 'G1',
      incidentId: 'CASE1',
    });
    expect(groupRecoveryEntry(world, 'G1')?.detail).toContain('only this group selected');
    expect(world).toEqual(before);
  });
  it('provides setup for an unlinked group without guessing from its matching flight reference', () => {
    const world = operation();
    world.groups[0].incident_id = null;
    world.incidents![0].group_ids = ['G2'];
    const result = groupRecoveryEntry(world, 'G1')!;
    expect(result.intent).toEqual({
      kind: 'setup',
      scenarioId: 'OP1',
      sourceVersion: 4,
      groupId: 'G1',
    });
    expect(result.detail).toContain('explicitly link');
    expect(result.intent).not.toHaveProperty('incidentId');
  });
  it('does not pick a case from one-sided, dangling, duplicated or conflicting memberships', () => {
    for (const mutate of [
      (world: World) => {
        world.groups[0].incident_id = null;
      },
      (world: World) => {
        world.groups[0].incident_id = 'MISSING';
      },
      (world: World) => {
        world.incidents![0].group_ids = ['G2'];
      },
      (world: World) => {
        world.incidents!.push({ ...world.incidents![0], id: 'CASE2' });
      },
      (world: World) => {
        world.incidents!.push(structuredClone(world.incidents![0]));
      },
      (world: World) => {
        world.incidents![0].group_ids.push('G1');
      },
      (world: World) => {
        world.groups.push(structuredClone(world.groups[0]));
      },
    ]) {
      const world = operation();
      mutate(world);
      const result = groupRecoveryEntry(world, 'G1')!;
      expect(result.intent.kind).toBe('setup');
      expect(result.intent).not.toHaveProperty('incidentId');
      expect(result.detail).toMatch(/conflicting|incomplete/);
    }
  });
  it('does not create a live intent for a missing group, simulation or invalid world identity', () => {
    expect(groupRecoveryEntry(operation(), 'MISSING')).toBeNull();
    expect(groupRecoveryEntry({ ...operation(), mode: 'simulation' }, 'G1')).toBeNull();
    expect(groupRecoveryEntry({ ...operation(), scenario_id: '' }, 'G1')).toBeNull();
    expect(groupRecoveryEntry({ ...operation(), version: NaN }, 'G1')).toBeNull();
  });
  it('routes all explicit booking evidence to case review without a fresh recovery selection', () => {
    for (const mutate of [
      (world: World) => {
        world.groups[0].status = 'confirmed';
      },
      (world: World) => {
        world.offers = [offer('confirmed')];
      },
      (world: World) => {
        world.offers = [
          {
            ...offer('invalidated'),
            external_bookings: [{ resource_id: 'R1', confirmation_id: 'B1' }],
          },
        ];
      },
      (world: World) => {
        world.reservations = [reservation()];
      },
      (world: World) => {
        world.offers = [offer('accepted')];
        world.resources = [resource()];
      },
    ]) {
      const world = operation();
      mutate(world);
      expect(isGroupSupplierConfirmed(world, 'G1')).toBe(true);
      expect(groupRecoveryEntry(world, 'G1')?.intent).toEqual({
        kind: 'review_case',
        scenarioId: 'OP1',
        sourceVersion: 4,
        groupId: 'G1',
        incidentId: 'CASE1',
      });
      expect(groupRecoveryEntry(world, 'G1')?.detail).toContain('no new recovery is preselected');
    }
  });
  it.each(['held', 'proposed', 'accepted', 'confirmed'] as const)(
    'protects confirmed resources allocated through a %s offer',
    (status) => {
      const world = operation();
      world.offers = [offer(status)];
      world.resources = [resource()];
      expect(isGroupSupplierConfirmed(world, 'G1')).toBe(true);
    },
  );
  it.each(['expired', 'invalidated', 'superseded', 'declined'] as const)(
    'does not turn a historical %s resource reference into this group’s booking',
    (status) => {
      const world = operation();
      world.offers = [offer(status)];
      world.resources = [resource()];
      expect(isGroupSupplierConfirmed(world, 'G1')).toBe(false);
      expect(groupRecoveryEntry(world, 'G1')?.intent.kind).toBe('recover');
      world.offers[0].external_bookings = [{ resource_id: 'R1' }];
      expect(isGroupSupplierConfirmed(world, 'G1')).toBe(true);
    },
  );
  it('does not use unrelated confirmed inventory, other groups’ bookings or an unconfirmed reservation as this group’s booking', () => {
    const world = operation();
    world.groups[1].status = 'confirmed';
    world.offers = [{ ...offer('confirmed'), group_id: 'G2' }];
    world.resources = [resource()];
    world.reservations = [reservation('held')];
    expect(isGroupSupplierConfirmed(world, 'G1')).toBe(false);
    expect(groupRecoveryEntry(world, 'G1')?.intent.kind).toBe('recover');
    world.offers.push(offer('proposed'));
    world.resources[0].booking_status = 'unbooked';
    expect(isGroupSupplierConfirmed(world, 'G1')).toBe(false);
  });
  it('the action dispatches exactly one navigation intent, never a source call or global replan', () => {
    const onGroupRecovery = vi.fn(),
      fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    const action = GroupRecoveryAction({
      world: operation(),
      groupId: 'G1',
      busy: false,
      onGroupRecovery,
    })!;
    const button = Children.toArray(action.props.children).find(
      (node) => isValidElement(node) && node.type === 'button',
    ) as ReactElement<{ onClick: () => void; type: string }>;
    expect(button.props.type).toBe('button');
    button.props.onClick();
    expect(onGroupRecovery).toHaveBeenCalledExactlyOnceWith({
      kind: 'recover',
      scenarioId: 'OP1',
      sourceVersion: 4,
      groupId: 'G1',
      incidentId: 'CASE1',
    });
    expect(fetchMock).not.toHaveBeenCalled();
    const blocked = GroupRecoveryAction({
      world: operation(),
      groupId: 'G1',
      busy: true,
      onGroupRecovery,
    })!;
    const disabled = Children.toArray(blocked.props.children).find(
      (node) => isValidElement(node) && node.type === 'button',
    ) as ReactElement<{ onClick: () => void; disabled: boolean }>;
    expect(disabled.props.disabled).toBe(true);
    disabled.props.onClick();
    expect(onGroupRecovery).toHaveBeenCalledTimes(1);
  });
  it('replaces the live empty-state global planner action and remains available beside existing proposals', () => {
    const world = operation();
    const empty = inspector(world);
    expect(empty).toContain('Find sourced alternatives');
    expect(empty).toContain('only this group selected');
    expect(empty).not.toContain('Calculate recovery');
    expect(empty).not.toContain('Run the recovery planner');
    world.offers = [offer()];
    const proposed = inspector(world);
    expect(proposed).toContain('Your recovery proposal is ready');
    expect(proposed).toContain('Find sourced alternatives');
    expect(proposed).not.toContain('Calculate recovery');
    world.resources = [resource()];
    const confirmed = inspector(world);
    expect(confirmed).toContain('Review linked case');
    expect(confirmed).not.toContain('Find sourced alternatives');
  });
  it('shows actionable unlinked setup and preserves the simulation planner action', () => {
    const world = operation();
    world.groups[0].incident_id = null;
    world.incidents = [];
    expect(inspector(world)).toContain('Set up recovery case');
    expect(inspector(world)).toContain('explicitly link this group');
    expect(inspector({ ...world, mode: 'simulation' })).toContain('Calculate recovery');
    expect(inspector({ ...world, mode: 'simulation' })).not.toContain('Find sourced alternatives');
    const missingCallback = renderToStaticMarkup(
      <GroupRecoveryAction world={world} groupId="G1" busy={false} />,
    );
    expect(missingCallback).toContain('disabled=""');
    expect(missingCallback).toContain('Open Recovery cases');
  });
});

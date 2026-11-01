import { describe, expect, it } from 'vitest';
import { passengerArrivalPending } from './passengerArrival';
import type { World } from './types';

function operation(): World {
  return {
    mode: 'live',
    scenario_id: 'OP1',
    epoch: '2030-11-01T00:00:00Z',
    incidents: [{ id: 'CASE1', group_ids: ['G1'] }],
    airports: [{ id: 'LHR', code: 'LHR', kind: 'airport' }],
    resources: [{ id: 'ORIGINAL', status: 'needs_diversion', destination: 'CRA' }],
    groups: [
      {
        id: 'G1',
        incident_id: 'CASE1',
        origin: 'LHR',
        ready_at: 601,
        original_flight_id: 'ORIGINAL',
        provenance: {
          arrival_reports: [
            {
              id: 'opr_' + 'a'.repeat(24),
              type: 'arrival_report',
              source: 'operator_report',
              scenario_id: 'OP1',
              epoch: '2030-11-01T00:00:00Z',
              original_flight_id: 'ORIGINAL',
              group_id: 'G1',
              incident_id: 'CASE1',
              airport_code: 'LHR',
              ready_at: '2030-11-01T10:00:30Z',
              received_at: '2030-11-01T09:00:00Z',
            },
          ],
        },
      },
    ],
  } as unknown as World;
}
const latest = (world: World) =>
  (world.groups[0].provenance!.arrival_reports as Record<string, unknown>[]).at(-1)!;
describe('current server-reported passenger readiness', () => {
  it('resolves only passenger planning hold without changing original flight status', () => {
    const world = operation(),
      before = structuredClone(world);
    expect(passengerArrivalPending(world, world.groups[0])).toBe(false);
    expect(world).toEqual(before);
    expect(world.resources[0].status).toBe('needs_diversion');
  });
  it.each([
    { id: 'opr_invalid' },
    { source: 'flight_schedule' },
    { type: 'flight_arrival' },
    { scenario_id: 'OLD' },
    { epoch: '2029-11-01T00:00:00Z' },
    { original_flight_id: 'DIFFERENT' },
    { group_id: 'G2' },
    { incident_id: 'OTHER' },
    { airport_code: 'OTP' },
    { ready_at: '2030-11-01T10:01:01Z' },
    { received_at: '2030-11-01T09:00:00' },
  ])('retains a pending arrival for stale or unverified evidence %j', (change) => {
    const world = operation();
    Object.assign(latest(world), change);
    expect(passengerArrivalPending(world, world.groups[0])).toBe(true);
  });
  it('requires reciprocal case binding and airport identity', () => {
    const world = operation();
    world.incidents![0].group_ids = [];
    expect(passengerArrivalPending(world, world.groups[0])).toBe(true);
    world.incidents![0].group_ids = ['G1'];
    world.airports[0].kind = 'hotel';
    expect(passengerArrivalPending(world, world.groups[0])).toBe(true);
  });
  it.each(['duplicate', 'reversed', 'nonobject', 'latest_stale'])(
    'does not fall back through an invalid %s history',
    (kind) => {
      const world = operation(),
        first = latest(world);
      world.groups[0].provenance!.arrival_reports =
        kind === 'duplicate'
          ? [first, { ...first }]
          : kind === 'reversed'
            ? [
                first,
                { ...first, id: 'opr_' + 'b'.repeat(24), received_at: '2030-11-01T08:00:00Z' },
              ]
            : kind === 'nonobject'
              ? [null, first]
              : [
                  first,
                  { ...first, id: 'opr_' + 'b'.repeat(24), ready_at: '2030-11-01T11:00:00Z' },
                ];
      expect(passengerArrivalPending(world, world.groups[0])).toBe(true);
    },
  );
});

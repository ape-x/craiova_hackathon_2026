import { describe, expect, it } from 'vitest';
import { voiceStartupScope } from './voiceScope';
import type { World } from './types';

const world = {
  mode: 'live',
  groups: [
    {
      id: 'G1',
      incident_id: 'CASE1',
      name: 'Private passenger label',
      provenance: {
        hotel_recovery_profile: { inputs: { pickup_at: 'not sent', coordinates: 'not sent' } },
      },
    },
    { id: 'G2', incident_id: 'CASE2' },
    { id: 'UNBOUND', incident_id: null },
  ],
  incidents: [
    {
      id: 'CASE1',
      group_ids: ['G1', 'OTHER'],
      requested_flight_number: 'EX123',
      date_utc: '2026-11-01',
    },
    { id: 'CASE2', group_ids: ['G2'] },
  ],
} as unknown as World;

describe('reference-only voice startup selection', () => {
  it('uses the open case and only includes a reciprocally linked selected group', () => {
    expect(voiceStartupScope(world, 'G1', 'CASE1', true)).toEqual({
      incident_id: 'CASE1',
      group_id: 'G1',
    });
    expect(voiceStartupScope(world, 'G2', 'CASE1', true)).toEqual({ incident_id: 'CASE1' });
    expect(voiceStartupScope(world, null, 'CASE1', true)).toEqual({ incident_id: 'CASE1' });
  });
  it('lets an explicit group selection derive its own incident instead of the previously opened case', () => {
    expect(voiceStartupScope(world, 'G2', 'CASE1', false)).toEqual({
      group_id: 'G2',
      incident_id: 'CASE2',
    });
    expect(voiceStartupScope(world, 'UNBOUND', 'CASE1', false)).toEqual({ group_id: 'UNBOUND' });
    const nonreciprocal = { ...world, incidents: [{ ...world.incidents![0], group_ids: [] }] };
    expect(voiceStartupScope(nonreciprocal, 'G1', null, false)).toEqual({ group_id: 'G1' });
  });
  it('retains a valid explicitly opened case after closing its modal without selecting its first group', () => {
    expect(voiceStartupScope(world, null, 'CASE1', false)).toEqual({ incident_id: 'CASE1' });
    expect(voiceStartupScope(world, 'Q-EX123', 'CASE1', false)).toEqual({ incident_id: 'CASE1' });
  });
  it('omits absent/stale selection and keeps simulation startup unchanged', () => {
    expect(voiceStartupScope(world, null, null, false)).toBeUndefined();
    expect(voiceStartupScope(world, 'Q-EX123', null, false)).toBeUndefined();
    expect(voiceStartupScope(world, 'deleted-group', 'deleted-case', true)).toBeUndefined();
    expect(voiceStartupScope(null, 'G1', 'CASE1', true)).toBeUndefined();
    expect(
      voiceStartupScope({ ...world, mode: 'simulation' }, 'G1', 'CASE1', true),
    ).toBeUndefined();
  });
  it('returns only identifiers, never case dates, names, coordinates, instructions or saved allowances', () => {
    const scope = voiceStartupScope(world, 'G1', 'CASE1', true)!;
    expect(Object.keys(scope).sort()).toEqual(['group_id', 'incident_id']);
    expect(JSON.stringify(scope)).toBe('{"incident_id":"CASE1","group_id":"G1"}');
  });
});

import { describe, expect, it } from 'vitest';
import { reportedArrivalFocus } from './arrivalFocus';
import { cameraFor } from './maps/overlays';
import type { MutationResult, World } from './types';

function fixture(): MutationResult {
  const world = {
    scenario_id: 'operation',
    version: 8,
    mode: 'live',
    airports: [
      { id: 'CRA', code: 'CRA', kind: 'airport', lat: 44.3181, lon: 23.8886 },
      { id: 'LHR', code: 'LHR', kind: 'airport', lat: 51.4706, lon: -0.46194 },
    ],
    groups: [
      {
        id: 'G1',
        incident_id: 'CASE',
        origin: 'LHR',
        provenance: { arrival_reports: [{ id: 'REPORT' }] },
      },
    ],
    resources: [],
    observations: [],
    events: [],
  } as unknown as World;
  return {
    world,
    applied: true,
    focus_id: 'LHR',
    report: {
      id: 'REPORT',
      source: 'operator_report',
      type: 'arrival_report',
      received_at: '2026-11-01T08:00:00Z',
      ready_at: '2026-11-01T09:00:00Z',
      description: 'Passengers ready at the reported airport.',
      incident_id: 'CASE',
      group_ids: ['G1'],
      airport_code: 'LHR',
      affected_group_ids: [],
      affected_offer_ids: [],
    },
  };
}

describe('current operator-reported arrival focus', () => {
  it('centers the actual reported airport instead of the home regional view', () => {
    const result = fixture();
    const focus = reportedArrivalFocus(result, result.world);
    expect(focus).toBe('LHR');
    expect(cameraFor(result.world, 'airport', focus).center).toEqual({
      lat: 51.4706,
      lng: -0.46194,
      altitude: 0,
    });
  });

  it('still focuses a saved arrival when recovery could not start', () => {
    const result = fixture();
    result.recovery_blocked = {
      code: 'hotel_setup_required',
      explanation: 'Fresh inputs required.',
    };
    expect(reportedArrivalFocus(result, result.world)).toBe('LHR');
  });

  it('accepts an unchanged replay but ignores a superseded report even at the same airport', () => {
    const result = { ...fixture(), replayed: true };
    expect(reportedArrivalFocus(result, result.world)).toBe('LHR');
    const current = structuredClone(result.world);
    current.groups[0].provenance = { arrival_reports: [{ id: 'REPORT' }, { id: 'LATER' }] };
    expect(reportedArrivalFocus(result, current)).toBeNull();
    current.groups[0].origin = 'CRA';
    expect(reportedArrivalFocus(result, current)).toBeNull();
  });

  it('does not focus when any reported passenger is missing or assigned to a different case', () => {
    const result = fixture();
    const current = structuredClone(result.world);
    current.groups[0].incident_id = 'OTHER';
    expect(reportedArrivalFocus(result, current)).toBeNull();
    current.groups = [];
    expect(reportedArrivalFocus(result, current)).toBeNull();
  });

  it('rejects an unrecorded response or a different current operation', () => {
    const result = fixture();
    expect(reportedArrivalFocus({ ...result, applied: false }, result.world)).toBeNull();
    expect(reportedArrivalFocus(result, { ...result.world, scenario_id: 'OTHER' })).toBeNull();
    expect(reportedArrivalFocus(result, { ...result.world, mode: 'simulation' })).toBeNull();
  });

  it('requires the catalog airport node and a nonempty exact group scope', () => {
    const result = fixture();
    const current = structuredClone(result.world);
    current.airports[1].code = 'OTP';
    expect(reportedArrivalFocus(result, current)).toBeNull();
    if (result.report?.type !== 'arrival_report') throw new Error('Invalid fixture');
    result.report.group_ids = [];
    expect(reportedArrivalFocus(result, result.world)).toBeNull();
  });
});

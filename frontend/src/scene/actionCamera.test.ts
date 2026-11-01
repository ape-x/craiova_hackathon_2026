import { describe, expect, it } from 'vitest';
import type { Airport, Resource, World } from '../types';
import { sceneCameraFor } from './actionCamera';
import { nextCameraFocus } from './cameraFocus';
import { buildNetworkProjection } from './networkProjection';
import { busSpots, gates, groupPosition } from './layout';
import { cameraFor } from '../maps/overlays';
import { resourcePoint, scenePoint } from '../maps/placement';

const airport = (id: string, lat: number, lon: number): Airport => ({
  id,
  code: id,
  name: id,
  lat,
  lon,
  available: true,
  diversion_eligible: true,
});
const resource = (
  id: string,
  kind: Resource['kind'],
  origin: string,
  destination: string,
): Resource => ({
  id,
  kind,
  origin,
  destination,
  name: id,
  departure: 100,
  arrival: 200,
  capacity: 10,
  accessible: true,
  available: true,
  cost_per_person: 10,
  room_capacity: 2,
  vehicle_id: id,
  status: 'scheduled',
  source: 'synthetic',
});
const world = {
  scenario_id: 'camera-test',
  mode: 'simulation',
  sim_time: 0,
  airports: [
    airport('CRA', 44.32, 23.89),
    airport('OTP', 44.57, 26.08),
    airport('LIS', 38.77, -9.13),
  ],
  resources: [
    resource('F-1', 'flight', 'CRA', 'LIS'),
    resource('F-2', 'flight', 'CRA', 'OTP'),
    resource('F-3', 'flight', 'CRA', 'OTP'),
    resource('F-4', 'flight', 'CRA', 'LIS'),
    resource('B-1', 'bus', 'CRA', 'OTP'),
    resource('H-CRA', 'hotel', 'CRA', 'CRA'),
  ],
  groups: Array.from({ length: 30 }, (_, i) => ({ id: `G-${i}` })),
  offers: [],
  observations: [],
} as unknown as World;

describe('explicit camera focus lifecycle', () => {
  it('refocuses repeated requests on the same target and survives repeated effect setup', () => {
    const first = nextCameraFocus(null, 1, 'scene|region|LIS');
    expect(first.following).toBe(true);
    expect(nextCameraFocus(first, 1, first.context)).toEqual(first);
    expect(nextCameraFocus(first, 2, first.context)).toEqual({ ...first, request: 2 });
  });

  it('ends following on manual selection, mode, reset or scenario changes until a new request', () => {
    const first = nextCameraFocus(null, 1, 'scene|region|LIS|0');
    for (const context of [
      'scene|airport|LIS|0',
      'scene|region|OTP|0',
      'scene|region|LIS|1',
      'new|region|LIS|0',
    ]) {
      const manual = nextCameraFocus(first, 1, context);
      expect(manual.following).toBe(false);
      expect(nextCameraFocus(manual, 1, first.context).following).toBe(false);
      expect(nextCameraFocus(manual, 2, first.context).following).toBe(true);
    }
    expect(nextCameraFocus(null, 0, first.context).following).toBe(false);
  });
});

describe('architectural action camera', () => {
  it('frames a distant airport at the bounded schematic marker, not its raw coordinates', () => {
    const point = buildNetworkProjection(world.airports).points.get('LIS')!;
    const camera = sceneCameraFor(world, 'region', 'LIS', 1, true);
    expect(camera.target[0]).toBe(point[0]);
    expect(camera.target[2]).toBe(point[2]);
    expect(Math.abs(camera.target[0])).toBeLessThanOrEqual(175);
    expect(camera.position[1]).toBeGreaterThan(camera.target[1]);
    expect(sceneCameraFor(world, 'region', 'LIS').target).toEqual([0, 0, 10]);
  });

  it('fits both displayed endpoints of a selected route and widens for a narrow viewport', () => {
    const projection = buildNetworkProjection(world.airports);
    const start = projection.points.get('CRA')!;
    const end = projection.points.get('LIS')!;
    const camera = sceneCameraFor(world, 'region', 'F-1', 1, true);
    expect(camera.target[0]).toBe((start[0] + end[0]) / 2);
    expect(camera.target[2]).toBe((start[2] + end[2]) / 2);
    expect(camera.position[1] - camera.target[1]).toBeGreaterThanOrEqual(
      Math.abs(end[0] - start[0]) * 1.5,
    );
    const narrow = sceneCameraFor(world, 'region', 'F-1', 2, true);
    expect(narrow.target).toEqual(camera.target);
    expect(narrow.position[1] - narrow.target[1]).toBe((camera.position[1] - camera.target[1]) * 2);
  });

  it('uses rendered aircraft, bus and hotel positions and ignores aircraft beyond visible slots', () => {
    expect(sceneCameraFor(world, 'airport', 'F-2', 1, true).target).toEqual([
      gates[1][0],
      3,
      gates[1][2],
    ]);
    expect(sceneCameraFor(world, 'airport', 'B-1', 1, true).target).toEqual([
      busSpots[0][0],
      2,
      busSpots[0][2] - 25,
    ]);
    expect(sceneCameraFor(world, 'airport', 'H-CRA', 1, true).target).toEqual([172, 10, 77]);
    expect(sceneCameraFor(world, 'airport', 'F-4', 1, true)).toEqual(
      sceneCameraFor(world, 'airport', null),
    );
  });

  it('follows an off-page passenger group in its replacement first slot and safely handles unknown targets', () => {
    expect(sceneCameraFor(world, 'passenger', 'G-29', 1, true).target).toEqual([
      groupPosition(0)[0],
      2,
      groupPosition(0)[2],
    ]);
    expect(sceneCameraFor(world, 'region', 'missing', 1, true)).toEqual(
      sceneCameraFor(world, 'region', null),
    );
    expect(sceneCameraFor({ ...world, airports: [] }, 'region', 'LIS', 1, true)).toEqual(
      sceneCameraFor(world, 'region', null),
    );
  });
});

describe('geographic action camera', () => {
  it('focuses the requested airport in network view without changing the manual overview', () => {
    expect(cameraFor(world, 'region', 'LIS', true).center).toEqual(
      scenePoint(world.airports[2], 0, 45),
    );
    expect(cameraFor(world, 'region', 'LIS', true).range).toBe(650);
    expect(cameraFor(world, 'region', 'LIS')).toEqual(cameraFor(world, 'region', null));
    const live = { ...world, mode: 'live' as const };
    expect(cameraFor(live, 'region', 'LIS', true).center).toEqual({
      lat: 38.77,
      lng: -9.13,
      altitude: 0,
    });
  });

  it('follows a simulated resource even when the architectural mode uses the network', () => {
    expect(cameraFor(world, 'region', 'F-1', true).center).toEqual(
      resourcePoint(world.resources[0], world),
    );
    expect(cameraFor(world, 'region', 'F-1', true).range).toBe(550);
    expect(cameraFor(world, 'region', 'F-1')).toEqual(cameraFor(world, 'region', null));
  });
});

import { describe, expect, it } from 'vitest';
import { buildNetworkProjection, regionalPoint } from './networkProjection';

const airport = (code: string, lat: number, lon: number) => ({ id: code, code, lat, lon });
const initial = [
  airport('CRA', 44.3181, 23.8886),
  airport('OTP', 44.5711, 26.085),
  airport('SBZ', 45.7856, 24.0913),
  airport('LTN', 51.8747, -0.3683),
];

describe('schematic network projection', () => {
  it('keeps the initial terrain scale while showing the remote airport on its perimeter', () => {
    const result = buildNetworkProjection(initial);
    expect(result.regional).toBe(true);
    expect(result.points.size).toBe(4);
    expect(result.points.get('CRA')).toEqual(regionalPoint(44.3181, 23.8886, 2));
    expect(Math.abs(result.points.get('LTN')![0])).toBeLessThanOrEqual(175);
    expect(Math.abs(result.points.get('LTN')![2])).toBeLessThanOrEqual(115);
  });

  it('fits every airport when a new remote destination expands the network', () => {
    const locations = [
      ...initial,
      airport('LIS', 38.7813, -9.13592),
      airport('JFK', 40.6394, -73.7793),
    ];
    const result = buildNetworkProjection(locations);
    expect(result.regional).toBe(false);
    expect(result.points.size).toBe(locations.length);
    for (const p of result.points.values()) {
      expect(Math.abs(p[0])).toBeLessThanOrEqual(175);
      expect(Math.abs(p[2])).toBeLessThanOrEqual(105);
      expect(p.every(Number.isFinite)).toBe(true);
    }
    expect(result.points.get('UNKNOWN')).toBeUndefined();
  });

  it('keeps Lisbon west of London and Romania without a floating-point world wrap', () => {
    const result = buildNetworkProjection([...initial, airport('LIS', 38.7813, -9.13592)]);
    expect(result.points.get('LIS')![0]).toBeLessThan(result.points.get('LTN')![0]);
    expect(result.points.get('LTN')![0]).toBeLessThan(result.points.get('CRA')![0]);
    const points = [...result.points.values()];
    for (let i = 0; i < points.length; i++) {
      for (let j = i + 1; j < points.length; j++) {
        const distance = Math.hypot(
          points[i][0] - points[j][0],
          (points[i][2] - points[j][2]) * 0.7,
        );
        expect(distance).toBeGreaterThan(44);
      }
    }
  });

  it('handles the date line, coincident locations and input order deterministically', () => {
    const locations = [airport('A', 10, 179), airport('B', 10, -179), airport('C', 10, 179)];
    const result = buildNetworkProjection(locations);
    expect([...result.points]).toEqual([
      ...buildNetworkProjection([...locations].reverse()).points,
    ]);
    for (const p of result.points.values()) expect(p.every(Number.isFinite)).toBe(true);
    expect(result.points.get('A')).not.toEqual(result.points.get('C'));
    expect(result.project(10, 180)[0]).toBeCloseTo(0);
  });

  it('does not fabricate coordinates for invalid or absent airport records', () => {
    const result = buildNetworkProjection([airport('BAD', Number.NaN, 1)]);
    expect(result.points.size).toBe(0);
    expect(buildNetworkProjection([]).project(0, 0).every(Number.isFinite)).toBe(true);
  });
});

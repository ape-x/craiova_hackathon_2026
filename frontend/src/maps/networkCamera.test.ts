import { describe, expect, it } from 'vitest';
import type { GeoPoint } from './contracts';
import { networkCamera } from './networkCamera';

const cra = { lat: 44.3181, lng: 23.8886 };
const lhr = { lat: 51.4706, lng: -0.4619 };
const otp = { lat: 44.5711, lng: 26.085 };

describe('geographic network overview', () => {
  it('keeps a useful local overview and centers a single airport', () => {
    const camera = networkCamera([cra], otp);
    expect(camera.center.lat).toBe(cra.lat);
    expect(camera.center.lng).toBeCloseTo(cra.lng, 10);
    expect(camera).toMatchObject({ range: 330_000, tilt: 25, heading: 0 });
    expect(networkCamera([cra, { lat: 44.4, lng: 24 }], otp).range).toBe(330_000);
  });

  it('frames London and Bucharest instead of cropping both at a fixed 330 km', () => {
    const camera = networkCamera([lhr, otp], cra);
    expect(camera.center.lat).toBeCloseTo(48.02085, 4);
    expect(camera.center.lng).toBeCloseTo(12.81155, 4);
    expect(camera.range).toBeGreaterThan(3_000_000);
    expect(camera.range).toBeLessThan(4_000_000);
    expect(camera.range).toBeGreaterThan(networkCamera([cra, otp], cra).range * 5);
  });

  it('uses geographic bounds without weighting the center toward duplicated airports', () => {
    const lis = { lat: 38.7813, lng: -9.1359 };
    const expected = networkCamera([lhr, otp, lis], cra);
    expect(networkCamera([otp, lhr, lis, lis, lis], cra)).toEqual(expected);
    expect(expected.center.lng).toBeCloseTo(8.47455, 4);
    expect(expected.range).toBeGreaterThan(4_000_000);
  });

  it('keeps date-line neighbors together instead of centering on the opposite hemisphere', () => {
    const points = [
      { lat: 10, lng: 179 },
      { lat: 12, lng: -179 },
    ];
    const camera = networkCamera(points, cra);
    expect(camera.center.lat).toBe(11);
    expect(Math.abs(camera.center.lng)).toBe(180);
    expect(camera.range).toBeGreaterThan(330_000);
    expect(camera.range).toBeLessThan(1_000_000);
    expect(networkCamera([...points].reverse(), cra)).toEqual(camera);
  });

  it('ignores malformed coordinates and uses a finite empty-world fallback', () => {
    const invalid: GeoPoint[] = [
      { lat: NaN, lng: 0 },
      { lat: 0, lng: Infinity },
      { lat: 91, lng: 0 },
      { lat: 0, lng: -181 },
    ];
    expect(networkCamera([...invalid, lhr, otp], cra)).toEqual(networkCamera([lhr, otp], cra));
    expect(networkCamera(invalid, cra)).toEqual(networkCamera([cra], otp));
    expect(networkCamera([], { lat: NaN, lng: NaN })).toEqual(
      networkCamera([{ lat: 0, lng: 0 }], cra),
    );
  });

  it('bounds a globe-spanning network without infinite range or an invalid center', () => {
    const camera = networkCamera(
      [
        { lat: 89, lng: 0 },
        { lat: -89, lng: 0 },
        { lat: 0, lng: 179.9 },
        { lat: 0, lng: -179.9 },
      ],
      cra,
    );
    expect(camera.range).toBe(30_000_000);
    expect(camera.center.lat).toBeGreaterThanOrEqual(-90);
    expect(camera.center.lat).toBeLessThanOrEqual(90);
    expect(camera.center.lng).toBeGreaterThanOrEqual(-180);
    expect(camera.center.lng).toBeLessThanOrEqual(180);
  });
});

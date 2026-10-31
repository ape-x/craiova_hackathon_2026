import { describe, it, expect, vi } from 'vitest';
import { createMapsLoader } from './loader';
import { moveCamera } from './camera';
import type { MapsWindow } from './loader';
import {
  buildOverlays,
  cameraFor,
  COLORS,
  observedPoint,
  updateObservationTracks,
} from './overlays';
import type { ObservationTracks } from './overlays';
import type { World } from '../types';

function mockDocument() {
  const scripts: Array<{ src: string; onerror?: () => void }> = [];
  return {
    scripts,
    doc: {
      createElement: () => ({ src: '', async: false, nonce: '' }),
      querySelector: () => null,
      head: { append: (script: { src: string }) => scripts.push(script) },
    } as unknown as Document,
  };
}
const maps3d = {
  Map3DElement: class {},
  Marker3DInteractiveElement: class {},
  Polyline3DElement: class {},
};
const marker = { PinElement: class {} };

describe('Google camera runtime boundary', () => {
  const camera = { center: { lat: 44.3, lng: 23.8 }, range: 2000, tilt: 60, heading: 0 };
  it('handles synchronous provider throws and rejected animations without escaping to React', async () => {
    await expect(
      moveCamera(
        {
          flyCameraTo: () => {
            throw new Error('Map is not initialized');
          },
        },
        camera,
        900,
      ),
    ).resolves.toBe(false);
    await expect(
      moveCamera(
        {
          flyCameraTo: async () => {
            throw new Error('Map was removed');
          },
        },
        camera,
        900,
      ),
    ).resolves.toBe(false);
  });
  it('preserves documented camera options for synchronous or asynchronous success', async () => {
    const flyCameraTo = vi.fn();
    await expect(moveCamera({ flyCameraTo }, camera, 0)).resolves.toBe(true);
    expect(flyCameraTo).toHaveBeenCalledWith({ endCamera: camera, durationMillis: 0 });
    await expect(moveCamera({ flyCameraTo: async () => undefined }, camera, 1200)).resolves.toBe(
      true,
    );
  });
});

describe('optional Google Maps dynamic loader (no real Google requests)', () => {
  it('does not insert a script before explicit load or without a key', async () => {
    const { scripts, doc } = mockDocument();
    const loader = createMapsLoader({}, doc);
    expect(scripts).toHaveLength(0);
    await expect(loader.load('')).rejects.toThrow('not configured');
    expect(scripts).toHaveLength(0);
  });

  it('shares one script across concurrent calls, loads documented libraries and rejects key changes', async () => {
    const { scripts, doc } = mockDocument();
    const host: MapsWindow = {};
    const loader = createMapsLoader(host, doc);
    const first = loader.load('public-restricted-test-key');
    const second = loader.load('public-restricted-test-key');
    await Promise.resolve();
    expect(scripts).toHaveLength(1);
    const params = new URL(scripts[0].src).searchParams;
    expect(params.get('v')).toBe('weekly');
    expect(params.get('libraries')).toBe('maps3d,marker');
    expect(params.get('callback')).toBe('google.maps.__airportRecoveryReady');
    host.google!.maps!.importLibrary = vi.fn(async (name) => (name === 'maps3d' ? maps3d : marker));
    host.google!.maps!.__airportRecoveryReady!();
    expect(await first).toEqual({ maps3d, marker });
    expect(await second).toEqual({ maps3d, marker });
    await expect(loader.load('another-key')).rejects.toThrow('different Google Maps key');
  });

  it('reports script and auth failures without substituting mock maps', async () => {
    const { scripts, doc } = mockDocument();
    const host: MapsWindow = {};
    const loader = createMapsLoader(host, doc);
    const result = loader.load('test-key');
    const failed = expect(result).rejects.toThrow('could not load');
    await Promise.resolve();
    scripts[0].onerror!();
    await failed;
    const listener = vi.fn();
    loader.onAuthenticationFailure(listener);
    host.gm_authFailure!();
    expect(listener).toHaveBeenCalledOnce();
    await expect(loader.load('test-key')).rejects.toThrow('rejected');
  });
});

const world = {
  sim_time: 700,
  airports: [
    {
      id: 'CRA',
      code: 'CRA',
      name: 'Craiova',
      lat: 44.3181,
      lon: 23.8886,
      available: true,
      diversion_eligible: true,
    },
    {
      id: 'OTP',
      code: 'OTP',
      name: 'Otopeni',
      lat: 44.5711,
      lon: 26.085,
      available: true,
      diversion_eligible: true,
    },
    {
      id: 'LTN',
      code: 'LTN',
      name: 'Luton',
      lat: 51.8747,
      lon: -0.3683,
      available: true,
      diversion_eligible: false,
    },
  ],
  resources: [
    {
      id: 'B12',
      kind: 'bus',
      origin: 'CRA',
      destination: 'OTP',
      departure: 690,
      arrival: 930,
      available: true,
    },
    {
      id: 'F110',
      kind: 'flight',
      origin: 'CRA',
      destination: 'LTN',
      departure: 870,
      arrival: 1080,
      available: true,
    },
  ],
  groups: [{ id: 'G001', name: 'Popescu', origin: 'CRA', size: 4, status: 'confirmed' }],
  offers: [
    {
      id: 'O1',
      group_id: 'G001',
      status: 'confirmed',
      itinerary: { legs: [{ resource_id: 'B12' }] },
    },
  ],
  observations: [
    {
      id: 'aircraft-a',
      lat: 44.4,
      lon: 24,
      altitude_ft: 10000,
      callsign: 'TEST',
      observed_at: '2026-10-31T10:00:00Z',
      source: 'Flightradar24',
      mode: 'LIVE',
    },
  ],
} as unknown as World;

describe('geographic source separation', () => {
  it('uses exact live airport coordinates and never draws planned resources as GPS positions', () => {
    const live = {
      ...world,
      mode: 'live' as const,
      airports: world.airports.map((airport) => ({
        ...airport,
        available: false,
        operational_status: 'unknown' as const,
      })),
    };
    const result = buildOverlays(live, 'B12', new Map());
    const airport = result.markers.find((marker) => marker.id === live.airports[0].id)!;
    expect(airport.label).toContain('STATUS UNKNOWN');
    expect(airport.color).toBe(COLORS.muted);
    expect(airport.point).toEqual({
      lat: live.airports[0].lat,
      lng: live.airports[0].lon,
      altitude: 0,
    });
    expect(result.markers.some((marker) => marker.id === 'B12')).toBe(false);
    expect(result.routes.some((route) => route.id === 'B12')).toBe(false);
  });
  it('converts provider feet and never invents missing coordinates or altitude', () => {
    expect(observedPoint(world.observations[0])).toEqual({ lat: 44.4, lng: 24, altitude: 3048 });
    expect(observedPoint({ lat: 44, lon: 24 })).toEqual({ lat: 44, lng: 24 });
    expect(observedPoint({ lat: 44, lon: 24, altitude_ft: -10 })?.altitude).toBeCloseTo(-3.048);
    expect(observedPoint({ lat: 500, lon: 24 })).toBeNull();
    expect(observedPoint({ callsign: 'No position' })).toBeNull();
  });

  it('mutes both markers and trails for stale, replay, sandbox and unknown-time observations', () => {
    const now = Date.parse('2026-10-31T10:00:40Z');
    const tracks: ObservationTracks = new Map([
      [
        'aircraft-a',
        [
          { lat: 44.3, lng: 24, timestamp: new Date(now - 40_000).toISOString() },
          { lat: 44.4, lng: 24, timestamp: new Date(now - 10_000).toISOString() },
        ],
      ],
    ]);
    for (const [mode, observed_at] of [
      ['LIVE', '2026-10-31T09:00:00Z'],
      ['REPLAY', '2026-10-31T10:00:30Z'],
      ['SANDBOX', '2026-10-31T10:00:30Z'],
      ['LIVE', 'invalid'],
    ]) {
      const snapshot = structuredClone(world);
      snapshot.observations[0] = { ...snapshot.observations[0], mode, observed_at };
      const overlays = buildOverlays(snapshot, null, tracks, now);
      expect(overlays.markers.find((item) => item.id === 'aircraft-a')?.color).toBe(COLORS.muted);
      expect(overlays.routes.find((item) => item.id === 'observed-aircraft-a')?.color).toBe(
        COLORS.muted,
      );
    }
  });

  it('makes trails from unique received timestamps, not synthetic history', () => {
    const tracks: ObservationTracks = new Map();
    updateObservationTracks(tracks, world.observations);
    updateObservationTracks(tracks, world.observations);
    expect(tracks.get('aircraft-a')).toHaveLength(1);
    updateObservationTracks(tracks, [
      { ...world.observations[0], lat: 44.5, observed_at: '2026-10-31T10:00:30Z' },
    ]);
    expect(tracks.get('aircraft-a')).toHaveLength(2);
    expect(
      buildOverlays(world, null, tracks, Date.parse('2026-10-31T10:00:40Z')).routes.find(
        (r) => r.id === 'observed-aircraft-a',
      )?.path,
    ).toHaveLength(2);
  });

  it('shows confirmed simulator capacity separately from observed, stale and sandbox data', () => {
    const snapshot = structuredClone(world);
    const fresh = buildOverlays(snapshot, 'B12', new Map(), Date.parse('2026-10-31T10:00:20Z'));
    expect(fresh.routes.find((r) => r.id === 'B12')).toMatchObject({
      color: COLORS.confirmed,
      dashed: false,
      observed: false,
    });
    expect(fresh.markers.find((m) => m.id === 'B12')?.label).toContain('SIM');
    expect(fresh.markers.find((m) => m.id === 'aircraft-a')?.label).toContain('OBSERVED');
    snapshot.resources[0].available = false;
    snapshot.airports[0].available = false;
    const failed = buildOverlays(snapshot, 'B12', new Map(), Date.parse('2026-10-31T10:10:00Z'));
    expect(failed.routes.find((r) => r.id === 'B12')?.color).toBe(COLORS.unavailable);
    expect(failed.markers.find((m) => m.id === 'CRA')?.label).toContain('CLOSED IN SIM');
    expect(failed.markers.find((m) => m.id === 'aircraft-a')?.label).toContain('STALE');
    snapshot.observations[0].mode = 'SANDBOX';
    expect(
      buildOverlays(snapshot, null, new Map()).markers.find((m) => m.id === 'aircraft-a')?.label,
    ).toContain('SANDBOX');
    expect(world.resources[0].available).toBe(true);
  });

  it('focuses selected observed aircraft and fits regional eligible airports', () => {
    expect(cameraFor(world, 'airport', 'aircraft-a').center).toEqual({
      lat: 44.4,
      lng: 24,
      altitude: 3048,
    });
    const region = cameraFor(world, 'region', null);
    expect(region.center.lat).toBeLessThan(46);
    expect(region.range).toBe(330000);
  });
});

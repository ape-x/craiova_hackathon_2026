import { describe, expect, it, vi } from 'vitest';
import { buildModelDescriptors, createModelAssetLoader, MODEL_URLS } from './models';
import { mountModelLayer } from './modelLayer';
import type { World } from '../types';
import type { GoogleMapElement, Maps3DLibrary, ModelOptions } from './contracts';

const world = {
  sim_time: 700,
  airports: [
    { id: 'CRA', lat: 44.3181, lon: 23.8886 },
    { id: 'OTP', lat: 44.5711, lon: 26.085 },
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
      destination: 'OTP',
      departure: 690,
      arrival: 740,
      available: true,
    },
  ],
  groups: [{ id: 'G001', name: 'Family', size: 4, origin: 'CRA', status: 'waiting' }],
  offers: [],
  observations: [{ id: 'a', lat: 44.4, lon: 24, altitude_ft: 10000, heading: 85, mode: 'LIVE' }],
} as unknown as World;

function binaryGLB(): ArrayBuffer {
  const bytes = new ArrayBuffer(24);
  const header = new DataView(bytes);
  header.setUint32(0, 0x46546c67, true);
  header.setUint32(4, 2, true);
  header.setUint32(8, bytes.byteLength, true);
  return bytes;
}
const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

describe('authored geographic vehicle models', () => {
  it('uses received altitude and heading, with no fabricated pose for incomplete observations', () => {
    const models = buildModelDescriptors(world, null).filter((m) => m.observed);
    expect(models).toHaveLength(1);
    expect(models[0]).toMatchObject({
      observed: true,
      options: {
        src: MODEL_URLS.aircraft,
        position: { lat: 44.4, lng: 24, altitude: 3048 },
        orientation: { heading: 85, tilt: 270, roll: 0 },
        altitudeMode: 'ABSOLUTE',
      },
    });
    const snapshot = structuredClone(world);
    snapshot.observations = [
      { ...world.observations[0], altitude_ft: undefined },
      { ...world.observations[0], id: 'b', heading: undefined },
      { ...world.observations[0], id: 'c', heading: -1 },
    ];
    expect(buildModelDescriptors(snapshot, null).filter((m) => m.observed)).toHaveLength(0);
  });

  it('caps observations at 20 and keeps the whole simulated scene without requiring selection', () => {
    const snapshot = structuredClone(world);
    snapshot.observations = Array.from({ length: 35 }, (_, index) => ({
      ...world.observations[0],
      id: `a${index}`,
    }));
    const observed = buildModelDescriptors(snapshot, 'a34').filter((m) => m.observed);
    expect(observed).toHaveLength(20);
    expect(observed[0].id).toBe('a34');
    const bus = buildModelDescriptors(snapshot, 'B12');
    expect(bus).toHaveLength(25);
    expect(bus.find((m) => m.id === 'B12')!).toMatchObject({
      id: 'B12',
      observed: false,
      options: { src: MODEL_URLS.bus, altitudeMode: 'CLAMP_TO_GROUND' },
    });
    expect(bus.find((m) => m.id === 'B12')!.title).toContain('SIMULATED');
    expect(bus.find((m) => m.id === 'B12')!.options.orientation.heading).toBeGreaterThan(0);
    expect(bus.find((m) => m.id === 'B12')!.options.orientation.heading).toBeLessThan(180);
    expect(buildModelDescriptors(snapshot, 'F110').find((m) => m.id === 'F110')).toMatchObject({
      observed: false,
      options: { src: MODEL_URLS.aircraft, altitudeMode: 'RELATIVE_TO_GROUND' },
    });
  });

  it('fetches only on demand, shares a binary preflight and rejects missing/corrupt assets', async () => {
    const fetcher = vi.fn(async () => new Response(binaryGLB(), { status: 200 }));
    const load = createModelAssetLoader(fetcher);
    expect(fetcher).not.toHaveBeenCalled();
    await Promise.all([load(MODEL_URLS.aircraft), load(MODEL_URLS.aircraft)]);
    expect(fetcher).toHaveBeenCalledOnce();
    await expect(load('https://untrusted.example/model.glb')).rejects.toThrow('Unrecognized');
    await expect(
      createModelAssetLoader(vi.fn(async () => new Response('missing', { status: 404 })))(
        MODEL_URLS.bus,
      ),
    ).rejects.toThrow('HTTP 404');
    await expect(
      createModelAssetLoader(
        vi.fn(async () => new Response('<html>Vite SPA index fallback</html>')),
      )(MODEL_URLS.bus),
    ).rejects.toThrow('valid GLB');
  });

  it('removes every model on disposal and never mounts a model after asynchronous disposal', async () => {
    const elements: Array<{
      options: ModelOptions;
      remove: ReturnType<typeof vi.fn>;
      setAttribute: ReturnType<typeof vi.fn>;
    }> = [];
    class Model {
      remove = vi.fn();
      setAttribute = vi.fn();
      constructor(public options: ModelOptions) {
        elements.push(this);
      }
    }
    const library = { Model3DElement: Model as unknown as Maps3DLibrary['Model3DElement'] };
    const map = { append: vi.fn() } as unknown as Pick<GoogleMapElement, 'append'>;
    const onError = vi.fn();
    let resolveAsset!: () => void;
    const pending = new Promise<void>((resolve) => {
      resolveAsset = resolve;
    });
    const beforeReady = mountModelLayer(
      map,
      library,
      buildModelDescriptors(world, 'B12'),
      onError,
      () => pending,
    );
    beforeReady();
    resolveAsset();
    await flush();
    expect(elements).toHaveLength(0);
    const active = mountModelLayer(
      map,
      library,
      buildModelDescriptors(world, 'B12'),
      onError,
      async () => undefined,
    );
    await flush();
    expect(elements).toHaveLength(6);
    expect(map.append).toHaveBeenCalledTimes(6);
    expect(elements[0].setAttribute).toHaveBeenCalledWith('data-source', 'observed');
    expect(elements[1].setAttribute).toHaveBeenCalledWith('data-source', 'simulation');
    active();
    active();
    expect(elements.every((item) => item.remove.mock.calls.length === 1)).toBe(true);
    expect(onError).not.toHaveBeenCalled();
  });

  it('surfaces model API/asset errors and never creates fallback geometry', async () => {
    const append = vi.fn();
    const onError = vi.fn();
    const load = vi.fn(async () => {
      throw new Error('Asset missing');
    });
    const constructor = vi.fn();
    mountModelLayer(
      { append },
      { Model3DElement: constructor as unknown as Maps3DLibrary['Model3DElement'] },
      buildModelDescriptors(world, null),
      onError,
      load,
    );
    await flush();
    expect(onError).toHaveBeenCalledOnce();
    expect(constructor).not.toHaveBeenCalled();
    expect(append).not.toHaveBeenCalled();
    mountModelLayer({ append }, {} as Pick<Maps3DLibrary, 'Model3DElement'>, [], onError, load);
    expect(onError).toHaveBeenLastCalledWith(expect.stringContaining('does not expose 3D models'));
  });
});

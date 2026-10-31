import { describe, expect, it, vi } from 'vitest';
import type { Maps3DLibrary, ModelOptions } from './contracts';
import type { GeoModel } from './models';
import { createModelLayer } from './modelLayer';

function descriptor(id = 'B12', options: Partial<ModelOptions> = {}): GeoModel {
  return {
    id,
    title: `${id} · SIMULATED`,
    observed: false,
    options: {
      src: '/scene/models/bus.glb',
      position: { lat: 44.3, lng: 23.8, altitude: 1 },
      orientation: { heading: 20, tilt: 0, roll: 0 },
      scale: 4,
      altitudeMode: 'RELATIVE_TO_GROUND',
      ...options,
    },
  };
}
function deferred() {
  let resolve!: () => void;
  let reject!: (cause: Error) => void;
  const promise = new Promise<void>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

function fixture(interactive = false) {
  const elements: Model[] = [];
  class Model {
    readonly src: string;
    position: ModelOptions['position'];
    orientation: ModelOptions['orientation'];
    scale: number;
    altitudeMode: ModelOptions['altitudeMode'];
    listeners = new Map<string, (event: Event) => void>();
    attributes = new Map<string, string>();
    setAttribute = vi.fn((name: string, value: string) => this.attributes.set(name, value));
    remove = vi.fn();
    addEventListener = vi.fn((name: string, callback: (event: Event) => void) =>
      this.listeners.set(name, callback),
    );
    removeEventListener = vi.fn((name: string) => this.listeners.delete(name));
    constructor(options: ModelOptions) {
      this.src = options.src;
      this.position = options.position;
      this.orientation = options.orientation;
      this.scale = options.scale;
      this.altitudeMode = options.altitudeMode;
      elements.push(this);
    }
  }
  const library: Pick<Maps3DLibrary, 'Model3DElement' | 'Model3DInteractiveElement'> = {
    Model3DElement: Model as unknown as Maps3DLibrary['Model3DElement'],
  };
  if (interactive)
    library.Model3DInteractiveElement = Model as unknown as Maps3DLibrary['Model3DElement'];
  const map = { append: vi.fn() };
  const onError = vi.fn();
  const onSelect = vi.fn();
  const load = vi.fn<(src: string) => Promise<void>>(async () => undefined);
  const layer = createModelLayer(map, library, onError, load, onSelect);
  return { layer, map, elements, library, load, onError, onSelect };
}

describe('keyed Google model lifecycle', () => {
  it('keeps identical elements and asset checks across live pose, scale and selection metadata updates', async () => {
    const f = fixture();
    f.layer.update([descriptor(), descriptor('B13')]);
    await flush();
    expect(f.load).toHaveBeenCalledOnce();
    expect(f.elements).toHaveLength(2);
    const [bus, other] = f.elements;
    const next = descriptor('B12', {
      position: { lat: 44.4, lng: 23.9, altitude: 3 },
      orientation: { heading: 85, tilt: 2, roll: 1 },
      scale: 5,
      altitudeMode: 'ABSOLUTE',
    });
    next.title = 'Selected simulated bus';
    f.layer.update([descriptor('B13'), next]);
    f.layer.update([descriptor('B13'), structuredClone(next)]);
    await flush();
    expect(f.elements).toHaveLength(2);
    expect(f.map.append).toHaveBeenCalledTimes(2);
    expect(f.load).toHaveBeenCalledOnce();
    expect(bus).toMatchObject(next.options);
    expect(bus.attributes.get('aria-label')).toBe('Selected simulated bus');
    expect(bus.attributes.get('data-model-id')).toBe('B12');
    expect(bus.attributes.get('data-source')).toBe('simulation');
    expect(bus.remove).not.toHaveBeenCalled();
    expect(other.remove).not.toHaveBeenCalled();
    expect(f.onError).not.toHaveBeenCalled();
  });

  it('uses the newest pose when an asset finishes, and ignores stale sources and removed entries', async () => {
    const f = fixture();
    const bus = deferred(),
      aircraft = deferred();
    f.load.mockImplementation((src) => (src.endsWith('bus.glb') ? bus.promise : aircraft.promise));
    f.layer.update([descriptor()]);
    f.layer.update([descriptor('B12', { src: '/scene/models/aircraft.glb' })]);
    f.layer.update([
      descriptor('B12', {
        src: '/scene/models/aircraft.glb',
        position: { lat: 46, lng: 26, altitude: 3000 },
      }),
    ]);
    bus.resolve();
    await flush();
    expect(f.elements).toHaveLength(0);
    aircraft.resolve();
    await flush();
    expect(f.elements).toHaveLength(1);
    expect(f.elements[0].position).toEqual({ lat: 46, lng: 26, altitude: 3000 });
    expect(f.load).toHaveBeenCalledTimes(2);
    f.layer.update([]);
    expect(f.elements[0].remove).toHaveBeenCalledOnce();
    f.layer.update([descriptor('B13')]);
    f.layer.update([]);
    await flush();
    expect(f.elements).toHaveLength(1);
  });

  it('separates observed and simulated IDs, removes only absent objects, and disposes once', async () => {
    const f = fixture();
    f.layer.update([descriptor(), { ...descriptor(), observed: true }]);
    await flush();
    expect(f.elements).toHaveLength(2);
    f.layer.update([{ ...descriptor(), observed: true }]);
    expect(f.elements[0].remove).toHaveBeenCalledOnce();
    expect(f.elements[1].remove).not.toHaveBeenCalled();
    expect(f.elements[1].attributes.get('data-source')).toBe('observed');
    f.layer.dispose();
    f.layer.dispose();
    f.layer.update([descriptor('B14')]);
    await flush();
    expect(f.elements.every((element) => element.remove.mock.calls.length === 1)).toBe(true);
    expect(f.map.append).toHaveBeenCalledTimes(2);
  });

  it('replaces only a changed asset and does not reset unchanged Google pose setters', async () => {
    const f = fixture();
    f.layer.update([descriptor(), descriptor('B13')]);
    await flush();
    const [bus, unchanged] = f.elements;
    const position = unchanged.position;
    const setter = vi.fn();
    Object.defineProperty(unchanged, 'position', { get: () => position, set: setter });
    f.layer.update([descriptor('B12', { src: '/scene/models/aircraft.glb' }), descriptor('B13')]);
    await flush();
    expect(f.elements).toHaveLength(3);
    expect(bus.remove).toHaveBeenCalledOnce();
    expect(unchanged.remove).not.toHaveBeenCalled();
    expect(setter).not.toHaveBeenCalled();
    expect(f.elements[2].src).toBe('/scene/models/aircraft.glb');
    expect(f.load).toHaveBeenCalledTimes(2);
  });

  it('never appends late models or reports late errors after disposal', async () => {
    const f = fixture();
    const pending = deferred();
    f.load.mockReturnValue(pending.promise);
    f.layer.update([descriptor()]);
    f.layer.dispose();
    pending.resolve();
    await flush();
    expect(f.map.append).not.toHaveBeenCalled();
    expect(f.onError).not.toHaveBeenCalled();
    const second = fixture();
    const failing = deferred();
    second.load.mockReturnValue(failing.promise);
    second.layer.update([descriptor()]);
    second.layer.dispose();
    failing.reject(new Error('private upstream URL'));
    await flush();
    expect(second.onError).not.toHaveBeenCalled();
  });

  it('keeps sanitized errors visible without retrying on each tick, and clears removed failures', async () => {
    const f = fixture();
    f.load.mockRejectedValue(new Error('private upstream URL with credentials'));
    f.layer.update([descriptor(), descriptor('B13')]);
    await flush();
    f.layer.update([descriptor(), descriptor('B13')]);
    await flush();
    expect(f.load).toHaveBeenCalledOnce();
    expect(f.elements).toHaveLength(0);
    expect(f.onError).toHaveBeenCalledOnce();
    expect(f.onError.mock.calls[0][0]).toContain('could not load');
    expect(f.onError.mock.calls[0][0]).not.toContain('private');
    f.layer.update([]);
    expect(f.onError).toHaveBeenLastCalledWith('');
  });

  it('cleans up a partially appended mesh and a rejected pose update', async () => {
    const f = fixture(true);
    f.map.append.mockImplementationOnce(() => {
      throw new Error('private provider payload');
    });
    f.layer.update([descriptor()]);
    await flush();
    expect(f.elements[0].remove).toHaveBeenCalledOnce();
    expect(f.elements[0].removeEventListener).toHaveBeenCalledOnce();
    expect(f.onError).toHaveBeenLastCalledWith(
      expect.stringContaining('could not place or update'),
    );
    f.layer.update([descriptor('B13')]);
    await flush();
    const active = f.elements[1];
    Object.defineProperty(active, 'position', {
      set() {
        throw new Error('private provider payload');
      },
    });
    f.layer.update([descriptor('B13', { position: { lat: 45, lng: 25 } })]);
    expect(active.remove).toHaveBeenCalledOnce();
    expect(active.removeEventListener).toHaveBeenCalledOnce();
    expect(JSON.stringify(f.onError.mock.calls)).not.toContain('private');
  });

  it('selects through the documented interactive API and unregisters its listener', async () => {
    const f = fixture(true);
    f.layer.update([descriptor('B12 / untrusted')]);
    await flush();
    const element = f.elements[0];
    expect(element.attributes.get('data-model-id')).toBe('B12___untrusted');
    const event = { stopPropagation: vi.fn() } as unknown as Event;
    element.listeners.get('gmp-click')!(event);
    expect(event.stopPropagation).toHaveBeenCalledOnce();
    expect(f.onSelect).toHaveBeenCalledWith('B12 / untrusted');
    f.layer.dispose();
    expect(element.listeners.has('gmp-click')).toBe(false);
    expect(element.removeEventListener).toHaveBeenCalledOnce();
    const basic = fixture();
    basic.layer.update([descriptor()]);
    await flush();
    expect(basic.elements[0].addEventListener).not.toHaveBeenCalled();
  });

  it('reports missing constructors without attempting any asset loads', () => {
    const onError = vi.fn(),
      load = vi.fn();
    const layer = createModelLayer(
      { append: vi.fn() },
      {} as Pick<Maps3DLibrary, 'Model3DElement'>,
      onError,
      load,
    );
    layer.update([descriptor()]);
    layer.dispose();
    expect(onError).toHaveBeenCalledWith(expect.stringContaining('does not expose 3D models'));
    expect(load).not.toHaveBeenCalled();
  });
});

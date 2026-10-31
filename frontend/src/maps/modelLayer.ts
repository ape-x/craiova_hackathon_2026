import type {
  GoogleMapElement,
  GoogleModelElement,
  Maps3DLibrary,
  ModelOptions,
} from './contracts';
import type { GeoModel } from './models';
import { ensureModelAsset } from './models';

type ModelLibrary = Pick<Maps3DLibrary, 'Model3DElement' | 'Model3DInteractiveElement'>;
export interface ModelLayer {
  update(descriptors: GeoModel[]): void;
  dispose(): void;
}
interface Entry {
  descriptor: GeoModel;
  element?: GoogleModelElement;
  applied?: ModelOptions;
  removeListener?: () => void;
  loadingSrc?: string;
  failedSrc?: string;
  error?: string;
  generation: number;
}
const unavailable =
  'This Google Maps version does not expose 3D models. Source markers and routes remain available.';
const assetError =
  'An authored 3D model could not load. Source markers and routes remain available; no replacement model was invented.';
const runtimeError =
  'Google Maps could not place or update an authored 3D model. Source markers and routes remain available.';

function snapshot(descriptor: GeoModel): GeoModel {
  return {
    ...descriptor,
    options: {
      ...descriptor.options,
      position: { ...descriptor.options.position },
      orientation: { ...descriptor.options.orientation },
    },
  };
}

/** One keyed layer per ready map. Live poses update documented properties
 * without detaching models or restarting GLB loads.
 * https://developers.google.com/maps/documentation/javascript/reference/3d-map-draw
 * There is no documented per-model GPU-ready event. Asset checks do not prove
 * Google rendered the mesh. Interactive clicks are capability checked.
 */
export function createModelLayer(
  map: Pick<GoogleMapElement, 'append'>,
  library: ModelLibrary,
  onError: (message: string) => void,
  loadAsset: (src: string) => Promise<void> = ensureModelAsset,
  onSelect?: (id: string) => void,
): ModelLayer {
  const interactive = typeof library.Model3DInteractiveElement === 'function';
  const Constructor = interactive ? library.Model3DInteractiveElement! : library.Model3DElement;
  if (typeof Constructor !== 'function') {
    onError(unavailable);
    return { update: () => undefined, dispose: () => undefined };
  }
  let disposed = false;
  let publishedError = '';
  const entries = new Map<string, Entry>();
  const assets = new Map<string, Promise<void>>();

  function publishError() {
    const next = [...entries.values()].find((entry) => entry.error)?.error ?? '';
    if (!disposed && next !== publishedError) {
      publishedError = next;
      onError(next);
    }
  }

  function removeElement(entry: Entry) {
    entry.removeListener?.();
    entry.removeListener = undefined;
    entry.element?.remove();
    entry.element = undefined;
    entry.applied = undefined;
  }

  function applyMetadata(entry: Entry) {
    const element = entry.element!;
    element.setAttribute('aria-label', entry.descriptor.title);
    element.setAttribute('data-source', entry.descriptor.observed ? 'observed' : 'simulation');
    element.setAttribute(
      'data-model-id',
      entry.descriptor.id.replace(/[^a-zA-Z0-9_.:-]/g, '_').slice(0, 120),
    );
  }

  function updateElement(entry: Entry) {
    const element = entry.element!;
    const next = entry.descriptor.options;
    const previous = entry.applied!;
    if (next.altitudeMode !== previous.altitudeMode) element.altitudeMode = next.altitudeMode;
    if (next.scale !== previous.scale) element.scale = next.scale;
    if (
      next.position.lat !== previous.position.lat ||
      next.position.lng !== previous.position.lng ||
      next.position.altitude !== previous.position.altitude
    )
      element.position = { ...next.position };
    if (
      next.orientation.heading !== previous.orientation.heading ||
      next.orientation.tilt !== previous.orientation.tilt ||
      next.orientation.roll !== previous.orientation.roll
    )
      element.orientation = { ...next.orientation };
    applyMetadata(entry);
    entry.applied = next;
  }

  function startLoading(key: string, entry: Entry) {
    const src = entry.descriptor.options.src;
    if (entry.loadingSrc === src || entry.failedSrc === src) return;
    // A changed source is a different asset. Remove only its obsolete shape
    // while checking the replacement; ordinary pose updates retain the mesh.
    removeElement(entry);
    entry.loadingSrc = src;
    entry.failedSrc = undefined;
    entry.error = undefined;
    const generation = ++entry.generation;
    const active = () => !disposed && entries.get(key) === entry && entry.generation === generation;
    let pending = assets.get(src);
    if (!pending) {
      try {
        pending = Promise.resolve(loadAsset(src));
      } catch {
        pending = Promise.reject(new Error('Authored model validation failed.'));
      }
      assets.set(src, pending);
    }
    void pending
      .then(() => {
        if (!active()) return;
        entry.loadingSrc = undefined;
        try {
          // The latest pose wins even if it changed while the asset was pending.
          entry.element = new Constructor(entry.descriptor.options);
          applyMetadata(entry);
          if (interactive && onSelect) {
            const listener = (event: Event) => {
              event.stopPropagation();
              onSelect(entry.descriptor.id);
            };
            entry.element.addEventListener('gmp-click', listener);
            const element = entry.element;
            entry.removeListener = () => element.removeEventListener('gmp-click', listener);
          }
          map.append(entry.element);
          entry.applied = entry.descriptor.options;
        } catch {
          removeElement(entry);
          entry.failedSrc = src;
          entry.error = runtimeError;
        }
        publishError();
      })
      .catch(() => {
        if (!active()) return;
        entry.loadingSrc = undefined;
        entry.failedSrc = src;
        entry.error = assetError;
        publishError();
      });
  }

  return {
    update(descriptors) {
      if (disposed) return;
      const desired = new Map(
        descriptors.map((descriptor) => [
          `${descriptor.observed ? 'observed' : 'simulation'}:${descriptor.id}`,
          snapshot(descriptor),
        ]),
      );
      for (const [key, entry] of entries) {
        if (!desired.has(key)) {
          removeElement(entry);
          entries.delete(key);
        }
      }
      for (const [key, descriptor] of desired) {
        let entry = entries.get(key);
        if (!entry) {
          entry = { descriptor, generation: 0 };
          entries.set(key, entry);
        } else entry.descriptor = descriptor;
        if (entry.element && entry.applied?.src === descriptor.options.src) {
          try {
            updateElement(entry);
          } catch {
            removeElement(entry);
            entry.failedSrc = descriptor.options.src;
            entry.error = runtimeError;
          }
        } else startLoading(key, entry);
      }
      publishError();
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      for (const entry of entries.values()) removeElement(entry);
      entries.clear();
      assets.clear();
    },
  };
}

/** Compatibility for a caller that only needs one static layer. */
export function mountModelLayer(
  map: Pick<GoogleMapElement, 'append'>,
  library: ModelLibrary,
  descriptors: GeoModel[],
  onError: (message: string) => void,
  loadAsset: (src: string) => Promise<void> = ensureModelAsset,
): () => void {
  const layer = createModelLayer(map, library, onError, loadAsset);
  layer.update(descriptors);
  return layer.dispose;
}

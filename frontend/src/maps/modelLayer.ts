import type { GoogleMapElement, Maps3DLibrary } from './contracts';
import type { GeoModel } from './models';
import { ensureModelAsset } from './models';

/** Own every created DOM model and guard asynchronous asset completion on dispose. */
export function mountModelLayer(
  map: Pick<GoogleMapElement, 'append'>,
  library: Pick<Maps3DLibrary, 'Model3DElement'>,
  descriptors: GeoModel[],
  onError: (message: string) => void,
  loadAsset: (src: string) => Promise<void> = ensureModelAsset,
): () => void {
  let disposed = false;
  const elements = new Set<HTMLElement>();
  if (typeof library.Model3DElement !== 'function') {
    onError(
      'This Google Maps version does not expose 3D models. Source markers and routes remain available.',
    );
    return () => undefined;
  }
  for (const descriptor of descriptors) {
    void loadAsset(descriptor.options.src)
      .then(() => {
        if (disposed) return;
        const model = new library.Model3DElement(descriptor.options);
        elements.add(model);
        model.setAttribute('aria-label', descriptor.title);
        model.setAttribute('data-source', descriptor.observed ? 'observed' : 'simulation');
        map.append(model);
      })
      .catch(() => {
        if (!disposed)
          onError(
            'An authored 3D vehicle model could not load. Source markers and routes remain available; no replacement model was invented.',
          );
      });
  }
  return () => {
    disposed = true;
    for (const element of elements) element.remove();
    elements.clear();
  };
}

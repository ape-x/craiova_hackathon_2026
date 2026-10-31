import type { LoadedMaps, Maps3DLibrary, MarkerLibrary } from './contracts';

type ImportLibrary = (name: string) => Promise<unknown>;
export interface MapsWindow {
  google?: { maps?: { importLibrary?: ImportLibrary; __airportRecoveryReady?: () => void } };
  gm_authFailure?: () => void;
}

/** Based on Google's documented dynamic import bootstrap. No deprecated Loader. */
export function createMapsLoader(host: MapsWindow, doc: Document) {
  let chosenKey: string | undefined;
  let libraryPromise: Promise<LoadedMaps> | undefined;
  let authenticationFailed = false;
  const listeners = new Set<() => void>();
  const previousFailure = host.gm_authFailure;
  host.gm_authFailure = () => {
    authenticationFailed = true;
    for (const listener of listeners) listener();
    previousFailure?.();
  };

  function installBootstrap(key: string) {
    host.google ??= {};
    const maps = (host.google.maps ??= {});
    if (maps.importLibrary) return;
    let bootstrap: Promise<void> | undefined;
    const requested = new Set<string>();
    const importLibrary: ImportLibrary = (name) => {
      requested.add(name);
      bootstrap ??= new Promise<void>((resolve, reject) => {
        // Queue one microtask so parallel maps3d + marker requests share a load.
        queueMicrotask(() => {
          const script = doc.createElement('script');
          const params = new URLSearchParams({
            key,
            v: 'weekly',
            loading: 'async',
            libraries: [...requested].join(','),
            callback: 'google.maps.__airportRecoveryReady',
            language: 'en',
            region: 'RO',
          });
          const timer = setTimeout(
            () =>
              reject(
                new Error(
                  'Google Maps loading timed out. Check the connection and allowed browser origin.',
                ),
              ),
            20000,
          );
          maps.__airportRecoveryReady = () => {
            clearTimeout(timer);
            resolve();
          };
          script.async = true;
          script.nonce = doc.querySelector<HTMLScriptElement>('script[nonce]')?.nonce ?? '';
          script.src = `https://maps.googleapis.com/maps/api/js?${params}`;
          script.onerror = () => {
            clearTimeout(timer);
            reject(
              new Error('Google Maps could not load. Check network and content security settings.'),
            );
          };
          doc.head.append(script);
        });
      });
      return bootstrap.then(() => {
        if (!maps.importLibrary || maps.importLibrary === importLibrary)
          throw new Error('Google Maps did not initialize its library loader.');
        return maps.importLibrary(name);
      });
    };
    maps.importLibrary = importLibrary;
  }

  return {
    onAuthenticationFailure(listener: () => void) {
      listeners.add(listener);
      if (authenticationFailed) listener();
      return () => {
        listeners.delete(listener);
      };
    },
    load(key: string): Promise<LoadedMaps> {
      if (!key.trim())
        return Promise.reject(
          new Error('Google Maps is not configured. Add the restricted browser key on the server.'),
        );
      if (authenticationFailed)
        return Promise.reject(
          new Error(
            'Google Maps rejected this key or browser origin. Check API enablement, billing and key restrictions.',
          ),
        );
      if (chosenKey && chosenKey !== key)
        return Promise.reject(
          new Error(
            'A different Google Maps key is already loaded. Reload the page after changing configuration.',
          ),
        );
      chosenKey = key;
      if (!libraryPromise) {
        installBootstrap(key);
        const importer = host.google!.maps!.importLibrary!;
        libraryPromise = Promise.all([importer('maps3d'), importer('marker')]).then(
          ([maps3d, marker]) => {
            const mapLibrary = maps3d as Maps3DLibrary,
              markerLibrary = marker as MarkerLibrary;
            if (
              !mapLibrary.Map3DElement ||
              !mapLibrary.Marker3DInteractiveElement ||
              !mapLibrary.Polyline3DElement ||
              !markerLibrary.PinElement
            ) {
              throw new Error(
                'The loaded Google Maps version does not expose the required 3D API.',
              );
            }
            return { maps3d: mapLibrary, marker: markerLibrary };
          },
        );
      }
      return libraryPromise;
    },
  };
}

let browserLoader: ReturnType<typeof createMapsLoader> | undefined;
export function getMapsLoader() {
  browserLoader ??= createMapsLoader(window as unknown as MapsWindow, document);
  return browserLoader;
}

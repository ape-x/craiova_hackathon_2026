export type MapLoadingState = 'loading' | 'rendering' | 'ready' | 'error';
export interface MapInitializationState {
  state: MapLoadingState;
  error: string;
}
type Failure = 'authentication' | 'initialization' | 'load' | 'timeout' | 'configuration';

const CHECK_PROVIDER =
  'Check the browser console for the exact Google provider error code. Verify project billing, Maps JavaScript API enablement and browser-key restrictions; then reload after correcting configuration.';
const FAILURES: Record<Failure, string> = {
  authentication: `Google Maps reported an authentication or project-access failure. ${CHECK_PROVIDER}`,
  initialization: `Google Maps could not initialize the 3D view. ${CHECK_PROVIDER} Also check WebGL support and network connectivity.`,
  load: `Google Maps JavaScript could not load. ${CHECK_PROVIDER} Also check network connectivity and content-security settings.`,
  timeout: `Google Maps has not confirmed a rendered 3D scene. ${CHECK_PROVIDER} Local coverage remains unverified.`,
  configuration:
    'Google Maps is not configured. Add the restricted browser key on the server, then reload the page.',
};

/** One mounted map owns this lifecycle. A fatal error is terminal until remount.
 * Google documents no reason payload on gm_authFailure or gmp-error; messages
 * come only from these fixed local categories, never exception strings/URLs.
 */
export function createMapInitialization(onChange: (value: MapInitializationState) => void) {
  let current: MapInitializationState = { state: 'loading', error: '' };
  let disposed = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let detach: (() => void) | undefined;
  const active = () => !disposed && current.state !== 'error';
  const update = (value: MapInitializationState) => {
    current = value;
    onChange(value);
  };
  const fail = (kind: Failure) => {
    if (!active()) return;
    clearTimeout(timer);
    update({ state: 'error', error: FAILURES[kind] });
  };
  return {
    get state() {
      return current;
    },
    get active() {
      return active();
    },
    get ready() {
      return active() && current.state === 'ready';
    },
    fail,
    watch(map: Pick<EventTarget, 'addEventListener' | 'removeEventListener'>) {
      if (!active() || current.state !== 'loading') return;
      const failed = () => fail('initialization');
      const steady = (event: Event) => {
        if (
          !active() ||
          current.state !== 'rendering' ||
          !(event as Event & { isSteady?: boolean }).isSteady
        )
          return;
        clearTimeout(timer);
        update({ state: 'ready', error: '' });
      };
      map.addEventListener('gmp-error', failed);
      map.addEventListener('gmp-steadychange', steady);
      detach = () => {
        map.removeEventListener('gmp-error', failed);
        map.removeEventListener('gmp-steadychange', steady);
      };
      update({ state: 'rendering', error: '' });
      // Arm before the caller appends the map: append can synchronously emit
      // readiness, which must clear this timer instead of being overwritten.
      timer = setTimeout(() => {
        if (current.state === 'rendering') fail('timeout');
      }, 30000);
    },
    runIfReady<T>(operation: () => T): T | undefined {
      if (active() && current.state === 'ready') return operation();
      return undefined;
    },
    dispose() {
      disposed = true;
      clearTimeout(timer);
      detach?.();
    },
  };
}

export type MapInitialization = ReturnType<typeof createMapInitialization>;
export type OverlayStage =
  | 'derive overlays'
  | 'create route'
  | 'attach route'
  | 'create marker'
  | 'create marker pin'
  | 'attach marker';
export function overlayFailureMessage(stage: OverlayStage, cause: unknown): string {
  // Even exception.name may contain arbitrary provider text, so use known
  // local classifications and never include the message, stack or URL.
  const name =
    cause instanceof TypeError ? 'TypeError' : cause instanceof RangeError ? 'RangeError' : 'Error';
  return `Simulation overlays could not be drawn (${stage}; ${name}). Google basemap status is reported separately. Return to simulation view if needed.`;
}
